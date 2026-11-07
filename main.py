"""Native AstrBot WeChat memory: passive capture, opt-in Dreaming and scoped recall."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart

from .config_layout import normalize
from .identity import Scope, get_scope, plain_text, private_owner, trusted_sender, valid_room
from .panel import MemoryPanel
from .retrieval import recall
from .scheduler import MemoryScheduler
from .storage import MemoryStore


class Main(Star):
    def __init__(self,context:Context,config:AstrBotConfig):
        super().__init__(context)
        config=normalize(config)
        self.cfg=config
        self.allowed_rooms={r for r in config.get('allowed_rooms',[]) if valid_room(r)}
        self.platform_id=str(config.get('platform_id','wechat_pc_hook'))
        self.store=MemoryStore(StarTools.get_data_dir(self.name)/'memory.sqlite3')
        self.scheduler=MemoryScheduler(self.store,context,config,self.allowed_rooms)
        self.panel=MemoryPanel(context,self.store,config,self.allowed_rooms,self.scheduler)
        logger.info('[wechat_memory] ready (enabled=%s; groups=%d)',bool(config.get('enabled')),len(self.allowed_rooms))

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        self.scheduler.start()

    async def terminate(self):
        self.panel.active=False
        await self.scheduler.close()
        await self.panel.close()
        self.store.close()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect(self,event:AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('collection_enabled',True):return
        scope=get_scope(event,self.platform_id,self.allowed_rooms)
        sender=trusted_sender(event) if scope else None
        if not sender:return
        message=event.message_obj
        mid=getattr(message,'message_id','')
        stamp=getattr(message,'timestamp',0)
        timestamp=int(stamp) if isinstance(stamp,(int,float)) and 0<stamp<time.time()+86400 else int(time.time())
        content=plain_text(message,max_chars=1500000)
        raw=getattr(message,'raw_message',None)
        metadata=raw if isinstance(raw,dict) else {}
        media_path=metadata.get('media_path','') if not content else ''
        media_kind=metadata.get('media_kind','') if not content else ''
        reply_to=metadata.get('reply_to_id','')
        identity_info={'mentions':[str(getattr(c,'qq','')) for c in getattr(message,'message',[]) if type(c).__name__=='At'][:50]}
        kind='text' if content else 'media'
        if not content and not media_path and not media_kind:
            # Do not fabricate media text from a missing attachment.
            return
        urgent=bool(content and any(w in content for w in ('请记住','记住这个','以后按这个来','把这个记下来')))
        try:
            inserted=self.store.enqueue(scope,mid,sender,content,timestamp,urgent,
                kind=kind,reply_to_id=reply_to,media_path=media_path,media_kind=media_kind,identity_metadata=identity_info)
            if inserted:
                from .people import observe
                observe(self.store,scope,sender,getattr(getattr(message,'sender',None),'nickname',''),source_message_id=mid)
                card=getattr(getattr(message,'sender',None),'card','')
                if isinstance(card,str):observe(self.store,scope,sender,card,'card',source_message_id=mid)
                import re
                alias=re.fullmatch(r'大家(?:以后)?叫我(.{2,20}?)(?:就行|就好)[。！! ]*',content)
                if alias:observe(self.store,scope,sender,alias.group(1),'self','self_statement',source_message_id=mid)
            if inserted and urgent:self.scheduler.wake()
        except Exception as exc:
            logger.warning('[wechat_memory] archive failure: %s',type(exc).__name__)

    @filter.on_llm_request()
    async def inject(self,event:AstrMessageEvent,req):
        if not self.cfg.get('enabled') or not self.cfg.get('retrieval_enabled',True):return
        scope=get_scope(event,self.platform_id,self.allowed_rooms)
        sender=trusted_sender(event) if scope else None
        if not sender:return
        question=str(event.message_str).strip()[:1500]
        if not question:return
        try:
            context=await recall(self.store,scope,sender,question,config=self.cfg)
            if context:
                parts=list(req.extra_user_content_parts or [])
                parts.append(TextPart(text=context).mark_as_temp())
                req.extra_user_content_parts=parts
        except Exception as exc:
            logger.warning('[wechat_memory] retrieval failure: %s',type(exc).__name__)

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def user_commands(self,event:AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('enable_private_profile_commands',True):return
        ids=private_owner(event,self.platform_id)
        if not ids:return
        text=str(event.message_str).strip()
        if text!='/wm' and not text.startswith('/wm '):return
        event.stop_event()
        try:result=self._command(*ids,text)
        except (ValueError,PermissionError) as exc:result='操作未执行：'+str(exc)[:120]
        except Exception as exc:
            logger.warning('[wechat_memory] private command failure: %s',type(exc).__name__)
            result='记忆服务暂不可用。'
        await event.send(event.plain_result(result))

    def _command(self,owner,user,command):
        help_text=('私聊记忆命令：\n/wm profile add 内容\n/wm profile list\n'
                   '/wm profile delete 记忆ID\n/wm share allow 群ID@chatroom\n'
                   '/wm share revoke 群ID@chatroom\n/wm share list\n'
                   '/wm group off 群ID@chatroom（删除群内个人画像并退出自动画像）\n'
                   '/wm group on 群ID@chatroom（恢复允许群内画像）\n'
                   '/wm group erase 群ID@chatroom（删除本人在该群的原始消息及画像）\n'
                   '/wm privacy off（撤销全部跨群授权）')
        words=command.split(' ',3)
        if len(words)==1 or words[1]=='help':return help_text
        if words[1]=='profile' and len(words)>2:
            if words[2]=='list':
                rows=self.store.global_list(self.platform_id,owner,user)
                return '你的全局画像：\n'+'\n'.join(x['id']+' '+x['content'] for x in rows) if rows else '暂无全局画像。'
            if words[2]=='add' and len(words)==4:
                mid=self.store.global_add(self.platform_id,owner,user,words[3])
                return '已添加，仅在本人明确授权的群里用于本人问答。记录ID：'+mid
            if words[2]=='delete' and len(words)==4:
                return '已删除。' if self.store.global_delete(self.platform_id,owner,user,words[3].strip()) else '记录不存在。'
        if words[1]=='share' and len(words)>2:
            if words[2]=='list':
                rooms=self.store.list_permissions(self.platform_id,owner,user)
                return '已授权的群：\n'+'\n'.join(rooms) if rooms else '没有授权群。'
            if words[2] in ('allow','revoke') and len(words)==4:
                room=words[3].strip()
                if room not in self.allowed_rooms:raise ValueError('该群未在 allowed_rooms')
                self.store.set_permission(self.platform_id,owner,user,room,words[2]=='allow')
                return '已修改本人跨群画像使用权限。'
        if words[1]=='group' and len(words)==4:
            op=words[2];room=words[3].strip()
            if room not in self.allowed_rooms:raise ValueError('群未获授权')
            scope=Scope(self.platform_id,owner,room)
            if op in ('on','off'):
                self.store.opt_out(scope,user,op=='off')
                return '已更新本人群内画像设置。'
            if op=='erase':
                stats=self.store.erase_user_from_room(scope,user)
                return f'本人在该群的数据已按请求清除：{stats}。以后默认退出群内自动画像。'
        if words[1]=='privacy' and len(words)>2 and words[2]=='off':
            n=self.store.revoke_all(self.platform_id,owner,user)
            return f'已撤销 {n} 个群的个人全局画像授权。'
        return help_text
