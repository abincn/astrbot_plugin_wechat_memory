"""Single-process bounded scheduler; no tools and no outbound WeChat actions."""
from __future__ import annotations

import asyncio
from astrbot.api import logger

from .extraction import analyze


class MemoryScheduler:
    def __init__(self, store, context, config, allowed_rooms: set[str]):
        self.store=store
        self.context=context
        self.config=config
        self.allowed_rooms=allowed_rooms
        self._stop=asyncio.Event()
        self._wake=asyncio.Event()
        self._task=None

    def wake(self):
        self._wake.set()

    def start(self):
        if self._task is None:
            self._task=asyncio.create_task(self._run(),name='wechat_memory_scheduler')

    async def close(self):
        self._stop.set()
        self._wake.set()
        if self._task:
            # Allow a running model request to finish; bounded by its own timeout.
            try:
                await asyncio.wait_for(self._task,timeout=75)
            except asyncio.TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task,return_exceptions=True)

    async def _run(self):
        try:
            while not self._stop.is_set():
                if self.config.get('enabled') and self.config.get('extraction_enabled') and self.config.get('extraction_provider_id'):
                    try:
                        await self.run_once()
                    except Exception as exc:
                        logger.warning(f'[wechat_memory] Scheduler cycle error: {type(exc).__name__}')
                try:
                    await asyncio.wait_for(self._wake.wait(),timeout=max(30,min(3600,int(self.config.get('scan_seconds',300)))))
                    self._wake.clear()
                except asyncio.TimeoutError:
                    pass
        finally:
            self._stop.set()

    async def run_once(self):
        cfg=self.config
        for scope in self.store.due_scopes():
            if self._stop.is_set():
                break
            if scope.platform!=cfg.get('platform_id','wechat_pc_hook') or scope.room not in self.allowed_rooms:
                continue
            if not self.store.ready(scope, max(3,int(cfg.get('message_threshold',20))),
                max(5,int(cfg.get('max_wait_minutes',60))), max(1,int(cfg.get('min_batch_messages',3)))):
                continue
            batch_id,rows=self.store.reserve(scope,
                max(1,min(50,int(cfg.get('max_batch_messages',50)))),
                max(1600,min(12000,int(cfg.get('max_batch_chars',6000)))),
                max(1,min(100,int(cfg.get('max_daily_calls_per_group',12)))))
            if not rows:
                continue
            try:
                items,input_tokens,output_tokens=await analyze(self.context,cfg['extraction_provider_id'],scope,rows)
                stats=self.store.complete(scope,batch_id,items,rows)
                self.store.record_usage(scope,input_tokens,output_tokens)
                logger.info(f'[wechat_memory] Extraction room={scope.room} messages={len(rows)} added={stats["added"]} review={stats["review"]} rejected={stats["rejected"]}')
            except asyncio.CancelledError:
                self.store.failed(scope,batch_id,'cancelled',120)
                raise
            except Exception as exc:
                delay=min(3600,120 * 2**min(4,max(r.get('attempts',0) for r in rows) if rows else 1))
                self.store.failed(scope,batch_id,type(exc).__name__,delay)
                logger.warning(f'[wechat_memory] Extraction failed room={scope.room} type={type(exc).__name__}; retry later')
        # Only processed queue entries are cleaned, never pending rows.
        self.store.cleanup(int(cfg.get('processed_retention_days',30)))
