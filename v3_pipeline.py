"""V3 task execution: dynamic source batching, budget and atomic progress."""
from __future__ import annotations

import asyncio
import uuid

from astrbot.api import logger

from .extraction import estimate_tokens
from .storage import day_key,now
from . import v3_extract,v3_store
from .message_content import prepare_message


def select_batch(store,s,*,cursor,end_limit=None,max_messages=500,max_chars=45000,replay_job_id=''):
    """Return a contiguous source range, including explicit skipped/deferred rows."""
    with store._lock:
        if end_limit is None:
            state=store.db.execute(
                'SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?',
                v3_store.key(s)).fetchone()
            cursor=int(state['dream_cursor']) if state else 0
            where='id>?';bounds=[cursor]
        else:
            where='id>? AND id<=?';bounds=[cursor,end_limit]
        rows=[dict(r) for r in store.db.execute(
            """SELECT id,message_id,sender_id,text,kind,message_at,reply_to_id,source,identity_json
               FROM raw_messages WHERE platform=? AND owner=? AND room=? AND """
            +where+' ORDER BY id LIMIT ?',
            (*v3_store.key(s),*bounds,max(1,min(max_messages,500))))]
        batch=[];skipped=[];last=cursor;chars=0
        for row in rows:
            if row['kind'] not in ('text','media_text') or not row['text']:
                detail=('missing_raw_requires_reimport' if row['kind']=='oversized' else 'media_pending_or_missing' if row['kind']=='media' else 'not_analyzable_text')
                skipped.append({'id':row['id'],'reason':'deferred' if row['kind'] in ('oversized','media') else 'excluded_by_policy','detail':detail})
                last=row['id'];continue
            row=prepare_message(row)
            if row['_deferred']:
                skipped.append({'id':row['id'],'reason':'deferred','detail':row['_deferred']})
                last=row['id'];continue
            size=len(row['text'])
            previous_fragment=store.db.execute('SELECT offset,chunk_chars FROM fragment_progress WHERE job_id=? AND raw_id=?',(replay_job_id,row['id'])).fetchone() if replay_job_id else None
            if size>max_chars or previous_fragment:
                if batch:break
                if row['_content'].get('xml'):
                    skipped.append({'id':row['id'],'reason':'deferred','detail':'XML 正文过长或存在旧分片，需单独整理，未送入模型'})
                    last=row['id'];continue
                if replay_job_id:
                    progress=store.db.execute('SELECT offset FROM fragment_progress WHERE job_id=? AND raw_id=?',(replay_job_id,row['id'])).fetchone()
                    offset=int(progress['offset']) if progress else 0
                    row['_fragment_start']=offset
                    row['_fragment_total']=size
                    chunk_chars=min(max_chars,int(progress['chunk_chars'])) if progress else max_chars
                    row['text']=row['text'][offset:offset+chunk_chars]
                    row['_fragment_end']=offset+len(row['text'])
                    batch.append(row);last=row['id'];break
                skipped.append({'id':row['id'],'reason':'deferred','detail':'oversized_text_requires_separate_review'})
                last=row['id'];continue
            if chars+size>max_chars:break
            batch.append(row);chars+=size;last=row['id']
        if not rows and end_limit is not None:return cursor,[],[],end_limit
        return cursor,batch,skipped,last


def _usage_reason(store,s,estimate,cfg):
    with store._lock:
        r=store.db.execute(
            """SELECT COALESCE(SUM(calls),0) calls,
                      COALESCE(SUM(input_tokens+output_tokens+reserved_input),0) tokens
               FROM memory_usage_daily WHERE day=? AND platform=? AND owner=? AND room=?
               AND purpose='dream'""",
            (day_key(),*v3_store.key(s))).fetchone()
        calls=int(r['calls']);tokens=int(r['tokens'])
        limit_calls=max(1,int(cfg.get('max_calls_per_group_day',2)))
        limit_tokens=max(1000,int(cfg.get('max_tokens_per_group_day',40000)))
        reserved=max(0,int(cfg.get('estimated_output_tokens_per_run',1500)))
        if calls>=limit_calls:return f'今日模型调用次数已达上限：{calls}/{limit_calls} 次。'
        if tokens+estimate+reserved>limit_tokens:
            return f'今日用量预算不足：已使用及预留 {tokens}，本批预计输入 {estimate}、输出 {reserved}，上限 {limit_tokens}。'
        return '预计费用超过已配置的金额预算，或其他用量限制。'


