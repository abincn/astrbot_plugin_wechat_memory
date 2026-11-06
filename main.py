"""AstrBot entrypoint: normalize host events, then delegate feature work."""
import time
import re
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Star, StarTools, Context
from astrbot.core.agent.message import TextPart
from .application import Application
from .adapters.events import get_scope, plain_text, private_owner, trusted_sender
from .adapters.provider import Provider
from .adapters.panel import Panel
from .features.commands import Commands
from .core.types import Failure


class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        # Grouped editor values override historic flat values without rewriting the user's config.
        self.cfg = dict(config)
        for value in config.values():
            if isinstance(value, dict):
                self.cfg.update(value)
        self.platform = self.cfg.get('platform_id', 'wechat_pc_hook')
        self.rooms = set(self.cfg.get('allowed_rooms', []))
        self.app = Application(StarTools.get_data_dir(self.name), self.cfg, Provider(context).generate)
        self.panel = Panel(context, self.app)
        self.commands = Commands(self.app.archive, self.app.memory, self.app.access, self.app.privacy, self.cfg)
        removed = [k for k in ('media_enabled','embedding_enabled','dreaming_v2_enabled') if self.cfg.get(k)]
        if removed:
            logger.warning('微信记忆新内核已移除以下配置对应能力：%s', ', '.join(removed))

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        self.app.tasks.start()

    async def terminate(self):
        self.panel.active = False
        await self.app.close()

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect(self, event: AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('collection_enabled', True):
            return
        scope = get_scope(event, self.platform, self.rooms)
        sender = trusted_sender(event) if scope else None
        if sender is None:
            return
        message = event.message_obj
        mid = str(message.message_id)
        text = plain_text(message)
        raw = getattr(message, 'raw_message', None)
        metadata = raw if isinstance(raw, dict) else {}
        mentions = [str(c.qq) for c in message.message if type(c).__name__=='At']
        self.app.archive.append(scope, mid, sender, text, int(message.timestamp or time.time()),
                                'text' if text else 'media', str(metadata.get('reply_to_id', '')), mentions)
        person = message.sender
        self.app.identity.observe(scope, sender, getattr(person, 'nickname', ''))
        self.app.identity.observe(scope, sender, getattr(person, 'card', ''), 'card')
        alias = re.fullmatch(r'大家(?:以后)?叫我(.{2,20}?)(?:就行|就好)[。！! ]*', text)
        if alias:
            self.app.identity.observe(scope, sender, alias.group(1), 'self', 'self_statement')

    @filter.on_llm_request()
    async def inject(self, event: AstrMessageEvent, req):
        if not self.cfg.get('enabled') or not self.cfg.get('retrieval_enabled', True):
            return
        scope = get_scope(event, self.platform, self.rooms)
        sender = trusted_sender(event) if scope else None
        if sender is None or not event.message_str.strip():
            return
        context = self.app.retrieval.recall(scope, sender, event.message_str[:1500])
        if context:
            req.extra_user_content_parts = list(req.extra_user_content_parts or []) + [TextPart(text=context).mark_as_temp()]

    @filter.event_message_type(filter.EventMessageType.PRIVATE_MESSAGE)
    async def user_commands(self, event: AstrMessageEvent):
        if not self.cfg.get('enabled') or not self.cfg.get('enable_private_profile_commands', True):
            return
        ids = private_owner(event, self.platform)
        text = event.message_str.strip()
        if ids is None or not (text=='/wm' or text.startswith('/wm ')):
            return
        event.stop_event()
        try:
            result = self.commands.execute(self.platform, *ids, text)
        except Failure as exc:
            result = str(exc)
        await event.send(event.plain_result(result))
