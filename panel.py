"""Dashboard-authenticated and username-allowlisted memory management API."""
from __future__ import annotations

import asyncio
import json

from astrbot.api.web import error_response, json_response, request

from .identity import Scope


class MemoryPanel:
    def __init__(self, context, store, config, allowed_rooms: set[str]):
        self.store=store
        self.config=config
        self.allowed_rooms=allowed_rooms
        self.active=True
        context.register_web_api('/astrbot_plugin_wechat_memory/manage', self.handle,
                                 ['POST'],'微信长期记忆工作台')

    async def handle(self):
        allowed={x.strip() for x in self.config.get('admin_users',[]) if isinstance(x,str) and x.strip()}
        actor=request.username
        if not self.active or not actor or str(actor) not in allowed:
            return error_response('无记忆管理权限。请将 Dashboard 用户名加入插件 admin_users 配置。',status_code=403)
        try:
            raw=await request.body()
            if len(raw)>32768:
                return error_response('请求体过大',status_code=413)
            payload=json.loads(raw)
            if not isinstance(payload,dict):
                raise ValueError('JSON object required')
            result=await asyncio.to_thread(self._execute,payload,str(actor))
            return json_response(result,headers={'Cache-Control':'no-store'})
        except PermissionError:
            return error_response('没有权限操作该微信群。',status_code=403)
        except (ValueError,TypeError,KeyError):
            return error_response('操作参数无效，请核对群、内容和记录状态。',status_code=400)
        except (OSError,RuntimeError):
            return error_response('记忆存储当前无法处理请求。',status_code=503)

    def _scope(self,payload:dict)->Scope:
        platform=payload.get('platform')
        owner=payload.get('owner')
        room=payload.get('room')
        if not all(isinstance(x,str) for x in (platform,owner,room)):
            raise ValueError('Missing scope')
        scope=Scope(platform,owner,room)
        if platform!=self.config.get('platform_id','wechat_pc_hook') or room not in self.allowed_rooms:
            raise PermissionError('Outside managed rooms')
        # Only trust scopes already registered by actual platform messages.
        if scope not in self.store.scopes():
            raise PermissionError('Unknown account/group')
        return scope

    def _execute(self,payload:dict,actor:str)->dict:
        op=payload.get('operation')
        if op=='scopes':
            scopes=[{'platform':s.platform,'owner':s.owner,'room':s.room}
                    for s in self.store.scopes()
                    if s.platform==self.config.get('platform_id','wechat_pc_hook') and s.room in self.allowed_rooms]
            return {'scopes':scopes}
        scope=self._scope(payload)
        if op=='overview':
            return {'stats':self.store.stats(scope),'paused':self.store.options(scope)}
        if op=='list':
            return {'items':self.store.list_memories(scope,status=payload.get('status',''),
                     kind=payload.get('kind',''),query=str(payload.get('query',''))[:100])}
        if op=='sources':
            mid=payload.get('id','')
            if not isinstance(mid,str) or len(mid)>64:raise ValueError('Invalid id')
            return {'sources':self.store.sources(scope,mid)}
        if op=='add':
            mid=self.store.upsert_manual(scope,content=payload.get('content'),
                kind=payload.get('kind','fact'),subject=payload.get('subject',''),
                topic=payload.get('topic',''),actor=actor)
            return {'id':mid,'saved':True}
        if op in ('approve','edit','disable','delete'):
            mid=payload.get('id')
            if not isinstance(mid,str) or len(mid)!=32 or any(c not in '0123456789abcdef' for c in mid):
                raise ValueError('Invalid ID')
            result=self.store.revise(scope,mid,action=op,actor=actor,content=payload.get('content',''))
            return {'saved':result}
        if op in ('pause','resume'):
            return {'paused':self.store.options(scope,paused=(op=='pause'))}
        if op=='export':
            rows=self.store.list_memories(scope,limit=200)
            for row in rows:
                row['sources']=self.store.sources(scope,row['id'])
            return {'scope':{'platform':scope.platform,'owner':scope.owner,'room':scope.room},'items':rows,
                    'note':'Latest 200 group-scoped records only; no cross-group personal data.'}
        raise ValueError('Unknown operation')