def _record_empty(store,s,cursor,end,skipped,*,dry_run=False,replay_job_id=''):
    if end<=cursor:return {'status':'empty','cursor':end}
    with store.transaction():
        if replay_job_id:
            job=store.db.execute(
                """SELECT status,next_raw_id FROM reanalysis_jobs WHERE id=?
                   AND platform=? AND owner=? AND room=?""",
                (replay_job_id,*v3_store.key(s))).fetchone()
            if not job or job['status']!='running' or job['next_raw_id']!=cursor+1:
                raise RuntimeError('Replay cursor changed')
            store.db.execute(
                """UPDATE reanalysis_jobs SET next_raw_id=?,updated_at=?
                   WHERE id=? AND platform=? AND owner=? AND room=?""",
                (end+1,now(),replay_job_id,*v3_store.key(s)))
        elif not dry_run:
            cur=store.db.execute(
                """UPDATE room_state SET dream_cursor=? WHERE platform=? AND owner=? AND room=?
                   AND dream_cursor=?""",
                (end,*v3_store.key(s),cursor))
            if cur.rowcount!=1:raise RuntimeError('Production cursor changed')
        if not dry_run:
            for m in skipped:
                store.db.execute(
                    """INSERT INTO analysis_ledger
                       (platform,owner,room,raw_id,pipeline_version,result,reason,updated_at)
                       VALUES(?,?,?,?,3,?,?,?)
                       ON CONFLICT(platform,owner,room,raw_id,pipeline_version) DO UPDATE SET
                           result=excluded.result,reason=excluded.reason,
                           updated_at=excluded.updated_at""",
                    (*v3_store.key(s),m['id'],m['reason'],m['detail'],now()))
    return {'status':'empty','cursor':end,'skipped':len(skipped)}


