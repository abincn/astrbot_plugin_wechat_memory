"""Independent member profile consolidation across existing messages and claims."""
from __future__ import annotations

import uuid
from astrbot.api import logger

from . import v3_extract,v3_store
from .storage import day_key, now
from .extraction import estimate_tokens
from .message_content import prepare_message
from .budget import observe

PROFILE_TYPES = ('person_fact','preference','behavior_pattern','relation_claim','third_party_statement')
PROFILE_MAX_CLAIMS = 12
PROFILE_INSTRUCTIONS = f"""本批是单个成员的长期画像巩固，仍使用上述完整 V3 JSON 协议。
events 和 aliases 必须为空列表，所有 claim 的 event_ref 必须为空字符串。
只允许 subject_type=member，subject_id 必须等于下方目标成员ID。
claim_type 只允许 {'、'.join(PROFILE_TYPES)}。
最多输出 {PROFILE_MAX_CLAIMS} 条；没有稳定证据时 claims=[]。同义主张复用已有内容、ID 和 revision。
"""

async def consolidate(scheduler,s,member=''):
    store=scheduler.store;cfg=scheduler.config
    if not cfg.get('enabled') or not cfg.get('extraction_provider_id'):
        return {'status':'disabled'}
    if not member:
        with store._lock:
            row=store.db.execute(
                """SELECT subject_id FROM consolidation_queue
                   WHERE platform=? AND owner=? AND room=? AND status='pending'
                   ORDER BY updated_at ASC LIMIT 1""",v3_store.key(s)).fetchone()
            member=row['subject_id'] if row else ''
    if not member:return {'status':'empty','reason':'No pending member'}
    if s.platform!=cfg.get('platform_id','wechat_pc_hook') or s.room not in scheduler.allowed_rooms or store.options(s) or store.is_opted_out(s,member):return {'status':'denied'}
    holder=uuid.uuid4().hex
    if not store.acquire(s,holder,ttl=150):return {'status':'busy'}
    try:
        cap=max(1000,min(16000,int(cfg.get('max_input_tokens_per_run',16000))))
        with store._lock:
            progress=store.db.execute('SELECT cursor,offset FROM profile_progress WHERE platform=? AND owner=? AND room=? AND subject_id=?',(*v3_store.key(s),member)).fetchone()
            cursor=int(progress['cursor']) if progress else 0
            fragment_offset=int(progress['offset']) if progress else 0
        with store._lock:
            # Include the subject's actual messages AND genuine third-party attribution in the same group.
            rows=[dict(r) for r in store.db.execute(
                """SELECT DISTINCT r.id,r.message_id,r.sender_id,r.message_at,r.text,r.reply_to_id,r.kind,r.source,r.identity_json
                   FROM raw_messages r
                   WHERE r.platform=? AND r.owner=? AND r.room=?
                     AND r.text!='' AND r.id>?
                     AND (r.sender_id=? OR r.id IN (
                        SELECT e.raw_id FROM claim_evidence e JOIN memory_claims c ON c.id=e.claim_id
                        WHERE c.platform=? AND c.owner=? AND c.room=?
                          AND c.subject_type='member' AND c.subject_id=?))
                   ORDER BY r.id ASC LIMIT 180""",
                (*v3_store.key(s),cursor,member,*v3_store.key(s),member))]
        if not rows:
            with store.transaction():
                store.db.execute("UPDATE consolidation_queue SET status='completed' WHERE platform=? AND owner=? AND room=? AND subject_id=?",(*v3_store.key(s),member))
            return {'status':'empty','reason':'No pending member evidence'}
        rows=[prepare_message(row) for row in rows]
        skipped=[row for row in rows if row['_deferred']]
        prefix=cursor
        for row in rows:
            if not row['_deferred']:break
            prefix=row['id']
        if skipped:
            with store.transaction():
                if store.is_opted_out(s,member):return {'status':'denied','reason':'成员已退出画像'}
                for row in skipped:
                    store.db.execute('''INSERT INTO analysis_ledger
                        (platform,owner,room,raw_id,pipeline_version,result,reason,updated_at)
                        VALUES(?,?,?,?,3,'deferred',?,?) ON CONFLICT(platform,owner,room,raw_id,pipeline_version)
                        DO UPDATE SET result=excluded.result,reason=excluded.reason,updated_at=excluded.updated_at''',
                        (*v3_store.key(s),row['id'],row['_deferred'],now()))
                if prefix>cursor:
                    store.db.execute('''INSERT INTO profile_progress(platform,owner,room,subject_id,cursor,offset) VALUES(?,?,?,?,?,0)
                        ON CONFLICT(platform,owner,room,subject_id) DO UPDATE SET cursor=excluded.cursor,offset=0''',
                        (*v3_store.key(s),member,prefix))
                    fragment_offset=0
        rows=[row for row in rows if not row['_deferred']]
        if not rows:return {'status':'empty','reason':'本批只有待解析附件或 XML，未调用模型','skipped':len(skipped)}
        existing=[r for r in v3_store.profiles(store,s,limit=100,member=member)
                  if not r['event_id'] and r['claim_type'] in PROFILE_TYPES]
        # Ensure every available message id in prompt is counted toward coverage.
        for message in rows:message['_timezone']=cfg.get('dream_timezone','Asia/Shanghai')
        instructions=PROFILE_INSTRUCTIONS+'\n目标成员ID：'+member
        system=v3_extract.system_prompt(cfg,'v3_profile_prompt',instructions)
        if fragment_offset and rows:
            if rows[0]['_content'].get('xml'):
                return {'status':'oversize','reason':'XML 存在旧分片偏移，需重新整理，未调用模型'}
            rows=rows[:1]
            rows[0]['_fragment_start']=fragment_offset
            rows[0]['_fragment_total']=len(rows[0]['text'])
            rows[0]['text']=rows[0]['text'][fragment_offset:]
            rows[0]['_fragment_end']=fragment_offset+len(rows[0]['text'])
        prompt,message_refs=v3_extract.build_prompt(rows,[],existing)
        while rows and estimate_tokens(system+prompt)>cap:
            if existing:
                existing.pop()
            elif len(rows)==1 and len(rows[0]['text'])>100 and not rows[0]['_content'].get('xml'):
                rows[0].setdefault('_fragment_start',0)
                rows[0].setdefault('_fragment_total',len(rows[0]['text']))
                rows[0]['text']=rows[0]['text'][:len(rows[0]['text'])//2]
                rows[0]['_fragment_end']=rows[0]['_fragment_start']+len(rows[0]['text'])
            else:
                rows.pop()  # only commit a contiguous evidence prefix
            prompt,message_refs=v3_extract.build_prompt(rows,[],existing)
        if not rows:return {'status':'oversize','reason':'单条证据及 V3 协议超过画像输入上限，未调用模型'}
        estimated_in=estimate_tokens(system+prompt)
        day=day_key()
        if not store.budget_reserve(
            s,estimated_in,purpose='dream',
            limit_calls=max(1,int(cfg.get('max_calls_per_group_day',2))),
            limit_tokens=max(1000,int(cfg.get('max_tokens_per_group_day',40000))),
            day=day,expected_output=max(0,int(cfg.get('estimated_output_tokens_per_run',1500))),
            max_usd=float(cfg.get('max_cost_per_group_day_usd',0) or 0),
            input_price=float(cfg.get('input_price_usd_per_million',0) or 0),
            output_price=float(cfg.get('output_price_usd_per_million',0) or 0)):
            from .v3_pipeline import _usage_reason
            return {'status':'budget_exceeded','reason':_usage_reason(store,s,estimated_in,cfg)}
        rid=''
        actual_in=estimated_in;actual_out=0;estimated=True;failed=True
        stage='登记执行记录'

        def record_usage(inp,out,is_estimated):
            nonlocal actual_in,actual_out,estimated,stage
            actual_in,actual_out,estimated=inp,out,is_estimated
            stage='校验模型输出'
            observe(store,inp,out,is_estimated,scope=s)

        try:
            rid=store.begin_run(s,0,0,str(cfg['extraction_provider_id']),estimated_in,False)
            stage='请求模型'
            payload,_,_,_=await v3_extract.analyze(scheduler.context,cfg['extraction_provider_id'],rows,[],existing,
                                                    prompt=prompt,system=system,message_refs=message_refs,usage_observer=record_usage)
            if payload['events'] or payload['aliases'] or len(payload['claims'])>PROFILE_MAX_CLAIMS:
                raise ValueError(f"画像巩固只允许最多 {PROFILE_MAX_CLAIMS} 条主张；模型返回 {len(payload['claims'])} 条主张、{len(payload['events'])} 个事件、{len(payload['aliases'])} 个称呼")
            if any(c['subject_type']!='member' or c['subject_id']!=member or c['claim_type'] not in PROFILE_TYPES for c in payload['claims']):
                raise ValueError('模型返回了非目标成员或不属于画像的主张类型')
            stage='提交画像'
            if scheduler._stop.is_set() or not cfg.get('enabled') or s.room not in scheduler.allowed_rooms or store.options(s) or store.is_opted_out(s,member):
                raise PermissionError('Consolidation disabled before commit')
            if not store.lease_held(s,holder):raise RuntimeError('Consolidation lease lost')
            stats=v3_store.commit(store,s,rid,0,0,rows,payload,consolidation_member=member)
            store.finish_run(rid,'committed',stats['claims'],
                             actual_in=actual_in if not estimated else None,
                             actual_out=actual_out if not estimated else None,
                             output_est=actual_out if estimated else 0)
            failed=False
            return {'status':'committed','run_id':rid,'member':member,**stats}
        except Exception as exc:
            from .errors import user_error
            kind=type(exc).__name__
            status=getattr(exc,'status_code',None)
            if stage=='请求模型':
                detail=f'提供方返回 HTTP {status}' if type(status) is int else user_error(kind)
            else:
                detail=user_error(exc,keep_detail=True)
            reason=f'画像巩固失败 · {stage}（{kind}）：{detail}'
            logger.warning('[wechat_memory] run_id=%s %s',rid,reason)
            store.finish_run(rid,'failed',error=reason,
                             actual_in=actual_in if not estimated else None,
                             actual_out=actual_out if not estimated else None,
                             output_est=actual_out if estimated else 0)
            return {'status':'failed','reason':reason,'run_id':rid}
        finally:
            store.budget_finish(s,estimated_in,actual_in,actual_out,purpose='dream',
                                estimated_usage=estimated,error=failed,day=day)
    finally:
        store.release(s,holder)
