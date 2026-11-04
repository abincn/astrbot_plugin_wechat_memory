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
        self.scheduler=scheduler;self.active=True;self.tasks=set()
        context.register_web_api('/astrbot_plugin_wechat_memory/manage',self.handle,
                                 ['POST'],'微信群长期记忆与 Dreaming 工作台')

    async def close(self):
        if self.tasks:
            for t in tuple(self.tasks):t.cancel()
            await asyncio.gather(*self.tasks,return_exceptions=True)

    def _background(self,coro):
        task=asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'scheduled':True,'running_tasks':len(self.tasks)}

    async def handle(self):
        allowed={x.strip() for x in self.config.get('admin_users',[]) if isinstance(x,str) and x.strip()}
        actor=getattr(request,'username',None)
        if not self.active or not actor or str(actor) not in allowed:
            return error_response('请使用授权的 AstrBot Dashboard 账户登录。',status_code=403)
        try:
            raw=await request.body()
            if len(raw)>65536:return error_response('请求内容超限',status_code=413)
            payload=json.loads(raw)
            if not isinstance(payload,dict):raise ValueError('Invalid body')
            op=payload.get('operation')
            if op in ('run_now','dry_run','media_run'):
                s=self._scope(payload)
                if not self.config.get('enabled'):raise ValueError('Plugin disabled')
                if op=='media_run':
                    if not self.config.get('media_enabled'):raise ValueError('Media not enabled')
                    return json_response(self._background(analyze_pending(self.store,s,self.config,limit=3)))
                return json_response(self._background(self.scheduler.run_scope(s,dry_run=op=='dry_run',force=True)))
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
            data=await asyncio.to_thread(self._execute,payload,str(actor))
            return json_response(data,headers={'Cache-Control':'no-store'})
        except PermissionError:
            return error_response('无权访问此账号/群的数据。',status_code=403)
        except (ValueError,TypeError,KeyError,sqlite3.Error) as exc:
            if payload.get('operation')=='backfill':
                return error_response('Hook Inbox 补录失败：'+str(exc)[:400],status_code=400)
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
        if op=='overview':
            stats=self.store.stats(s)
            pin=float(self.config.get('input_price_usd_per_million',0) or 0)
            pout=float(self.config.get('output_price_usd_per_million',0) or 0)
            if pin>0 and pout>0:
                usage=stats['today_usage']
                stats['today_cost_usd_estimate']=round((usage['input_tokens']*pin+
                    usage['output_tokens']*pout)/1_000_000,6)
            return {'stats':stats,'paused':self.store.options(s),'quality':self.store.data_quality(s)}
        if op=='list':
            return {'items':self.store.list_memories(s,status=payload.get('status',''),kind=payload.get('kind',''),
                query=str(payload.get('query',''))[:100])}
        if op=='sources':
            return {'sources':self.store.sources(s,str(payload.get('id',''))[:64])}
        if op=='history':return {'items':self.store.history(s)}
        if op=='topics':return {'items':self.store.list_topics(s)}
        if op=='observations':return {'items':self.store.list_observations(s)}
        if op=='runs':
            rows=self.store.list_runs(s)
            for row in rows:
                detail=self.store.v2_run_detail(s,row['id'])
                if detail:row['v2_detail']=detail
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
            rows=self.store.list_memories(s,limit=200)
            for row in rows:row['sources']=self.store.sources(s,row['id'])
            return {'scope':dict(platform=s.platform,owner=s.owner,room=s.room),'items':rows,
                    'note':'Current group only; no global private profile or cross-group content.'}
        raise ValueError('Unsupported action')
