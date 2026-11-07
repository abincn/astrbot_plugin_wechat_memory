"""Durable per-call reservations; task-local identity prevents double settlement."""
from contextvars import ContextVar
import time
import uuid

_active = ContextVar('wechat_memory_reservation', default=None)


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS model_calls (
      id TEXT PRIMARY KEY, day TEXT NOT NULL, platform TEXT, owner TEXT, room TEXT,
      purpose TEXT, reserved_input INTEGER, reserved_output INTEGER,
      actual_input INTEGER, actual_output INTEGER, estimated INTEGER DEFAULT 1,
      status TEXT NOT NULL, error INTEGER DEFAULT 0, created_at INTEGER, finished_at INTEGER)''')


def recover(store):
    """One process per database. Never silently refund an interrupted provider request."""
    with store.transaction():
        rows=store.db.execute("SELECT * FROM model_calls WHERE status='reserved'").fetchall()
        for r in rows:
            inp=r['actual_input'] if r['actual_input'] is not None else r['reserved_input']
            out=r['actual_output'] if r['actual_output'] is not None else r['reserved_output']
            store.db.execute('''UPDATE memory_usage_daily SET
              reserved_input=MAX(0,reserved_input-?),input_tokens=input_tokens+?,
              output_tokens=output_tokens+?,estimated=1,errors=errors+1
              WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?''',
              (r['reserved_input']+r['reserved_output'],inp,out,r['day'],r['platform'],r['owner'],r['room'],r['purpose']))
            store.db.execute("UPDATE model_calls SET status='needs_reconciliation',error=1,finished_at=? WHERE id=?",
                             (int(time.time()),r['id']))


def reserve(store,s,estimated,*,purpose,limit_calls,limit_tokens,day=None,
            max_usd=0,input_price=0,output_price=0,expected_output=0):
    from .storage import day_key, now
    day=day or day_key()
    estimated=max(0,int(estimated));expected_output=max(0,int(expected_output))
    ident=uuid.uuid4().hex
    with store.transaction():
        store.db.execute('INSERT OR IGNORE INTO memory_usage_daily(day,platform,owner,room,purpose) VALUES(?,?,?,?,?)',
                         (day,*store._key(s),purpose))
        r=store.db.execute('SELECT * FROM memory_usage_daily WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?',
                           (day,*store._key(s),purpose)).fetchone()
        reserved=store.db.execute("SELECT COALESCE(SUM(reserved_input),0),COALESCE(SUM(reserved_output),0) FROM model_calls WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=? AND status='reserved'",
                                 (day,*store._key(s),purpose)).fetchone()
        if r['calls']>=limit_calls or r['input_tokens']+r['output_tokens']+r['reserved_input']+estimated+expected_output>limit_tokens:
            return False
        if max_usd>0 and input_price>0 and output_price>0:
            cost=((r['input_tokens']+reserved[0]+estimated)*input_price+
                  (r['output_tokens']+reserved[1]+expected_output)*output_price)/1_000_000
            if cost>max_usd:return False
        store.db.execute('INSERT INTO model_calls(id,day,platform,owner,room,purpose,reserved_input,reserved_output,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (ident,day,*store._key(s),purpose,estimated,expected_output,'reserved',now()))
        store.db.execute('UPDATE memory_usage_daily SET calls=calls+1,reserved_input=reserved_input+? WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?',
                         (estimated+expected_output,day,*store._key(s),purpose))
    active=dict(_active.get() or {})
    active[(id(store),*store._key(s),purpose)]=ident
    _active.set(active)
    return True


def observe(store,inp,out,estimated,purpose='dream',scope=None):
    active=_active.get()
    matches=[ident for key,ident in (active or {}).items() if key[0]==id(store) and key[-1]==purpose and (scope is None or key[1:4]==store._key(scope))]
    if len(matches)!=1:return
    ident=matches[0]
    with store.transaction():
        store.db.execute("UPDATE model_calls SET actual_input=?,actual_output=?,estimated=? WHERE id=? AND status='reserved'",
                         (max(0,int(inp)),max(0,int(out)),int(estimated),ident))


def finish(store,s,estimated,actual_in,actual_out,*,purpose,estimated_usage,error,day=None):
    active=_active.get()
    ident=(active or {}).get((id(store),*store._key(s),purpose))
    if not ident:
        raise RuntimeError('No task-local model reservation')
    with store.transaction():
        r=store.db.execute('SELECT * FROM model_calls WHERE id=?',(ident,)).fetchone()
        if not r or r['status']!='reserved':return
        inp=r['actual_input'] if r['actual_input'] is not None else max(0,actual_in)
        out=r['actual_output'] if r['actual_output'] is not None else max(r['reserved_output'] if error else 0,actual_out)
        estimated_usage=bool(r['estimated']) if r['actual_input'] is not None else estimated_usage
        store.db.execute('''UPDATE memory_usage_daily SET reserved_input=MAX(0,reserved_input-?),
          input_tokens=input_tokens+?,output_tokens=output_tokens+?,estimated=MAX(estimated,?),errors=errors+?
          WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?''',
          (r['reserved_input']+r['reserved_output'],inp,out,int(estimated_usage),int(error),r['day'],*store._key(s),purpose))
        store.db.execute("UPDATE model_calls SET actual_input=?,actual_output=?,estimated=?,status=?,error=?,finished_at=? WHERE id=?",
                         (inp,out,int(estimated_usage),'needs_reconciliation' if error and r['actual_input'] is None else 'settled',int(error),int(time.time()),r['id']))


def status(store,s,cfg):
    import datetime as dt
    from zoneinfo import ZoneInfo
    from .storage import day_key
    day=day_key()
    with store._lock:
        row=store.db.execute("SELECT * FROM memory_usage_daily WHERE day=? AND platform=? AND owner=? AND room=? AND purpose='dream'",(day,*store._key(s))).fetchone()
        usage=dict(row) if row else {'calls':0,'input_tokens':0,'output_tokens':0,'reserved_input':0}
    reset=dt.datetime.combine(dt.date.fromisoformat(day)+dt.timedelta(days=1),dt.time.min,dt.timezone.utc)
    try:zone=ZoneInfo(str(cfg.get('dream_timezone','Asia/Shanghai')))
    except (ValueError,KeyError):zone=dt.timezone.utc
    return {'day':day,'usage':usage,'remaining_calls':max(0,int(cfg.get('max_calls_per_group_day',2))-usage['calls']),
            'remaining_tokens':max(0,int(cfg.get('max_tokens_per_group_day',40000))-usage['input_tokens']-usage['output_tokens']-usage['reserved_input']),
            'next_reset':int(reset.timestamp()),'next_reset_local':reset.astimezone(zone).isoformat()}
