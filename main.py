"""AstrBot-native WeChat group memory, without Hermes or task plugin dependencies."""
from __future__ import annotations

import asyncio
import time

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from astrbot.core.agent.message import TextPart

from .identity import Scope, get_scope, plain_text, private_owner, trusted_sender, valid_room
from .panel import MemoryPanel
from .retrieval import recall
from .scheduler import MemoryScheduler
from .storage import MemoryStore


class Main(Star):
    def __init__(self, context:Context, config:AstrBotConfig):
        super().__init__(context)
        self.cfg=config
        self.allowed_rooms={r for r in config.get('allowed_rooms',[]) if valid_room(r)}
        self.platform_id=str(config.get('platform_id','wechat_pc_hook'))
        self.store=MemoryStore(StarTools.get_data_dir(self.name)/'memory.sqlite3')
        self.scheduler=MemoryScheduler(self.store,context,config,self.allowed_rooms)
        self.panel=MemoryPanel(context,self.store,config,self.allowed_rooms)
        logger.info('[wechat_memory] Initialized; enabled=%s rooms=%s',bool(config.get('enabled')),len(self.allowed_rooms))

    @filter.on_astrbot_loaded()
    async def start(self):
        self.scheduler.start()

    async def terminate(self):
        self.panel.active=False
        await self.scheduler.close()
        self.store.close()
        logger.info('[wechat_memory] Stopped')

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect_group_message(self,event:AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('collection_enabled',True):
            return
        scope=get_scope(event,self.platform_id,self.allowed_rooms)
        if scope is None:
            return
        sender=trusted_sender(event)
        if not sender:
            return
        text=plain_text(event.message_obj)
        if not text:
            return
        mid=getattr(event.message_obj,'message_id','')
        stamp=getattr(event.message_obj,'timestamp',0)
        timestamp=int(stamp) if isinstance(stamp,(int,float)) and stamp>0 else int(time.time())
        urgent=any(k in text for k in ('记住这个','请记住','沉淀记忆','以后按这个来','把这个记下来'))
        try:
            inserted=self.store.enqueue(scope,mid,sender,text,timestamp,urgent)
            if inserted and urgent:
                self.scheduler.wake()
        except Exception as exc:
            # Never block the normal AstrBot reply pipeline on a memory error.
            logger.warning(f'[wechat_memory] Collector unavailable: {type(exc).__name__}')

    @filter.on_llm_request()
    async def inject(self,event:AstrMessageEvent,req):
        if not self.cfg.get('enabled') or not self.cfg.get('retrieval_enabled',True):
            return
        scope=get_scope(event,self.platform_id,self.allowed_rooms)
        if scope is None:
            return
        sender=trusted_sender(event)
        if not sender:
            return
        query=event.message_str.strip()[:1500]
        if not query:
            return
        try:
            context=recall(self.store,scope,sender,query,
                max_items=int(self.cfg.get('max_retrieved_memories',6)),
                max_chars=int(self.cfg.get('max_context_chars',2400)))
            if context:
                parts=list(req.extra_user_content_parts or [])
                parts.append(TextPart(text=context).mark_as_temp())
                req.extra_user_content_parts=parts
        except Exception as exc:
            logger.warning(f'[wechat_memory] Recall unavailable: {type(exc).__name__}')

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def own_profile_commands(self,event:AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('enable_private_profile_commands',True):
            return
        ids=private_owner(event,self.platform_id)
        if ids is None:
            return
        text=event.message_str.strip()
        if text!='/wm' and not text.startswith('/wm '):
            return
        event.stop_event()
        owner,user=ids
        try:
            message=self._handle_self_command(owner,user,text)
        except (ValueError,TypeError) as exc:
            message='记忆操作未执行：'+str(exc)[:100]
        except Exception as exc:
            logger.warning(f'[wechat_memory] Private profile command failed: {type(exc).__name__}')
            message='记忆服务暂不可用。'
        # Explicit direct-message command only. Honors Hook connector's normal send setting.
        await event.send(event.plain_result(message))

    def _handle_self_command(self,owner:str,user:str,text:str)->str:
        help_text=('私聊记忆命令：\n'
            '/wm profile add 内容（创建自己的全局画像）\n'
            '/wm profile list（查看自己的全局画像）\n'
            '/wm profile delete 记录ID（删除自己的全局画像）\n'
            '/wm share allow 群ID@chatroom（允许在该群给自己使用画像）\n'
            '/wm share revoke 群ID@chatroom（撤销授权）\n'
            '/wm share list（查看授权群）\n'
            '/wm privacy off（全部撤销授权）\n'
            '不会从其他群自动复制个人画像；群内问答只为本人查询已授权画像。')
        cmd=text.split(' ',3)
        if len(cmd)==1 or cmd[1]=='help':return help_text
        if cmd[1]=='profile':
            if len(cmd)<3: return help_text
            if cmd[2]=='list':
                entries=self.store.global_list(self.platform_id,owner,user)
                return '你的全局画像：\n'+'\n'.join(x['id']+' '+x['content'] for x in entries) if entries else '暂无全局个人画像。'
            if cmd[2]=='add' and len(cmd)==4:
                mid=self.store.global_add(self.platform_id,owner,user,cmd[3],'')
                return '已保存你的个人画像，记录ID：'+mid+'。只有你明确授权的群才能在你本人提问时使用。'
            if cmd[2]=='delete' and len(cmd)==4:
                return '已删除。' if self.store.global_delete(self.platform_id,owner,user,cmd[3].strip()) else '记录不存在。'
        if cmd[1]=='share':
            if len(cmd)<3: return help_text
            if cmd[2]=='list':
                rooms=self.store.list_permissions(self.platform_id,owner,user)
                return '已授权群：\n'+'\n'.join(rooms) if rooms else '尚未授权任何群。'
            if cmd[2] in ('allow','revoke') and len(cmd)==4:
                room=cmd[3].strip()
                if not valid_room(room) or room not in self.allowed_rooms:
                    raise ValueError('群ID无效，或未列入插件 allowed_rooms。')
                allow=cmd[2]=='allow'
                self.store.set_permission(self.platform_id,owner,user,room,allow)
                return '已授权该群仅对你本人使用画像。' if allow else '该群授权已撤销。'
        if cmd[1]=='privacy' and len(cmd)>=3 and cmd[2]=='off':
            n=self.store.revoke_all(self.platform_id,owner,user)
            return f'已撤销 {n} 个群的跨群个人画像授权。画像本身未删除。'
        return help_text
