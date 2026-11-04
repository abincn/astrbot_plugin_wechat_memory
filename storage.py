"""Fresh-install SQLite memory store. Raw evidence is never purged by Dreaming."""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .identity import Scope
from .schema_v3 import migrate
from .v3_schema import migrate as migrate_v3
from .policy import fingerprint, safe_content, topic_key


def now():
    return int(time.time())


def day_key():
    return dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')


class MemoryStore:
    def __init__(self, path: str | Path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=10000')
        self.db.execute('PRAGMA foreign_keys=ON')
        with self._lock:
            self.db.executescript('''
            CREATE TABLE IF NOT EXISTS raw_messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              message_id TEXT NOT NULL, sender_id TEXT NOT NULL,
              text TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'text',
              message_at INTEGER NOT NULL, received_at INTEGER NOT NULL,
              reply_to_id TEXT NOT NULL DEFAULT '', media_path TEXT NOT NULL DEFAULT '',
              media_kind TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'event',
              status TEXT NOT NULL DEFAULT 'ready', urgent INTEGER NOT NULL DEFAULT 0,
              UNIQUE(platform,owner,room,message_id)
            );
            CREATE INDEX IF NOT EXISTS raw_scope ON raw_messages(platform,owner,room,id);
            CREATE INDEX IF NOT EXISTS raw_sender ON raw_messages(platform,owner,room,sender_id,message_at);
            CREATE INDEX IF NOT EXISTS raw_time ON raw_messages(platform,owner,room,message_at);
            CREATE TABLE IF NOT EXISTS room_state (
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              dream_cursor INTEGER NOT NULL DEFAULT 0, paused INTEGER NOT NULL DEFAULT 0,
              next_attempt INTEGER NOT NULL DEFAULT 0, last_success INTEGER NOT NULL DEFAULT 0,
              last_error TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(platform,owner,room)
            );
            CREATE TABLE IF NOT EXISTS dream_runs (
              id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              from_cursor INTEGER NOT NULL, to_cursor INTEGER NOT NULL,
              status TEXT NOT NULL, dry_run INTEGER NOT NULL DEFAULT 0,
              error TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT '',
              input_estimated INTEGER NOT NULL DEFAULT 0, input_actual INTEGER,
              output_actual INTEGER, output_estimated INTEGER NOT NULL DEFAULT 0,
              changes_count INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, finished_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS runs_scope ON dream_runs(platform,owner,room,created_at);
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              scope TEXT NOT NULL CHECK(scope IN ('group','member','global')),
              subject_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'fact',
              topic_key TEXT NOT NULL DEFAULT '', content TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('active','review','superseded','deleted')),
              revision INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL DEFAULT 'dream',
              importance INTEGER NOT NULL DEFAULT 2,
              valid_from INTEGER, valid_to INTEGER,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS mem_scope ON memories(platform,owner,room,scope,subject_id,status);
            CREATE INDEX IF NOT EXISTS mem_topic ON memories(platform,owner,room,scope,subject_id,topic_key);
            CREATE TABLE IF NOT EXISTS memory_sources (
              memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
              raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
              PRIMARY KEY(memory_id,raw_id)
            );
            CREATE TABLE IF NOT EXISTS memory_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
              revision INTEGER NOT NULL, action TEXT NOT NULL, previous_content TEXT NOT NULL,
              previous_status TEXT NOT NULL, actor TEXT NOT NULL,
              run_id TEXT NOT NULL DEFAULT '', occurred_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dream_candidates (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES dream_runs(id),
              op_index INTEGER NOT NULL, action TEXT NOT NULL, memory_id TEXT NOT NULL DEFAULT '',
              subject_id TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
              old_content TEXT NOT NULL DEFAULT '', new_content TEXT NOT NULL DEFAULT '',
              reason TEXT NOT NULL DEFAULT '', sources_json TEXT NOT NULL DEFAULT '[]',
              expected_revision INTEGER, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '',
              applied_memory_id TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL,
              UNIQUE(run_id,op_index)
            );
            CREATE INDEX IF NOT EXISTS candidate_run ON dream_candidates(run_id,status);
            CREATE TABLE IF NOT EXISTS working_snapshots (
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              subject_id TEXT NOT NULL, content TEXT NOT NULL, revision_hash TEXT NOT NULL,
              updated_at INTEGER NOT NULL,
              PRIMARY KEY(platform,owner,room,subject_id)
            );
            CREATE TABLE IF NOT EXISTS memory_usage_daily (
              day TEXT NOT NULL, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              purpose TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0,
              input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0,
              estimated INTEGER NOT NULL DEFAULT 1, errors INTEGER NOT NULL DEFAULT 0,
              reserved_input INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(day,platform,owner,room,purpose)
            );
            CREATE TABLE IF NOT EXISTS profile_optouts (
              platform TEXT NOT NULL,owner TEXT NOT NULL,room TEXT NOT NULL,user_id TEXT NOT NULL,
              disabled INTEGER NOT NULL,changed_at INTEGER NOT NULL,
              PRIMARY KEY(platform,owner,room,user_id)
            );
            CREATE TABLE IF NOT EXISTS profile_permissions (
              platform TEXT NOT NULL, owner TEXT NOT NULL, user_id TEXT NOT NULL,
              room TEXT NOT NULL, allowed INTEGER NOT NULL, changed_at INTEGER NOT NULL,
              PRIMARY KEY(platform,owner,user_id,room)
            );
            CREATE TABLE IF NOT EXISTS lease (
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              holder TEXT NOT NULL, expires_at INTEGER NOT NULL,
              PRIMARY KEY(platform,owner,room)
            );
            CREATE TABLE IF NOT EXISTS import_cursors (
              source_hash TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              last_sequence INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
              PRIMARY KEY(source_hash,owner,room)
            );
            CREATE TABLE IF NOT EXISTS embedding_vectors (
              memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
              model TEXT NOT NULL, revision INTEGER NOT NULL, vector_json TEXT NOT NULL,
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS media_analysis (
              raw_id INTEGER PRIMARY KEY REFERENCES raw_messages(id) ON DELETE CASCADE,
              status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
              model TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
              updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS retrieval_audit (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              requester TEXT NOT NULL, selected_ids TEXT NOT NULL,
              raw_ids TEXT NOT NULL, chars INTEGER NOT NULL,
              mode TEXT NOT NULL, created_at INTEGER NOT NULL
            );
            ''')
            self._fts = False
            try:
                self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS raw_fts USING fts5(text, content='raw_messages', content_rowid='id', tokenize='trigram')")
                self.db.executescript('''
                CREATE TRIGGER IF NOT EXISTS raw_ai AFTER INSERT ON raw_messages BEGIN
                  INSERT INTO raw_fts(rowid,text) VALUES(new.id,new.text);
                END;
                CREATE TRIGGER IF NOT EXISTS raw_ad AFTER DELETE ON raw_messages BEGIN
                  INSERT INTO raw_fts(raw_fts,rowid,text) VALUES('delete',old.id,old.text);
                END;
                ''')
                self._fts = True
            except sqlite3.OperationalError:
                pass
            migrate(self.db)
            migrate_v3(self.db)

    @contextlib.contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    @staticmethod
    def _key(s: Scope):
        return s.platform, s.owner, s.room

    def enqueue(self, s: Scope, message_id: str, sender_id: str, text: str, timestamp: int,
                urgent: bool = False, *, kind='text', reply_to_id='', media_path='', media_kind='', source='event') -> bool:
        if not isinstance(message_id, str) or not 0 < len(message_id) <= 220:
            return False
        if not isinstance(sender_id, str) or not 0 < len(sender_id) <= 256:
            return False
        if not isinstance(text, str):
            text = ''
        if len(text.encode('utf-8')) > 262144:
            # Keep a visible evidence record, never silently truncate a message as if complete.
            text, kind = '', 'oversized'
        if kind == 'text' and not text.strip():
            return False
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO room_state(platform,owner,room) VALUES(?,?,?)',self._key(s))
            cur = self.db.execute('''INSERT OR IGNORE INTO raw_messages
                (platform,owner,room,message_id,sender_id,text,kind,message_at,received_at,
                 reply_to_id,media_path,media_kind,source,status,urgent)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (*self._key(s),message_id,sender_id,text,kind,int(timestamp),now(),
                 str(reply_to_id)[:220],str(media_path)[:1024],str(media_kind)[:64],str(source)[:50],
                 'skipped' if kind=='oversized' else 'ready',int(urgent)))
            return cur.rowcount > 0

    def scopes(self) -> list[Scope]:
        with self._lock:
            return [Scope(*r) for r in self.db.execute(
                'SELECT platform,owner,room FROM room_state ORDER BY owner,room')]

    def options(self, s: Scope, *, paused: bool | None = None) -> bool:
        with self.transaction() if paused is not None else self._lock:
            if paused is not None:
                self.db.execute('''INSERT INTO room_state(platform,owner,room,paused)
                    VALUES(?,?,?,?) ON CONFLICT(platform,owner,room) DO UPDATE SET paused=excluded.paused''',
                    (*self._key(s),int(paused)))
            row=self.db.execute('SELECT paused FROM room_state WHERE platform=? AND owner=? AND room=?', self._key(s)).fetchone()
            return bool(row and row['paused'])

    def due_scopes(self) -> list[Scope]:
        with self._lock:
            return [Scope(*r) for r in self.db.execute('''SELECT platform,owner,room FROM room_state
                WHERE paused=0 AND next_attempt<=? ORDER BY last_success LIMIT 100''',(now(),))]

    def backlog(self,s: Scope) -> tuple[int,int]:
        with self._lock:
            row=self.db.execute('''SELECT COUNT(*) n, MIN(received_at) oldest FROM raw_messages
                WHERE platform=? AND owner=? AND room=? AND id>(SELECT dream_cursor FROM room_state
                WHERE platform=? AND owner=? AND room=?) AND kind IN ('text','media_text')''',
                (*self._key(s),*self._key(s))).fetchone()
            return int(row['n']),int(row['oldest'] or 0)

    def pending_batch(self, s: Scope, *, max_messages: int, max_chars: int) -> tuple[int,list[dict],int]:
        with self._lock:
            state=self.db.execute('SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?',self._key(s)).fetchone()
            cursor=int(state['dream_cursor']) if state else 0
            rows=[dict(r) for r in self.db.execute('''SELECT id,message_id,sender_id,text,kind,message_at,
                reply_to_id,urgent,source FROM raw_messages WHERE platform=? AND owner=? AND room=? AND id>?
                ORDER BY id ASC LIMIT ?''',(*self._key(s),cursor,min(max_messages,500)))]
            selected=[]; length=0; last=cursor
            for r in rows:
                # Non-text/invalid rows advance the cursor without consuming LLM input.
                if r['kind'] not in ('text','media_text') or not r['text']:
                    last=r['id'];continue
                if len(r['text'])>max_chars:
                    if selected:break
                    # Oversized content stays in raw archive, marked skipped for dreaming.
                    last=r['id'];continue
                if length+len(r['text'])>max_chars:break
                selected.append(r);length+=len(r['text']);last=r['id']
            return cursor,selected, last

    def get_context_records(self,s:Scope, users:set[str],limit_per_user=20,group_limit=25)->list[dict]:
        with self._lock:
            result=[dict(r) for r in self.db.execute('''SELECT id,subject_id,scope,kind,content,revision,updated_at
              FROM memories WHERE platform=? AND owner=? AND room=? AND status='active' AND scope='group'
              ORDER BY updated_at DESC LIMIT ?''',(*self._key(s),group_limit))]
            for user in sorted(users)[:60]:
                if self.is_opted_out(s,user):continue
                result.extend(dict(r) for r in self.db.execute('''SELECT id,subject_id,scope,kind,content,revision,updated_at
                   FROM memories WHERE platform=? AND owner=? AND room=? AND scope='member'
                   AND subject_id=? AND status='active' ORDER BY updated_at DESC LIMIT ?''',
                   (*self._key(s),user,limit_per_user)))
            return result

    def acquire(self,s:Scope,holder:str,ttl:int=130)->bool:
        with self.transaction():
            row=self.db.execute('SELECT holder,expires_at FROM lease WHERE platform=? AND owner=? AND room=?',self._key(s)).fetchone()
            if row and row['expires_at']>now() and row['holder']!=holder:return False
            self.db.execute('''INSERT INTO lease(platform,owner,room,holder,expires_at) VALUES(?,?,?,?,?)
                ON CONFLICT(platform,owner,room) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at''',
                (*self._key(s),holder,now()+ttl))
            return True

    def lease_held(self,s:Scope,holder:str)->bool:
        with self._lock:
            row=self.db.execute('SELECT holder,expires_at FROM lease WHERE platform=? AND owner=? AND room=?',self._key(s)).fetchone()
            return bool(row and row['holder']==holder and row['expires_at']>now())

    def release(self,s:Scope,holder:str):
        with self.transaction():
            self.db.execute('DELETE FROM lease WHERE platform=? AND owner=? AND room=? AND holder=?',(*self._key(s),holder))

    def budget_reserve(self,s:Scope,estimated:int,*,purpose:str,limit_calls:int,limit_tokens:int,
                       day:str|None=None,max_usd:float=0,input_price:float=0,output_price:float=0,
                       expected_output:int=0)->bool:
        today=day or day_key()
        with self.transaction():
            self.db.execute('INSERT OR IGNORE INTO memory_usage_daily(day,platform,owner,room,purpose) VALUES(?,?,?,?,?)',
                            (today,*self._key(s),purpose))
            r=self.db.execute('''SELECT calls,input_tokens,output_tokens,reserved_input FROM memory_usage_daily
               WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?''',(today,*self._key(s),purpose)).fetchone()
            if r['calls']>=limit_calls or r['input_tokens']+r['output_tokens']+r['reserved_input']+estimated+expected_output>limit_tokens:
                return False
            if max_usd>0 and input_price>0 and output_price>0:
                estimate_usd=((r['input_tokens']+r['reserved_input']+estimated)*input_price
                              +(r['output_tokens']+expected_output)*output_price)/1_000_000
                if estimate_usd>max_usd:return False
            self.db.execute('''UPDATE memory_usage_daily SET calls=calls+1,reserved_input=reserved_input+?
               WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?''',
                (estimated,today,*self._key(s),purpose))
            return True

    def budget_finish(self,s:Scope,estimated:int,actual_in:int,actual_out:int,*,purpose:str,estimated_usage:bool,error:bool,day:str|None=None):
        with self.transaction():
            self.db.execute('''UPDATE memory_usage_daily SET reserved_input=MAX(0,reserved_input-?),
                input_tokens=input_tokens+?,output_tokens=output_tokens+?,
                estimated=MAX(estimated,?), errors=errors+?
                WHERE day=? AND platform=? AND owner=? AND room=? AND purpose=?''',
                (estimated,max(0,actual_in),max(0,actual_out),int(estimated_usage),int(error),day or day_key(),*self._key(s),purpose))

    def begin_run(self,s:Scope,cursor:int,end:int,provider:str,estimated:int,dry_run:bool)->str:
        rid=uuid.uuid4().hex
        with self.transaction():
            self.db.execute('''INSERT INTO dream_runs(id,platform,owner,room,from_cursor,to_cursor,status,
              dry_run,provider,input_estimated,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
              (rid,*self._key(s),cursor,end,'running',int(dry_run),provider,estimated,now()))
        return rid

    def finish_run(self,rid:str,status:str,changes:int=0,error:str='',actual_in:int|None=None,
                   actual_out:int|None=None,output_est:int=0):
        with self.transaction():
            self.db.execute('''UPDATE dream_runs SET status=?,changes_count=?,error=?,input_actual=?,
                output_actual=?,output_estimated=?,finished_at=? WHERE id=?''',
                (status,changes,error[:120],actual_in,actual_out,output_est,now(),rid))

    def fail_scope(self,s:Scope,reason:str,delay:int):
        with self.transaction():
            self.db.execute('''UPDATE room_state SET next_attempt=?,last_error=?
               WHERE platform=? AND owner=? AND room=?''',(now()+delay,reason[:120],*self._key(s)))

    def advance(self,s:Scope,start:int,end:int):
        with self.transaction():
            row=self.db.execute('SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?',self._key(s)).fetchone()
            if not row or row['dream_cursor']!=start:raise RuntimeError('Dream cursor changed')
            self.db.execute('''UPDATE room_state SET dream_cursor=?,next_attempt=0,last_success=?,last_error=''
               WHERE platform=? AND owner=? AND room=?''',(end,now(),*self._key(s)))

    def get_retrieval_candidates(self,s:Scope,user_id:str,include_global:bool=True,limit:int=500):
        with self._lock:
            opted=self.is_opted_out(s,user_id)
            items=[dict(r) for r in self.db.execute('''SELECT * FROM memories WHERE platform=? AND owner=? AND room=?
                AND status='active' AND (scope='group' OR (scope='member' AND subject_id=?))
                ORDER BY updated_at DESC LIMIT ?''',(*self._key(s),user_id,min(limit,1000)))]
            if opted:
                items=[r for r in items if r['scope']=='group']
            if include_global:
                permission=self.db.execute('''SELECT 1 FROM profile_permissions WHERE platform=? AND owner=?
                    AND user_id=? AND room=? AND allowed=1''',(s.platform,s.owner,user_id,s.room)).fetchone()
                if permission:
                    items.extend(dict(r) for r in self.db.execute('''SELECT * FROM memories WHERE platform=? AND owner=?
                        AND room='' AND scope='global' AND subject_id=? AND status='active' LIMIT 100''',
                        (s.platform,s.owner,user_id)))
            return items

    def active_snapshots(self,s:Scope,user_id:str,include_global:bool):
        allowed={(s.platform,s.owner,s.room,'') , (s.platform,s.owner,s.room,user_id)}
        with self._lock:
            if include_global and self.db.execute('''SELECT 1 FROM profile_permissions WHERE platform=? AND owner=? AND
                  room=? AND user_id=? AND allowed=1''',(s.platform,s.owner,s.room,user_id)).fetchone():
                allowed.add((s.platform,s.owner,'',user_id))
            output=[]
            for k in allowed:
                r=self.db.execute('''SELECT content FROM working_snapshots WHERE platform=? AND owner=? AND room=? AND subject_id=?''',k).fetchone()
                if r:output.append(r['content'])
            return output

    def refresh_snapshot(self,s:Scope,subject_id:str,max_chars:int=1100):
        with self.transaction():
            scope='global' if not s.room else 'group' if not subject_id else 'member'
            rows=self.db.execute('''SELECT id,revision,content,kind FROM memories WHERE platform=? AND owner=? AND room=?
                AND scope=? AND subject_id=? AND status='active' ORDER BY importance DESC,updated_at DESC,id LIMIT 80''',
                (*self._key(s),scope,subject_id)).fetchall()
            lines=[];chars=0
            for r in rows:
                line='- '+r['content']
                if chars+len(line)>max_chars:continue
                lines.append(line);chars+=len(line)
            content='\n'.join(lines)
            digest=hashlib.sha256(json.dumps([(r['id'],r['revision']) for r in rows]).encode()).hexdigest()[:20]
            self.db.execute('''INSERT INTO working_snapshots(platform,owner,room,subject_id,content,revision_hash,updated_at)
                VALUES(?,?,?,?,?,?,?) ON CONFLICT(platform,owner,room,subject_id)
                DO UPDATE SET content=excluded.content,revision_hash=excluded.revision_hash,updated_at=excluded.updated_at''',
                (*self._key(s),subject_id,content,digest,now()))
            return content

    def search_raw(self,s:Scope,query:str,limit:int=12,sender:str='',since:int=0,until:int=0):
        query=query.strip()[:120]
        if not query:return []
        conditions=['platform=?','owner=?','room=?','kind IN (\'text\',\'media_text\')']
        params=list(self._key(s))
        if sender:
            conditions.append('sender_id=?');params.append(sender)
        if since:
            conditions.append('message_at>=?');params.append(since)
        if until:
            conditions.append('message_at<=?');params.append(until)
        # Trigram FTS performs well for Chinese >=3 chars; LIKE handles 1-2 chars.
        if self._fts and len(query)>=3 and all(ch not in query for ch in ('"','*')):
            try:
                with self._lock:
                    sql='''SELECT r.id,r.message_id,r.sender_id,r.text,r.message_at,r.reply_to_id
                         FROM raw_fts f JOIN raw_messages r ON r.id=f.rowid WHERE raw_fts MATCH ?
                         AND '''+' AND '.join('r.'+c for c in conditions)+''' ORDER BY bm25(raw_fts),r.message_at DESC LIMIT ?'''
                    return [dict(r) for r in self.db.execute(sql,[f'"{query}"',*params,min(limit,50)])]
            except sqlite3.OperationalError:
                pass
        escaped=query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT id,message_id,sender_id,text,message_at,reply_to_id
                FROM raw_messages WHERE '''+' AND '.join(conditions)+" AND text LIKE ? ESCAPE '\\' ORDER BY message_at DESC LIMIT ?",
                [*params,'%'+escaped+'%',min(limit,50)])]

    def recent_raw(self,s:Scope,since:int,until:int,limit:int=30):
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT id,message_id,sender_id,text,message_at,reply_to_id
              FROM raw_messages WHERE platform=? AND owner=? AND room=? AND message_at>=? AND message_at<?
              AND kind IN ('text','media_text') ORDER BY message_at DESC,id DESC LIMIT ?''',
              (*self._key(s),since,until,min(limit,50)))]

    def raw_by_ids(self,s:Scope,ids:list[str]):
        if not ids:return {}
        with self._lock:
            marks=','.join('?' for _ in ids[:40])
            rows=self.db.execute('''SELECT id,message_id,sender_id,text,message_at,kind FROM raw_messages
                WHERE platform=? AND owner=? AND room=? AND message_id IN ('''+marks+')',
                (*self._key(s),*ids[:40])).fetchall()
            return {r['message_id']:dict(r) for r in rows}

    def history(self,s:Scope,limit:int=60):
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT id,message_id,sender_id,text,message_at,kind,
                 media_kind,reply_to_id,status FROM raw_messages WHERE platform=? AND owner=? AND room=?
                 ORDER BY id DESC LIMIT ?''',(*self._key(s),min(limit,200)))]

    def list_runs(self,s:Scope,limit:int=40):
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT * FROM dream_runs WHERE platform=? AND owner=? AND room=?
                ORDER BY created_at DESC LIMIT ?''',(*self._key(s),min(limit,100)))]

    def candidates(self,s:Scope,run_id:str='',status:str='',limit:int=100):
        with self._lock:
            cond='WHERE r.platform=? AND r.owner=? AND r.room=?';args=list(self._key(s))
            if run_id:cond+=' AND c.run_id=?';args.append(run_id)
            if status:cond+=' AND c.status=?';args.append(status)
            return [dict(r) for r in self.db.execute('''SELECT c.* FROM dream_candidates c JOIN dream_runs r ON r.id=c.run_id '''+
               cond+' ORDER BY c.created_at DESC,c.op_index LIMIT ?',(*args,min(limit,200)))]

    def list_memories(self,s:Scope,*,status='',kind='',query='',limit=100):
        sql='''SELECT m.*, (SELECT COUNT(*) FROM memory_sources src WHERE src.memory_id=m.id) source_count
            FROM memories m WHERE platform=? AND owner=? AND room=? AND scope!='global' '''
        args=list(self._key(s))
        if status in ('active','review','superseded','deleted'):
            sql+=' AND status=?';args.append(status)
        if kind in ('fact','decision','context','preference','profile'):
            sql+=' AND kind=?';args.append(kind)
        if query:
            sql+=' AND content LIKE ?';args.append('%'+query[:100].replace('%','')+'%')
        sql+=' ORDER BY updated_at DESC,id DESC LIMIT ?';args.append(min(max(1,limit),200))
        with self._lock:return [dict(r) for r in self.db.execute(sql,args)]

    def sources(self,s:Scope,mid:str):
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT m.message_id,m.sender_id,m.message_at AS created_at,
                  substr(m.text,1,2000) text FROM memory_sources src
                  JOIN raw_messages m ON m.id=src.raw_id JOIN memories fact ON fact.id=src.memory_id
                  WHERE fact.id=? AND fact.platform=? AND fact.owner=? AND fact.room=?
                  ORDER BY m.message_at LIMIT 40''',(mid,*self._key(s)))]

    def upsert_manual(self,s:Scope,*,content,kind='fact',subject='',topic='',actor='admin'):
        text=safe_content(content,1000)
        if kind not in ('fact','decision','context','preference','profile'):raise ValueError('Invalid type')
        if kind=='profile' and not subject:raise ValueError('Member ID required')
        subject=str(subject).strip() if kind=='profile' else ''
        mid=uuid.uuid4().hex
        with self.transaction():
            self.db.execute('''INSERT INTO memories(id,platform,owner,room,scope,subject_id,kind,topic_key,content,
              status,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,'active',?,?,?)''',
              (mid,*self._key(s),'member' if subject else 'group',subject,kind,topic_key(topic,text),text,
               'manual:'+actor[:60],now(),now()))
        self.refresh_snapshot(s,subject)
        return mid

    def _version(self,row,action,actor,run_id=''):
        self.db.execute('''INSERT INTO memory_versions(memory_id,revision,action,previous_content,previous_status,
           actor,run_id,occurred_at) VALUES(?,?,?,?,?,?,?,?)''',
           (row['id'],row['revision'],action,row['content'],row['status'],actor[:100],run_id,now()))

    def revise(self,s:Scope,mid:str,*,action:str,actor:str,content='',expected_revision:int|None=None):
        if action not in ('approve','edit','disable','delete','rollback'):raise ValueError('Invalid action')
        with self.transaction():
            row=self.db.execute('''SELECT * FROM memories WHERE id=? AND platform=? AND owner=? AND room=? AND scope!='global' ''',
                (mid,*self._key(s))).fetchone()
            if not row:return False
            if expected_revision is not None and row['revision']!=expected_revision:raise ValueError('Revision conflict')
            self._version(row,action,actor)
            if action=='approve':
                if row['status']!='review':raise ValueError('Not review')
                self.db.execute("UPDATE memories SET status='active',revision=revision+1,updated_at=? WHERE id=?",(now(),mid))
            elif action=='edit':
                val=safe_content(content,1000)
                self.db.execute('UPDATE memories SET content=?,revision=revision+1,updated_at=? WHERE id=?',(val,now(),mid))
            elif action=='rollback':
                old=self.db.execute('''SELECT previous_content,previous_status FROM memory_versions
                    WHERE memory_id=? AND revision<? ORDER BY revision DESC,id DESC LIMIT 1''',(mid,row['revision'])).fetchone()
                if not old:raise ValueError('No previous version')
                restored_status=old['previous_status']
                if restored_status not in ('active','review','superseded','deleted'):
                    raise ValueError('Invalid previous state')
                self.db.execute('''UPDATE memories SET content=?,status=?,revision=revision+1,updated_at=? WHERE id=?''',
                                (old['previous_content'],restored_status,now(),mid))
            else:
                self.db.execute('''UPDATE memories SET status=?,revision=revision+1,updated_at=? WHERE id=?''',
                  ('deleted' if action=='delete' else 'superseded',now(),mid))
            self.db.execute('DELETE FROM embedding_vectors WHERE memory_id=?',(mid,))
        self.refresh_snapshot(s,row['subject_id'])
        return True

    def global_add(self,platform,owner,user,content,source_message_id=''):
        text=safe_content(content,600);mid=uuid.uuid4().hex
        with self.transaction():
            self.db.execute('''INSERT INTO memories(id,platform,owner,room,scope,subject_id,kind,topic_key,content,
                status,origin,created_at,updated_at) VALUES(?,?,?,'','global',?,'profile',?,?,'active','user_dm',?,?)''',
                (mid,platform,owner,user,topic_key('',text),text,now(),now()))
        self.refresh_snapshot(Scope(platform,owner,''),user)
        return mid

    def global_list(self,platform,owner,user):
        with self._lock:
            return [dict(r) for r in self.db.execute('''SELECT id,content,created_at FROM memories
                WHERE platform=? AND owner=? AND room='' AND scope='global' AND subject_id=? AND status='active'
                ORDER BY updated_at DESC LIMIT 100''',(platform,owner,user))]

    def global_delete(self,platform,owner,user,mid):
        with self.transaction():
            row=self.db.execute('''SELECT * FROM memories WHERE id=? AND platform=? AND owner=?
              AND room='' AND scope='global' AND subject_id=? AND status='active' ''',(mid,platform,owner,user)).fetchone()
            if not row:return False
            self._version(row,'user_delete',user)
            self.db.execute("UPDATE memories SET status='deleted',revision=revision+1,updated_at=? WHERE id=?",(now(),mid))
            self.db.execute('DELETE FROM embedding_vectors WHERE memory_id=?',(mid,))
        self.refresh_snapshot(Scope(platform,owner,''),user)
        return True

    def set_permission(self,platform,owner,user,room,allowed):
        with self.transaction():
            self.db.execute('''INSERT INTO profile_permissions(platform,owner,user_id,room,allowed,changed_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(platform,owner,user_id,room) DO UPDATE
                SET allowed=excluded.allowed,changed_at=excluded.changed_at''',
                (platform,owner,user,room,int(allowed),now()))

    def list_permissions(self,platform,owner,user):
        with self._lock:return [r['room'] for r in self.db.execute('''SELECT room FROM profile_permissions
            WHERE platform=? AND owner=? AND user_id=? AND allowed=1 ORDER BY room''',(platform,owner,user))]

    def revoke_all(self,platform,owner,user):
        with self.transaction():
            return self.db.execute('''UPDATE profile_permissions SET allowed=0,changed_at=?
               WHERE platform=? AND owner=? AND user_id=? AND allowed=1''',(now(),platform,owner,user)).rowcount

    def record_retrieval(self,s:Scope,user:str,mem_ids:list[str],raw_ids:list[int],chars:int,mode:str):
        with self.transaction():
            self.db.execute('''INSERT INTO retrieval_audit(platform,owner,room,requester,selected_ids,raw_ids,
                chars,mode,created_at) VALUES(?,?,?,?,?,?,?,?,?)''',
                (*self._key(s),user,json.dumps(mem_ids),json.dumps(raw_ids),chars,mode,now()))
            self.db.execute('''DELETE FROM retrieval_audit WHERE id IN
                (SELECT id FROM retrieval_audit WHERE platform=? AND owner=? AND room=?
                 ORDER BY id DESC LIMIT -1 OFFSET 1000)''',self._key(s))

    def retrieval_audit(self,s:Scope,limit=40):
        with self._lock:return [dict(r) for r in self.db.execute('''SELECT id,requester,selected_ids,raw_ids,chars,mode,created_at
            FROM retrieval_audit WHERE platform=? AND owner=? AND room=? ORDER BY id DESC LIMIT ?''',(*self._key(s),min(limit,100)))]

    def stats(self,s:Scope):
        with self._lock:
            row=self.db.execute('''SELECT dream_cursor,last_success,last_error,paused FROM room_state
              WHERE platform=? AND owner=? AND room=?''',self._key(s)).fetchone()
            stats={'cursor':int(row['dream_cursor']) if row else 0,'last_success':int(row['last_success']) if row else 0,
                   'last_error':row['last_error'] if row else '', 'paused':bool(row['paused']) if row else False}
            for key,sql in [
                ('raw',"SELECT COUNT(*) FROM raw_messages WHERE platform=? AND owner=? AND room=?"),
                ('active',"SELECT COUNT(*) FROM memories WHERE platform=? AND owner=? AND room=? AND status='active'"),
                ('review',"SELECT COUNT(*) FROM memories WHERE platform=? AND owner=? AND room=? AND status='review'"),
                ('candidates_review',"SELECT COUNT(*) FROM dream_candidates c JOIN dream_runs d ON d.id=c.run_id WHERE d.platform=? AND d.owner=? AND d.room=? AND c.status='review'")]:
                stats[key]=self.db.execute(sql,self._key(s)).fetchone()[0]
            stats['pending']=self.backlog(s)[0]
            usage=self.db.execute('''SELECT COALESCE(SUM(calls),0) calls, COALESCE(SUM(input_tokens),0) input_tokens,
                COALESCE(SUM(output_tokens),0) output_tokens,COALESCE(SUM(errors),0) errors
                FROM memory_usage_daily WHERE day=? AND platform=? AND owner=? AND room=?''',(day_key(),*self._key(s))).fetchone()
            stats['today_usage']=dict(usage)
            return stats

    def data_quality(self,s:Scope):
        with self._lock:
            stats=self.stats(s)
            stats['runs_7d']=[dict(r) for r in self.db.execute('''SELECT status,COUNT(*) count,SUM(changes_count) changes
                FROM dream_runs WHERE platform=? AND owner=? AND room=? AND created_at>?
                GROUP BY status''',(*self._key(s),now()-7*86400))]
            stats['media_pending']=self.db.execute('''SELECT COUNT(*) FROM raw_messages r LEFT JOIN media_analysis a ON a.raw_id=r.id
                WHERE r.platform=? AND r.owner=? AND r.room=? AND r.media_path!='' AND a.raw_id IS NULL''',self._key(s)).fetchone()[0]
            return stats

    def close(self):
        with self._lock:self.db.close()

    def opt_out(self,s:Scope,user:str,disabled:bool):
        with self.transaction():
            self.db.execute('''CREATE TABLE IF NOT EXISTS profile_optouts (
                platform TEXT NOT NULL,owner TEXT NOT NULL,room TEXT NOT NULL,user_id TEXT NOT NULL,
                disabled INTEGER NOT NULL,changed_at INTEGER NOT NULL,
                PRIMARY KEY(platform,owner,room,user_id))''')
            self.db.execute('''INSERT INTO profile_optouts(platform,owner,room,user_id,disabled,changed_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(platform,owner,room,user_id)
                DO UPDATE SET disabled=excluded.disabled,changed_at=excluded.changed_at''',
                (*self._key(s),user,int(disabled),now()))
            if disabled:
                self.db.execute(
                    """UPDATE observation_signals SET status='invalidated',content='',
                       updated_at=? WHERE platform=? AND owner=? AND room=?
                       AND scope='member' AND subject_id=?""",
                    (now(),*self._key(s),user))
                rows=self.db.execute('''SELECT * FROM memories WHERE platform=? AND owner=? AND room=?
                    AND scope='member' AND subject_id=? AND status!='deleted' ''',(*self._key(s),user)).fetchall()
                for m in rows:
                    self._version(m,'user_profile_optout',user)
                    self.db.execute('''UPDATE memories SET status='deleted',revision=revision+1,updated_at=? WHERE id=?''',
                                    (now(),m['id']))
                    self.db.execute('DELETE FROM embedding_vectors WHERE memory_id=?',(m['id'],))
        self.refresh_snapshot(s,user)

    def is_opted_out(self,s:Scope,user:str)->bool:
        with self._lock:
            try:
                r=self.db.execute('''SELECT disabled FROM profile_optouts WHERE platform=? AND owner=?
                    AND room=? AND user_id=?''',(*self._key(s),user)).fetchone()
                return bool(r and r['disabled'])
            except sqlite3.OperationalError:return False

    def erase_user_from_room(self,s:Scope,user:str)->dict:
        """Self-service erasure of user-authored raw evidence and group member profile."""
        with self.transaction():
            self.db.execute('''CREATE TABLE IF NOT EXISTS profile_optouts (
                platform TEXT NOT NULL,owner TEXT NOT NULL,room TEXT NOT NULL,user_id TEXT NOT NULL,
                disabled INTEGER NOT NULL,changed_at INTEGER NOT NULL,
                PRIMARY KEY(platform,owner,room,user_id))''')
            self.db.execute('''INSERT INTO profile_optouts(platform,owner,room,user_id,disabled,changed_at)
                VALUES(?,?,?,?,1,?) ON CONFLICT(platform,owner,room,user_id)
                DO UPDATE SET disabled=1,changed_at=excluded.changed_at''',(*self._key(s),user,now()))
            memories=[r['id'] for r in self.db.execute('''SELECT id FROM memories WHERE platform=? AND owner=? AND room=?
                AND scope='member' AND subject_id=?''',(*self._key(s),user))]
            for mid in memories:
                self.db.execute('DELETE FROM memory_versions WHERE memory_id=?',(mid,))
                self.db.execute('DELETE FROM memories WHERE id=?',(mid,))
            erased_ids=[r['message_id'] for r in self.db.execute('''SELECT message_id FROM raw_messages
                WHERE platform=? AND owner=? AND room=? AND sender_id=?''',(*self._key(s),user))]
            for candidate in self.db.execute('''SELECT c.id,c.sources_json FROM dream_candidates c
                JOIN dream_runs d ON d.id=c.run_id WHERE d.platform=? AND d.owner=? AND d.room=?''',self._key(s)).fetchall():
                try:linked=set(json.loads(candidate['sources_json']))
                except (ValueError,TypeError):linked=set()
                if linked.intersection(erased_ids):
                    self.db.execute('''UPDATE dream_candidates SET old_content='',new_content='',reason='',
                        sources_json='[]',status='redacted',error='source_erasure' WHERE id=?''',(candidate['id'],))
            # Redact derivative texts containing the user's source evidence before
            # cascading deletion of the raw source links.
            self.db.execute(
                """UPDATE observation_signals SET status='invalidated',content='',
                   updated_at=? WHERE id IN (
                     SELECT DISTINCT x.observation_id FROM observation_sources x
                     JOIN raw_messages m ON m.id=x.raw_id
                     WHERE m.platform=? AND m.owner=? AND m.room=? AND m.sender_id=?)""",
                (now(),*self._key(s),user))
            self.db.execute(
                """UPDATE observation_signals SET status='invalidated',content='',
                   updated_at=? WHERE platform=? AND owner=? AND room=?
                   AND scope='member' AND subject_id=?""",
                (now(),*self._key(s),user))
            self.db.execute(
                """UPDATE topic_states SET title='历史话题（证据已清除）',
                   summary='',state='closed',updated_at=?
                   WHERE id IN (
                     SELECT DISTINCT x.topic_id FROM topic_sources x
                     JOIN raw_messages m ON m.id=x.raw_id
                     WHERE m.platform=? AND m.owner=? AND m.room=? AND m.sender_id=?)""",
                (now(),*self._key(s),user))
            erased=self.db.execute('''DELETE FROM raw_messages WHERE platform=? AND owner=? AND room=? AND sender_id=?''',
                                   (*self._key(s),user)).rowcount
            self.db.execute('''DELETE FROM retrieval_audit WHERE platform=? AND owner=? AND room=? AND requester=?''',
                            (*self._key(s),user))
            self.db.execute('''DELETE FROM working_snapshots WHERE platform=? AND owner=? AND room=? AND subject_id=?''',
                            (*self._key(s),user))
            # Any generated group record that lost ALL its original evidence must be quarantined.
            self.db.execute('''UPDATE memories SET status='review',revision=revision+1,updated_at=?
               WHERE platform=? AND owner=? AND room=? AND scope='group' AND origin='dream'
               AND NOT EXISTS(SELECT 1 FROM memory_sources src WHERE src.memory_id=memories.id)''',
               (now(),*self._key(s)))
        self.refresh_snapshot(s,'')
        return {'raw_removed':erased,'profile_removed':len(memories)}

    # Dreaming V2: all queries remain scoped to the bot account and group.
    def v2_context(self, s: Scope, senders: set[str], topic_limit: int = 8,
                   observation_limit: int = 24) -> tuple[list[dict], list[dict]]:
        with self._lock:
            topics = [dict(r) for r in self.db.execute(
                """SELECT id,title,summary,state,revision FROM topic_states
                   WHERE platform=? AND owner=? AND room=?
                   ORDER BY last_message_at DESC LIMIT ?""",
                (*self._key(s), min(12, max(1, topic_limit))))]
            users = sorted(sender for sender in senders if not self.is_opted_out(s, sender))
            if users:
                placeholders = ','.join('?' for _ in users[:60])
                query = ("""SELECT id,scope,subject_id,signal_type,content,evidence_type,
                            status,revision,topic_id FROM observation_signals
                            WHERE platform=? AND owner=? AND room=? AND status='observing'
                            AND (scope='group' OR subject_id IN (""" + placeholders +
                         """)) ORDER BY updated_at DESC LIMIT ?""")
                params = (*self._key(s), *users[:60], min(40, max(1, observation_limit)))
            else:
                query = """SELECT id,scope,subject_id,signal_type,content,evidence_type,
                           status,revision,topic_id FROM observation_signals
                           WHERE platform=? AND owner=? AND room=? AND status='observing'
                           AND scope='group' ORDER BY updated_at DESC LIMIT ?"""
                params = (*self._key(s), min(40, max(1, observation_limit)))
            observations = [dict(r) for r in self.db.execute(query, params)]
            return topics, observations

    def list_topics(self, s: Scope, limit: int = 50) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                """SELECT t.*,(SELECT COUNT(*) FROM topic_sources x WHERE x.topic_id=t.id) source_count
                   FROM topic_states t WHERE platform=? AND owner=? AND room=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (*self._key(s), min(100, max(1, limit))))]

    def list_observations(self, s: Scope, limit: int = 100) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                """SELECT o.*,(SELECT COUNT(*) FROM observation_sources x
                   WHERE x.observation_id=o.id) source_count
                   FROM observation_signals o WHERE platform=? AND owner=? AND room=?
                   ORDER BY updated_at DESC LIMIT ?""",
                (*self._key(s), min(200, max(1, limit))))]

    def v2_run_detail(self, s: Scope, run_id: str) -> dict | None:
        with self._lock:
            r = self.db.execute(
                """SELECT d.* FROM dream_run_details d JOIN dream_runs r ON r.id=d.run_id
                   WHERE r.id=? AND r.platform=? AND r.owner=? AND r.room=?""",
                (run_id, *self._key(s))).fetchone()
            return dict(r) if r else None


    def replay_prepare(self, s: Scope, start: int, end: int) -> dict:
        with self._lock:
            bounds=self.db.execute(
                """SELECT COUNT(*) n,COALESCE(MAX(id),0) max_id FROM raw_messages
                   WHERE platform=? AND owner=? AND room=?""",self._key(s)).fetchone()
            maximum=int(bounds['max_id'])
            if maximum==0:
                raise ValueError('该群没有原始消息')
            start=int(start);end=int(end) if end else maximum
            if start<1 or end<start or end>maximum:
                raise ValueError('历史重分析 ID 范围不合法')
            total=self.db.execute(
                """SELECT COUNT(*) FROM raw_messages WHERE platform=? AND owner=? AND room=?
                   AND id BETWEEN ? AND ?""",(*self._key(s),start,end)).fetchone()[0]
            if total==0:
                raise ValueError('指定范围没有该群的原始消息')
            return {'from_id':start,'to_id':end,'rows':total,'source_max_id':maximum}

    def replay_create(self,s:Scope,actor:str,start:int,end:int,mode:str)->dict:
        if mode not in ('preview','apply'):
            raise ValueError('Invalid replay mode')
        scope=self.replay_prepare(s,start,end)
        rid=uuid.uuid4().hex
        with self.transaction():
            busy=self.db.execute(
                """SELECT id FROM reanalysis_jobs WHERE platform=? AND owner=? AND room=?
                   AND status IN ('pending','running','paused','failed') LIMIT 1""",self._key(s)).fetchone()
            if busy:raise ValueError('当前群已有运行中的历史重分析任务')
            self.db.execute(
                """INSERT INTO reanalysis_jobs(id,platform,owner,room,start_raw_id,end_raw_id,
                   next_raw_id,status,mode,actor,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (rid,*self._key(s),scope['from_id'],scope['to_id'],scope['from_id'],
                 'pending',mode,str(actor)[:100],now(),now()))
        return {'id':rid,**scope,'status':'pending','mode':mode}

    def replay_job(self,s:Scope,rid:str)->dict|None:
        with self._lock:
            r=self.db.execute(
                """SELECT * FROM reanalysis_jobs WHERE id=? AND platform=? AND owner=? AND room=?""",
                (rid,*self._key(s))).fetchone()
            return dict(r) if r else None

    def replay_jobs(self,s:Scope,limit:int=30)->list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute(
                """SELECT * FROM reanalysis_jobs WHERE platform=? AND owner=? AND room=?
                   ORDER BY created_at DESC LIMIT ?""",
                (*self._key(s),min(50,max(1,limit))))]

    def replay_status(self,s:Scope,rid:str,status:str,error:str=''):
        if status not in ('pending','running','paused','completed','failed','cancelled'):
            raise ValueError('Invalid replay status')
        with self.transaction():
            row=self.db.execute(
                """SELECT status FROM reanalysis_jobs WHERE id=? AND platform=? AND owner=? AND room=?""",
                (rid,*self._key(s))).fetchone()
            if not row:raise ValueError('History job not found')
            if row['status'] in ('completed','cancelled'):
                raise ValueError('History job has already stopped')
            self.db.execute(
                """UPDATE reanalysis_jobs SET status=?,last_error=?,updated_at=?
                   WHERE id=? AND platform=? AND owner=? AND room=?""",
                (status,error[:160],now(),rid,*self._key(s)))

    def replay_batch(self,s:Scope,start:int,end_limit:int,max_messages:int,max_chars:int):
        with self._lock:
            rows=[dict(r) for r in self.db.execute(
                """SELECT id,message_id,sender_id,text,kind,message_at,reply_to_id,urgent,source
                   FROM raw_messages WHERE platform=? AND owner=? AND room=?
                   AND id>=? AND id<=? ORDER BY id LIMIT ?""",
                (*self._key(s),start,end_limit,min(max(1,max_messages),500)))]
            if not rows:
                return start-1,[],end_limit
            selected=[];length=0;last=start-1
            for row in rows:
                if row['kind'] not in ('text','media_text') or not row['text']:
                    last=row['id'];continue
                if len(row['text'])>max_chars:
                    if selected:break
                    last=row['id'];continue
                if length+len(row['text'])>max_chars:break
                selected.append(row);length+=len(row['text']);last=row['id']
            return start-1,selected,last

    def replay_advance_empty(self,s:Scope,job_id:str,start_cursor:int,end:int):
        with self.transaction():
            cur=self.db.execute(
                """UPDATE reanalysis_jobs SET next_raw_id=?,batches_done=batches_done+1,
                   updated_at=? WHERE id=? AND platform=? AND owner=? AND room=?
                   AND status='running' AND next_raw_id=?""",
                (end+1,now(),job_id,*self._key(s),start_cursor+1))
            if cur.rowcount!=1:raise RuntimeError('History job advanced concurrently')