async def run(scheduler,s,*,dry_run=False,force=False,replay_job_id=''):
    store=scheduler.store;cfg=scheduler.config
    if not cfg.get('enabled') or not cfg.get('extraction_provider_id'):
        return {'status':'disabled','reason':'No provider configured'}
    if s.platform!=cfg.get('platform_id','wechat_pc_hook') or s.room not in scheduler.allowed_rooms or store.options(s):
        return {'status':'denied'}
    holder=uuid.uuid4().hex
    if not store.acquire(s,holder,ttl=150):return {'status':'busy'}
    try:
        if replay_job_id:
            job=store.replay_job(s,replay_job_id)
            if not job or job['status']!='running':return {'status':'denied'}
            cursor=int(job['next_raw_id'])-1
            end_limit=int(job['end_raw_id'])
        else:
            cursor=0;end_limit=None
        max_messages=max(1,min(500,int(cfg.get('max_messages_per_run',500))))
        with store._lock:
            hint=store.db.execute(
                """SELECT max_messages FROM v3_batch_hints
                   WHERE platform=? AND owner=? AND room=?""",
                v3_store.key(s)).fetchone()
        if hint:max_messages=min(max_messages,max(1,int(hint['max_messages'])))
        max_chars=max(1000,min(120000,int(cfg.get('max_message_chars_per_run',45000))))
        cursor,batch,skipped,end=select_batch(
            store,s,cursor=cursor,end_limit=end_limit,
            max_messages=max_messages,max_chars=max_chars,replay_job_id=replay_job_id)
        if not batch:
            return _record_empty(store,s,cursor,end,skipped,
                                 dry_run=dry_run,replay_job_id=replay_job_id)
        if not force and len(batch)<max(1,int(cfg.get('min_messages_to_dream',10))):
            return {'status':'insufficient','messages':len(batch)}

        # Model context may grow; select a complete message prefix by estimated token budget.
        events,claims=v3_store.context(store,s,{m['sender_id'] for m in batch},texts=[m['text'] for m in batch])
        from .people import aliases
        name_context=[{'subject_id':r['subject_id'],'name':r['name'],'kind':r['kind']} for r in aliases(store,s) if r['status']=='confirmed'][:100]
        for message in batch:
            message['_aliases']=name_context
            message['_timezone']=cfg.get('dream_timezone','Asia/Shanghai')
        refs=list(dict.fromkeys(m['reply_to_id'] for m in batch if m.get('reply_to_id')))[:12]
        auxiliary=[];context_chars=0
        for source in store.raw_by_ids(s,refs).values():
            if source['message_id'] in {m['message_id'] for m in batch} or store.is_opted_out(s,source['sender_id']):continue
            source=prepare_message(source)
            if source['_deferred']:continue
            if not source['text'] or context_chars+len(source['text'])>6000:continue
            source['reply_to_id']='';auxiliary.append(source);context_chars+=len(source['text'])
        batch[0]['_auxiliary']=auxiliary
        cap=max(1000,int(cfg.get('max_input_tokens_per_run',16000)))
        # Prefer shrinking optional historical context before dropping live evidence.
        # Output coverage also needs room for every message_id in the provider response.
        output_reserve=max(1500,int(cfg.get('estimated_output_tokens_per_run',1500)))
        while batch:
            estimate=estimate_tokens(v3_extract.SYSTEM+v3_extract.build_prompt(s,batch,events,claims))
            coverage_estimate=1200+len(batch)*4
            if estimate<=cap and coverage_estimate<=output_reserve:break
            if estimate>cap and batch[0].get('_auxiliary'):
                batch[0]['_auxiliary'].pop();continue
            if estimate>cap and claims:claims.pop();continue
            if estimate>cap and events:events.pop();continue
            if len(batch)==1 and replay_job_id and len(batch[0]['text'])>100 and not batch[0]['_content'].get('xml'):
                batch[0].setdefault('_fragment_start',0)
                batch[0].setdefault('_fragment_total',len(batch[0]['text']))
                batch[0]['text']=batch[0]['text'][:len(batch[0]['text'])//2]
                batch[0]['_fragment_end']=batch[0]['_fragment_start']+len(batch[0]['text'])
                continue
            batch.pop()
            end=max([cursor]+[r['id'] for r in batch]+[r['id'] for r in skipped
                        if not batch or r['id']<batch[-1]['id']])
        if not batch:
            return {'status':'oversize','reason':'First message plus existing context exceeds token budget'}
        # Keep skipped rows only inside the committed prefix, and do not skip later rows.
        last_message=batch[-1]['id']
        end=last_message  # selected prefix already includes preceding non-text records
        skipped=[r for r in skipped if r['id']<=end]
        day=day_key()
        limit_calls=max(1,int(cfg.get('max_calls_per_group_day',2)))
        limit_tokens=max(1000,int(cfg.get('max_tokens_per_group_day',40000)))
        expected_output=output_reserve
        if not store.budget_reserve(
            s,estimate,purpose='dream',limit_calls=limit_calls,limit_tokens=limit_tokens,
            day=day,max_usd=float(cfg.get('max_cost_per_group_day_usd',0) or 0),
            input_price=float(cfg.get('input_price_usd_per_million',0) or 0),
            output_price=float(cfg.get('output_price_usd_per_million',0) or 0),
            expected_output=expected_output):
            return {'status':'budget_exceeded',
                    'reason':_usage_reason(store,s,estimate,cfg),'estimated_input':estimate}
        rid=''
        inp=estimate;out=0;estimated=True;failed=True
        try:
            rid=store.begin_run(s,cursor,end,str(cfg['extraction_provider_id']),estimate,dry_run)
            from .budget import observe
            proposal,inp,out,estimated=await v3_extract.analyze(
                scheduler.context,cfg['extraction_provider_id'],s,batch,events,claims,
                usage_observer=lambda i,o,e:observe(store,i,o,e,scope=s))
            if scheduler._stop.is_set() or not cfg.get('enabled') or s.room not in scheduler.allowed_rooms or store.options(s):
                raise RuntimeError('Group disabled during Dreaming')
            if not store.lease_held(s,holder):raise RuntimeError('Lost group Dreaming lease')
            stats=v3_store.commit(
                store,s,rid,cursor,end,batch,proposal,
                dry_run=dry_run,replay_job_id=replay_job_id,skipped=skipped)
            failed=False
            if not dry_run and cfg.get('embedding_enabled'):
                try:
                    from .claim_vectors import index_missing
                    await index_missing(store,s,cfg)
                except Exception as vector_error:
                    logger.warning('[wechat_memory] claim index failure: %s',type(vector_error).__name__)
            if hint:
                with store.transaction():
                    store.db.execute(
                        """UPDATE v3_batch_hints SET max_messages=MIN(500,max_messages*2),
                           updated_at=? WHERE platform=? AND owner=? AND room=?""",
                        (now(),*v3_store.key(s)))
            store.finish_run(rid,'preview' if dry_run else 'committed',stats['claims'],
                             actual_in=inp if not estimated else None,
                             actual_out=out if not estimated else None,
                             output_est=out if estimated else 0)
            return {'status':'preview' if dry_run else 'committed',
                    'run_id':rid,'cursor':end,'changed':stats['claims'],
                    'topics':stats['events'],'observations':stats['claims'],
                    'details':stats,'estimated_input':estimate,
                    'input_tokens':inp,'output_tokens':out,'usage_estimated':estimated}
        except Exception as exc:
            # Model-side incomplete/invalid output shrinks the next batch; source cursor
            # remains unchanged. Do not treat database/authorization errors as shrink cues.
            retryable=isinstance(exc,v3_extract.CoverageError) and (len(batch)>1 or bool(replay_job_id and len(batch)==1 and '_fragment_start' in batch[0] and len(batch[0]['text'])>100))
            if retryable and len(batch)==1 and replay_job_id:
                with store.transaction():
                    store.db.execute('''INSERT INTO fragment_progress(job_id,raw_id,offset,chunk_chars) VALUES(?,?,?,?)
                      ON CONFLICT(job_id,raw_id) DO UPDATE SET chunk_chars=excluded.chunk_chars''',
                      (replay_job_id,batch[0]['id'],batch[0]['_fragment_start'],max(100,len(batch[0]['text'])//2)))
            status_code=getattr(exc,'status_code',None)
            transient=isinstance(exc,(TimeoutError,ConnectionError)) or status_code in (429,502,503,504)
            if retryable:
                # Both preview and apply must retry smaller, without advancing either cursor.
                with store.transaction():
                    store.db.execute(
                        """INSERT INTO v3_batch_hints(platform,owner,room,max_messages,updated_at)
                           VALUES(?,?,?,?,?)
                           ON CONFLICT(platform,owner,room) DO UPDATE SET
                            max_messages=MIN(max_messages,excluded.max_messages),
                            updated_at=excluded.updated_at""",
                        (*v3_store.key(s),max(1,len(batch)//2),now()))
            from .errors import user_error
            explanation=user_error(exc)
            store.finish_run(rid,'failed',error=explanation)
            if not replay_job_id:
                store.fail_scope(s,explanation,
                                 max(120,int(cfg.get('retry_delay_seconds',600))))
            logger.exception('[wechat_memory] V3 run failed room=%s',s.room)
            return {'status':'retryable' if retryable else 'transient' if transient else 'failed',
                    'run_id':rid,'reason':explanation}
        finally:
            store.budget_finish(s,estimate,inp,out,purpose='dream',
                                estimated_usage=estimated,error=failed,day=day)
    finally:
        store.release(s,holder)
