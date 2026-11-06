"""Dashboard-authenticated, scope-checked management of Dreaming and memory."""
from __future__ import annotations

import asyncio
import json
import sqlite3

from astrbot.api.web import error_response, json_response, request

from .backfill import import_inbox
from .evolution import resolve_candidate
from .identity import Scope
from .media import analyze_pending


class MemoryPanel:
    def __init__(self,context,store,config,allowed_rooms,scheduler):
        self.context=context
        self.store=store;self.config=config;self.allowed_rooms=allowed_rooms
        from .exports import ExportSessions
        self.exports=ExportSessions(store)
        from .rooms import RoomNames
        self.room_names=RoomNames(context,store)
        from .member_names import MemberNames
        self.member_names=MemberNames(context,store)
        self.scheduler=scheduler;self.active=True;self.tasks=set();self.replay_tasks={}
        context.register_web_api('/astrbot_plugin_wechat_memory/manage',self.handle,
                                 ['POST'],'微信群长期记忆与 Dreaming 工作台')

    async def close(self):
        await self.scheduler.jobs.close()
        self.exports.close()

    def _background(self,coro):
        return self.scheduler.jobs.spawn(coro)

    def _start_replay(self,s,job_id):
        return self.scheduler.jobs.start_replay(s,job_id)

    async def handle(self):
        allowed={x.strip() for x in self.config.get('admin_users',[]) if isinstance(x,str) and x.strip()}
        actor=getattr(request,'username',None)
        if not self.active or not actor or str(actor) not in allowed:
            return error_response('请使用授权的 AstrBot Dashboard 账户登录。',status_code=403)
        payload={}
        try:
            raw=await request.body()
            if len(raw)>65536:return error_response('请求内容超限',status_code=413)
            parsed=json.loads(raw)
            if not isinstance(parsed,dict):raise ValueError('Invalid body')
            payload=parsed
            op=payload.get('operation')
            if op=='scopes':
                if payload.get('refresh') is True:self.member_names.checked.clear()
                return json_response({'scopes':await self.room_names.list(self.config.get('platform_id','wechat_pc_hook'),self.allowed_rooms,refresh=payload.get('refresh') is True)},headers={'Cache-Control':'no-store'})
            if op=='deferred_replay':
                s=self._scope(payload)
                if not self.config.get('enabled') or not self.config.get('reanalysis_enabled'):
                    raise ValueError('请启用插件及历史重分析')
                raw_id=int(payload.get('raw_id',0))
                with self.store._lock:
                    row=self.store.db.execute("SELECT r.* FROM analysis_ledger l JOIN raw_messages r ON r.id=l.raw_id WHERE l.platform=? AND l.owner=? AND l.room=? AND l.raw_id=? AND l.result='deferred'",(*self.store._key(s),raw_id)).fetchone()
                if not row:raise ValueError('延期项目已变化，请刷新')
                if not row['text']:raise ValueError('缺少可分析文本，请先补录原文或处理媒体')
                job=self.store.replay_create(s,str(actor),raw_id,raw_id,str(payload.get('mode','preview')))
                self.store.replay_status(s,job['id'],'running')
                return json_response({**job,**self._start_replay(s,job['id'])})
            if op in ('run_now','dry_run','media_run'):
                s=self._scope(payload)
                if not self.config.get('enabled'):raise ValueError('Plugin disabled')
                if op=='media_run':
                    if not self.config.get('media_enabled'):raise ValueError('Media not enabled')
                    return json_response(self._background(analyze_pending(self.store,s,self.config,limit=3)))
                return json_response(self._background(self.scheduler.run_scope(s,dry_run=op=='dry_run',force=True)))
            if op=='v3_profile_consolidate':
                if not self.config.get('dreaming_v3_enabled',True):
                    raise ValueError('Dreaming V3 not enabled')
                s=self._scope(payload)
                from .v3_profile import consolidate
                return json_response(self._background(consolidate(
                    self.scheduler,s,str(payload.get('subject_id',''))[:256])))
            if op=='backfill':
                if not self.config.get('enabled') or not self.config.get('backfill_enabled'):
                    raise ValueError('请先在插件配置中启用 backfill_enabled')
                s=self._scope(payload)
                path=str(self.config.get('hook_inbox_db','') or '').strip()
                adapter=self.context.get_platform_inst(s.platform)
                if adapter is None or not callable(getattr(adapter,'get_task_scope',None)):
                    if not path:
                        raise ValueError('未找到运行中的微信 Hook 连接器，无法自动获取 Inbox 路径')
                else:
                    info=adapter.get_task_scope()
                    if not isinstance(info,dict) or info.get('owner') != s.owner:
                        raise PermissionError('微信 Hook 账号与所选账号不一致')
                    if not path:
                        path=str(info.get('inbox_db','') or '').strip()
                if not path:
                    raise ValueError('Hook 连接器没有提供 Inbox 路径')
                result=await asyncio.to_thread(import_inbox,self.store,path,s.platform,{s.room},
                    max_rows=min(2000,max(1,int(payload.get('limit',500)))),
                    selected_owner=s.owner)
                result['source']='hook-inbox'
                return json_response(result,headers={'Cache-Control':'no-store'})
            if op in ('replay_start','replay_resume','replay_pause','replay_cancel'):
                if not (self.config.get('dreaming_v3_enabled',True) or self.config.get('dreaming_v2_enabled')) or not self.config.get('reanalysis_enabled'):
                    raise ValueError('请先启用历史重分析功能')
                s=self._scope(payload)
                if op=='replay_start':
                    if payload.get('confirmed') is not True:
                        raise ValueError('请先确认历史重分析范围及费用')
                    job=self.store.replay_create(
                        s,str(actor),int(payload.get('from_id',1)),
                        int(payload.get('to_id',0)),str(payload.get('mode','preview')))
                    self.store.replay_status(s,job['id'],'running')
                    return json_response({**job,**self._start_replay(s,job['id'])})
                job_id=str(payload.get('job_id',''))
                job=self.store.replay_job(s,job_id)
                if not job:raise ValueError('找不到当前群的历史重分析任务')
                if op=='replay_resume':
                    from .budget import status as budget_status
                    budget=budget_status(self.store,s,self.config)
                    if budget['remaining_calls']<=0 or budget['remaining_tokens']<max(1500,int(self.config.get('estimated_output_tokens_per_run',1500))):
                        raise ValueError('额度不足；下次重置 '+budget['next_reset_local'])
                    if job['status'] in ('completed','cancelled'):
                        raise ValueError('历史重分析任务已结束')
                    if int(job.get('next_attempt',0))>__import__('time').time():
                        raise ValueError('任务仍在退避等待，请稍后恢复')
                    self.store.replay_status(s,job_id,'running')
                    return json_response(self._start_replay(s,job_id))
                self.store.replay_status(s,job_id,'paused' if op=='replay_pause' else 'cancelled')
                return json_response({'job_id':job_id,'status':'paused' if op=='replay_pause' else 'cancelled'})
            data=await asyncio.to_thread(self._execute,payload,str(actor))
            if not str(op or '').startswith('export'):
                data=await self.member_names.enrich(self._scope(payload),data)
            from .errors import localize
            return json_response(localize(data),headers={'Cache-Control':'no-store'})
        except PermissionError:
            return error_response('无权访问此账号/群的数据。',status_code=403)
        except (ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            from .errors import user_error
            if payload.get('operation')=='backfill':
                return error_response('Hook Inbox 补录失败：'+user_error(exc)[:400],status_code=400)
            if str(payload.get('operation','')).startswith('replay_'):
                return error_response('历史重分析：'+user_error(exc)[:200],status_code=400)
            return error_response('操作参数、来源或版本无效，请查看群配置及记录状态。',status_code=400)
        except OSError as exc:
            if payload.get('operation')=='backfill':
                return error_response('Hook Inbox 文件不可用：'+str(exc)[:400],status_code=503)
            return error_response('数据文件不可用。',status_code=503)

    def _scope(self,payload):
        platform=payload.get('platform');owner=payload.get('owner');room=payload.get('room')
        if not all(isinstance(x,str) for x in (platform,owner,room)):
            raise ValueError('Missing scope')
        s=Scope(platform,owner,room)
        if platform!=self.config.get('platform_id','wechat_pc_hook') or room not in self.allowed_rooms:
            raise PermissionError('Not a managed room')
        if s not in self.store.scopes():raise PermissionError('Unknown room/account')
        return s

    def _execute(self,payload,actor):
        op=payload.get('operation')
        if op=='scopes':
            return {'scopes':[dict(platform=s.platform,owner=s.owner,room=s.room) for s in self.store.scopes()
                   if s.platform==self.config.get('platform_id','wechat_pc_hook') and s.room in self.allowed_rooms]}
        s=self._scope(payload)
        if op=='export_prepare':return self.exports.prepare(s,actor)
        if op=='export_next':return self.exports.next(s,actor,str(payload.get('token','')),payload.get('position'))
        if op=='export_cancel':
            self.exports.cancel(s,actor,str(payload.get('token','')))
            return {'cancelled':True}
        from .pages import TABLES, read
        if payload.get('paged') and (op in TABLES or op in ('v3_profiles','deferred_list')):
            return read(self.store,s,op,payload)
        if op=='raw_disclosure':
            ident=int(payload.get('id',0));value=payload.get('disclosure')
            if value not in ('unreviewed','public'):raise ValueError('Invalid disclosure policy')
            with self.store.transaction():
                r=self.store.db.execute('SELECT sender_id FROM raw_messages WHERE id=? AND platform=? AND owner=? AND room=?',(ident,*self.store._key(s))).fetchone()
                if not r or self.store.is_opted_out(s,r['sender_id']):raise ValueError('Source unavailable')
                self.store.db.execute('UPDATE raw_messages SET disclosure=? WHERE id=?',(value,ident))
            return {'saved':True}
        if op=='claim_links':
            with self.store._lock:
                return {'items':[dict(r) for r in self.store.db.execute('''SELECT l.*,c.content target_content,c.revision target_revision
                    FROM claim_links l JOIN memory_claims c ON c.id=l.target_id JOIN memory_claims a ON a.id=l.source_id
                    WHERE a.platform=? AND a.owner=? AND a.room=? AND c.platform=a.platform AND c.owner=a.owner AND c.room=a.room AND l.source_id=?''',(*self.store._key(s),str(payload.get('id',''))))]}
        if op=='claim_link_resolve':
            with self.store.transaction():
                ids=[str(payload.get('id','')),str(payload.get('target_id',''))]
                rows=[self.store.db.execute('SELECT * FROM memory_claims WHERE id=? AND platform=? AND owner=? AND room=?',(ident,*self.store._key(s))).fetchone() for ident in ids]
                if any(r is None for r in rows) or rows[0]['revision']!=payload.get('revision') or rows[1]['revision']!=payload.get('target_revision'):raise ValueError('Claim revision changed')
                if ids[0]==ids[1] or not self.store.db.execute("SELECT 1 FROM claim_links WHERE source_id=? AND target_id=? AND status='pending'",ids).fetchone():raise ValueError('Conflict no longer pending')
                if payload.get('action') not in ('keep_both','supersede_target','merge_into_target'):raise ValueError('Invalid conflict action')
                if payload['action']=='merge_into_target':
                    source,target=rows
                    if (source['subject_type'],source['subject_id'],source['claim_type'],source['event_id'])!=(target['subject_type'],target['subject_id'],target['claim_type'],target['event_id']):raise ValueError('Cannot merge different identities or events')
                    if target['status'] not in ('active','candidate','review'):raise ValueError('Target no longer usable')
                    for r,reason in ((source,'merged_into:'+target['id']),(target,'evidence_merged_from:'+source['id'])):
                        self.store.db.execute('INSERT INTO memory_claim_versions(claim_id,revision,previous_content,previous_status,actor,reason,occurred_at) VALUES(?,?,?,?,?,?,strftime("%s","now"))',(r['id'],r['revision'],r['content'],r['status'],actor,reason))
                    self.store.db.execute('INSERT OR IGNORE INTO claim_evidence(claim_id,raw_id,relation) SELECT ?,raw_id,relation FROM claim_evidence WHERE claim_id=?',(target['id'],source['id']))
                    self.store.db.execute('INSERT OR IGNORE INTO claim_spans SELECT ?,raw_id,start_offset,end_offset FROM claim_spans WHERE claim_id=?',(target['id'],source['id']))
                    self.store.db.execute("UPDATE memory_claims SET status='superseded',revision=revision+1 WHERE id=?",(source['id'],))
                    self.store.db.execute('UPDATE memory_claims SET revision=revision+1,last_supported=MAX(last_supported,?) WHERE id=?',(source['last_supported'],target['id']))
                if payload['action']=='supersede_target':
                    r=rows[1]
                    if (r['subject_type'],r['subject_id'],r['claim_type'])!=(rows[0]['subject_type'],rows[0]['subject_id'],rows[0]['claim_type']):raise ValueError('Cannot replace another identity')
                    self.store.db.execute('INSERT INTO memory_claim_versions(claim_id,revision,previous_content,previous_status,actor,reason,occurred_at) VALUES(?,?,?,?,?,?,strftime("%s","now"))',(r['id'],r['revision'],r['content'],r['status'],actor,'superseded_by:'+ids[0]))
                    self.store.db.execute("UPDATE memory_claims SET status='superseded',revision=revision+1 WHERE id=?",(r['id'],))
                self.store.db.execute("UPDATE claim_links SET status=? WHERE source_id=? AND target_id=?",(payload['action'],*ids))
            return {'saved':True}
        if op=='alias_sources':
            with self.store._lock:
                return {'items':[dict(r) for r in self.store.db.execute('''SELECT DISTINCT r.message_id,r.sender_id,r.message_at,r.text,a.source
                  FROM member_aliases a JOIN raw_messages r ON r.platform=a.platform AND r.owner=a.owner AND r.room=a.room
                  AND (r.message_id=a.source_message_id OR EXISTS(SELECT 1 FROM alias_sources x WHERE x.alias_id=a.id AND x.raw_id=r.id))
                  WHERE a.id=? AND a.platform=? AND a.owner=? AND a.room=? ORDER BY r.message_at LIMIT 50''',(str(payload.get('id','')),*self.store._key(s)))]}
        if op=='member_aliases':
            from .people import aliases
            return {'items':aliases(self.store,s,str(payload.get('subject_id','')))}
        if op=='alias_add':
            from .people import observe
            subject=str(payload.get('subject_id',''))
            with self.store._lock:
                known=self.store.db.execute('SELECT 1 FROM raw_messages WHERE platform=? AND owner=? AND room=? AND sender_id=?',(*self.store._key(s),subject)).fetchone()
            if not known:raise ValueError('Unknown member')
            observe(self.store,s,subject,payload.get('name'),'admin','admin:'+actor)
            return {'saved':True}
        if op=='alias_status':
            from .people import key
            with self.store.transaction():
                row=self.store.db.execute('SELECT * FROM member_aliases WHERE id=? AND platform=? AND owner=? AND room=?',(payload.get('id'),*key(s))).fetchone()
                if not row or row['revision']!=payload.get('revision'):raise ValueError('Alias revision changed')
                if self.store.is_opted_out(s,row['subject_id']):raise ValueError('Member opted out')
                status=payload.get('status')
                if status not in ('confirmed','retracted'):raise ValueError('Invalid alias status')
                self.store.db.execute("INSERT INTO alias_versions VALUES(?,?,?,?,strftime('%s','now'))",(row['id'],row['revision'],row['status'],actor))
                self.store.db.execute('UPDATE member_aliases SET status=?,revision=revision+1 WHERE id=?',(status,row['id']))
            return {'saved':True}
        if op in ('profile_classify_preview','profile_classify_apply'):
            from .people import classify
            with self.store.transaction():
                if op.endswith('preview'):
                    rows=[dict(r) for r in self.store.db.execute('SELECT * FROM memory_claims WHERE platform=? AND owner=? AND room=?',self.store._key(s))]
                    return {'items':[dict(id=r['id'],revision=r['revision'],content=r['content'],purpose=classify(r)[0],reason=classify(r)[1]) for r in rows]}
                for item in payload.get('items',[]):
                    row=self.store.db.execute('SELECT * FROM memory_claims WHERE id=? AND platform=? AND owner=? AND room=?',(item['id'],*self.store._key(s))).fetchone()
                    if not row or row['revision']!=item['revision']:raise ValueError('Claim revision changed')
                    purpose=item.get('purpose')
                    if purpose not in ('long_term','recent','event','uncertain'):raise ValueError('Invalid purpose')
                    self.store.db.execute('INSERT INTO memory_claim_versions (claim_id,revision,previous_content,previous_status,actor,reason,occurred_at) VALUES(?,?,?,?,?,?,strftime("%s","now"))',(row['id'],row['revision'],row['content'],row['status'],actor,'classification:'+row['purpose']))
                    self.store.db.execute('UPDATE memory_claims SET purpose=?,purpose_reason=?,revision=revision+1 WHERE id=?',(purpose,'管理员分类确认',row['id']))
            return {'saved':True}
        if op=='budget_status':
            from .budget import status
            return status(self.store,s,self.config)
        if op=='overview':
            stats=self.store.stats(s)
            from .budget import status
            stats['budget']=status(self.store,s,self.config)
            pin=float(self.config.get('input_price_usd_per_million',0) or 0)
            pout=float(self.config.get('output_price_usd_per_million',0) or 0)
            if pin>0 and pout>0:
                usage=stats['today_usage']
                stats['today_cost_usd_estimate']=round((usage['input_tokens']*pin+
                    usage['output_tokens']*pout)/1_000_000,6)
            if self.config.get('dreaming_v3_enabled',True):
                from .v3_store import stats as v3_stats
                stats['v3']=v3_stats(self.store,s)
            return {'stats':stats,'paused':self.store.options(s),'quality':self.store.data_quality(s)}
        if op=='list':
            return {'items':self.store.list_memories(s,status=payload.get('status',''),kind=payload.get('kind',''),
                query=str(payload.get('query',''))[:100])}
        if op=='sources':
            return {'sources':self.store.sources(s,str(payload.get('id',''))[:64])}
        if op=='history':return {'items':self.store.history(s)}
        if op in ('v3_claim_edit','v3_claim_approve','v3_claim_retract','v3_claim_versions'):
            from . import v3_store
            ident=str(payload.get('id',''))[:64]
            if op=='v3_claim_versions':return {'items':v3_store.claim_versions(self.store,s,ident)}
            return v3_store.revise_claim(
                self.store,s,ident,int(payload.get('revision',-1)),
                op.removeprefix('v3_claim_'),actor,
                str(payload.get('content','')) if op=='v3_claim_edit' else '')
        if op in ('v3_events','v3_claims','v3_profiles','v3_coverage','v3_sources','v3_event_sources'):
            from . import v3_store
            if op=='v3_events':return {'items':v3_store.events(self.store,s)}
            if op=='v3_claims':return {'items':v3_store.claims(self.store,s,event_id=str(payload.get('event_id',''))[:64],subject=str(payload.get('subject_id',''))[:256])}
            if op=='v3_profiles':
                from .people import members
                return {'items':members(self.store,s)}
            if op=='v3_coverage':return {'items':v3_store.coverage(self.store,s)}
            if op=='v3_event_sources':return {'items':v3_store.event_sources(self.store,s,str(payload.get('id',''))[:64])}
            return {'items':v3_store.sources(self.store,s,str(payload.get('id',''))[:64])}
        if op=='replay_prepare':
            if not self.config.get('reanalysis_enabled'):
                raise ValueError('请在插件配置中启用历史重分析')
            return self.store.replay_prepare(s,int(payload.get('from_id',1)),
                                                 int(payload.get('to_id',0)))
        if op=='replay_jobs':return {'items':self.store.replay_jobs(s)}
        if op=='topics':return {'items':self.store.list_topics(s)}
        if op=='observations':return {'items':self.store.list_observations(s)}
        if op=='runs':
            rows=self.store.list_runs(s)
            for row in rows:
                detail=self.store.v2_run_detail(s,row['id'])
                if detail:row['v2_detail']=detail
                with self.store._lock:
                    v3=self.store.db.execute(
                        "SELECT * FROM v3_run_details WHERE run_id=?",(row['id'],)).fetchone()
                if v3:row['v3_detail']=dict(v3)
            return {'items':rows}
        if op=='candidates':return {'items':self.store.candidates(s,status=str(payload.get('status',''))[:20])}
        if op=='retrieval_audit':return {'items':self.store.retrieval_audit(s)}
        if op=='versions':
            mid=payload.get('id','')
            with self.store._lock:
                if not self.store.db.execute('''SELECT 1 FROM memories WHERE id=? AND platform=? AND owner=? AND room=?''',
                    (mid,*self.store._key(s))).fetchone():raise PermissionError('Outside group')
                rows=self.store.db.execute('''SELECT revision,action,previous_content,previous_status,actor,run_id,occurred_at
                    FROM memory_versions WHERE memory_id=? ORDER BY id DESC LIMIT 80''',(mid,)).fetchall()
            return {'items':[dict(x) for x in rows]}
        if op=='add':
            return {'id':self.store.upsert_manual(s,content=payload.get('content'),kind=payload.get('kind','fact'),
                subject=payload.get('subject',''),topic=payload.get('topic',''),actor=actor),'saved':True}
        if op in ('approve','edit','disable','delete','rollback'):
            mid=payload.get('id','')
            if not isinstance(mid,str) or len(mid)!=32 or any(c not in '0123456789abcdef' for c in mid):
                raise ValueError('Invalid memory ID')
            if op=='approve' and any(c['status']=='review' for c in self.store.candidates(s,limit=200)
                                     if c['applied_memory_id']==mid):
                raise ValueError('Use candidate review to approve this Dreaming proposal')
            expected=payload.get('revision')
            return {'saved':self.store.revise(s,mid,action=op,actor=actor,
                                              content=payload.get('content',''),expected_revision=expected)}
        if op in ('candidate_approve','candidate_reject'):
            return {'saved':resolve_candidate(self.store,s,str(payload.get('id','')),
                'approve' if op=='candidate_approve' else 'reject',actor)}
        if op in ('pause','resume'):
            return {'paused':self.store.options(s,paused=(op=='pause'))}
        if op=='export':
            tables=('memories','memory_events','memory_claims','analysis_ledger','raw_messages',
                    'member_aliases','profile_progress')
            with self.store.transaction():
                result={table:[dict(r) for r in self.store.db.execute(
                    'SELECT * FROM '+table+' WHERE platform=? AND owner=? AND room=?',self.store._key(s))] for table in tables}
                for table,parent,column in (('claim_evidence','memory_claims','claim_id'),
                    ('memory_claim_versions','memory_claims','claim_id'),
                    ('event_evidence','memory_events','event_id'),
                    ('memory_sources','memories','memory_id'),('memory_versions','memories','memory_id')):
                    result[table]=[dict(r) for r in self.store.db.execute(
                        'SELECT x.* FROM '+table+' x JOIN '+parent+' p ON p.id=x.'+column+
                        ' WHERE p.platform=? AND p.owner=? AND p.room=?',self.store._key(s))]
            return {'scope':dict(platform=s.platform,owner=s.owner,room=s.room),
                    'data':result,'counts':{name:len(rows) for name,rows in result.items()},
                    'note':'当前群完整管理导出，含受限内容；不是数据库备份。'}
        raise ValueError('Unsupported action')
