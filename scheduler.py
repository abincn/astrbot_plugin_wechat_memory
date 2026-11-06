"""Bounded, per-room Dreaming scheduler with persistent cursor and SQLite lease."""
from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from zoneinfo import ZoneInfo

from astrbot.api import logger
from .extraction import (analyze, analyze_v2, build_input, build_input_v2,
                         estimate_tokens, SYSTEM, SYSTEM_V2)
from .evolution import apply
from .storage import now,day_key


class MemoryScheduler:
    def __init__(self,store,context,config,allowed_rooms:set[str]):
        self.store=store;self.context=context;self.config=config;self.allowed_rooms=allowed_rooms
        self._task=None;self._stop=asyncio.Event();self._wake=asyncio.Event()
        self._running=set()
        from .jobs import TaskCoordinator
        self.jobs=TaskCoordinator(self)

    def start(self):
        if self._task is None:self._task=asyncio.create_task(self._run(),name='wechat_memory_dreaming')

    def wake(self):self._wake.set()

    async def close(self):
        self._stop.set();self._wake.set()
        await self.jobs.close()
        if self._task:
            try:await asyncio.wait_for(self._task,timeout=85)
            except asyncio.TimeoutError:
                self._task.cancel();await asyncio.gather(self._task,return_exceptions=True)

    def _stale_backlog(self,s):
        days=max(1,int(self.config.get('low_activity_max_wait_days',7)))
        with self.store._lock:
            row=self.store.db.execute('''SELECT MIN(r.received_at) FROM raw_messages r
              JOIN room_state t ON t.platform=r.platform AND t.owner=r.owner AND t.room=r.room
              WHERE r.platform=? AND r.owner=? AND r.room=? AND r.id>t.dream_cursor''',self.store._key(s)).fetchone()
        return bool(row and row[0] and now()-row[0]>=days*86400)

    def _scheduled_now(self,s):
        cfg=self.config
        try:zone=ZoneInfo(str(cfg.get('dream_timezone','Asia/Shanghai')))
        except (KeyError,ValueError):zone=dt.timezone.utc
        current=dt.datetime.now(zone)
        hour=int(cfg.get('dream_hour',3))
        with self.store._lock:
            urgent=self.store.db.execute('''SELECT 1 FROM raw_messages WHERE platform=? AND owner=? AND room=?
                AND urgent=1 AND id>(SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?) LIMIT 1''',
                (*self.store._key(s),*self.store._key(s))).fetchone()
        if urgent or self._stale_backlog(s):return True
        if current.hour<hour:return False
        with self.store._lock:
            r=self.store.db.execute('''SELECT last_success FROM room_state WHERE platform=? AND owner=? AND room=?''',
                                    self.store._key(s)).fetchone()
        last=int(r['last_success']) if r else 0
        if last and dt.datetime.fromtimestamp(last,zone).date()==current.date():
            if not (cfg.get('dreaming_v3_enabled',True) or cfg.get('dreaming_v2_enabled',False)):
                return False
            # V2 may process additional accumulated batches, bounded by daily budget.
            count,_=self.store.backlog(s)
            if count < max(1,int(cfg.get('min_messages_to_dream',10))):
                if cfg.get('dreaming_v3_enabled',True):
                    with self.store._lock:
                        pending=self.store.db.execute("SELECT 1 FROM consolidation_queue WHERE platform=? AND owner=? AND room=? AND status='pending' LIMIT 1",self.store._key(s)).fetchone()
                    return bool(pending)
                return False
        return True

    async def _run(self):
        while not self._stop.is_set():
            if self.config.get('enabled') and self.config.get('dreaming_enabled') and self.config.get('extraction_provider_id'):
                try:await self.run_once()
                except Exception as exc:logger.warning('[wechat_memory] Dream sweep error: %s',type(exc).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(),timeout=max(30,min(3600,int(self.config.get('scan_seconds',300)))))
                self._wake.clear()
            except asyncio.TimeoutError:pass

    async def run_once(self):
        limit=max(1,min(4,int(self.config.get('max_parallel_groups',2))))
        sem=asyncio.Semaphore(limit)
        async def one(s):
            async with sem:
                if s.platform!=self.config.get('platform_id','wechat_pc_hook') or s.room not in self.allowed_rooms:return
                if self.config.get('deferred_auto_retry') and self.config.get('reanalysis_enabled') and not self.store.options(s):
                    with self.store.transaction():
                        row=self.store.db.execute("""SELECT l.raw_id FROM analysis_ledger l JOIN raw_messages r ON r.id=l.raw_id
                          WHERE l.platform=? AND l.owner=? AND l.room=? AND l.result='deferred'
                          AND l.auto_attempts<2 AND COALESCE(l.last_attempt,0)<? AND r.text!=''
                          AND NOT EXISTS(SELECT 1 FROM reanalysis_jobs j WHERE j.platform=l.platform AND j.owner=l.owner AND j.room=l.room AND j.status='running')
                          ORDER BY l.raw_id LIMIT 1""",(*self.store._key(s),now()-86400)).fetchone()
                        if row:self.store.db.execute('UPDATE analysis_ledger SET last_attempt=? WHERE platform=? AND owner=? AND room=? AND raw_id=?',(now(),*self.store._key(s),row['raw_id']))
                    if row:
                        job=self.store.replay_create(s,'automatic_deferred',row['raw_id'],row['raw_id'],'apply')
                        self.store.replay_status(s,job['id'],'running')
                        self.jobs.start_replay(s,job['id'])
                        return
                if not self._scheduled_now(s):return
                count,_=self.store.backlog(s)
                if count<max(1,int(self.config.get('min_messages_to_dream',10))):
                    with self.store._lock:
                        urgent=self.store.db.execute('''SELECT 1 FROM raw_messages WHERE platform=? AND owner=? AND room=? AND urgent=1
                          AND id>(SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?) LIMIT 1''',
                          (*self.store._key(s),*self.store._key(s))).fetchone()
                    if not urgent and not self._stale_backlog(s):
                        if self.config.get('dreaming_v3_enabled',True):
                            from .v3_profile import consolidate
                            await consolidate(self,s)
                        return
                if self.config.get('media_enabled'):
                    from .media import analyze_pending
                    await analyze_pending(self.store,s,self.config,limit=2)
                await self.run_scope(s,force=bool(count<max(1,int(self.config.get('min_messages_to_dream',10)))))
                if self.config.get('dreaming_v3_enabled',True):
                    from .v3_profile import consolidate
                    await consolidate(self,s)
        await asyncio.gather(*(one(s) for s in self.store.due_scopes()),return_exceptions=False)

    async def run_scope(self,s,*,dry_run=False,force=False,replay_job_id=''):
        if self.config.get('dreaming_v3_enabled',True):
            from .v3_pipeline import run
            return await run(self,s,dry_run=dry_run,force=force,replay_job_id=replay_job_id)
        cfg=self.config
        if not cfg.get('enabled') or not cfg.get('extraction_provider_id'):
            return {'status':'disabled','reason':'No provider configured'}
        if s.platform!=cfg.get('platform_id','wechat_pc_hook') or s.room not in self.allowed_rooms or self.store.options(s):
            return {'status':'denied'}
        holder=uuid.uuid4().hex
        if not self.store.acquire(s,holder,ttl=140):return {'status':'busy'}
        try:
            max_messages=max(1,min(500,int(cfg.get('max_messages_per_run',250))))
            max_chars=max(500,min(120000,int(cfg.get('max_message_chars_per_run',20000))))
            if replay_job_id:
                if not cfg.get('dreaming_v2_enabled',False):
                    return {'status':'disabled','reason':'V2 not enabled'}
                job=self.store.replay_job(s,replay_job_id)
                if not job or job['status']!='running':
                    return {'status':'denied','reason':'History job not running'}
                cursor,batch,end=self.store.replay_batch(
                    s,job['next_raw_id'],job['end_raw_id'],max_messages,max_chars)
            else:
                cursor,batch,end=self.store.pending_batch(
                    s,max_messages=max_messages,max_chars=max_chars)
            if not batch:
                if replay_job_id and end>cursor:
                    self.store.replay_advance_empty(s,replay_job_id,cursor,end)
                elif end>cursor and not dry_run:
                    self.store.advance(s,cursor,end)
                return {'status':'empty','cursor':end}
            if not force and len(batch)<max(1,int(cfg.get('min_messages_to_dream',10))):
                return {'status':'insufficient','messages':len(batch)}
            users={m['sender_id'] for m in batch}
            existing=self.store.get_context_records(s,users,
                limit_per_user=max(1,min(30,int(cfg.get('max_existing_per_member',12)))))
            v2_enabled=bool(cfg.get('dreaming_v2_enabled',False))
            topics,observations=self.store.v2_context(s,users) if v2_enabled else ([],[])
            if v2_enabled:
                soft=max(1,min(100,int(cfg.get('analysis_batch_soft_limit',60))))
                if len(batch)>soft:
                    batch=batch[:soft]
                    end=batch[-1]['id']
            cap=max(1000,int(cfg.get('max_input_tokens_per_run',16000)))
            # Reduce at complete message boundaries; the unprocessed remainder remains in raw archive.
            while batch:
                estimate=estimate_tokens(build_input_v2(s,batch,existing,topics,observations)+SYSTEM_V2) if v2_enabled else estimate_tokens(build_input(s,batch,existing)+SYSTEM)
                if estimate<=cap:break
                batch.pop();end=batch[-1]['id'] if batch else cursor
            if not batch:
                return {'status':'oversize','reason':'First message plus memory snapshot exceeds budget'}
            charge_day=day_key()
            max_calls=max(1,int(cfg.get('max_calls_per_group_day',2)))
            max_daily=max(1000,int(cfg.get('max_tokens_per_group_day',40000)))
            if not self.store.budget_reserve(s,estimate,purpose='dream',limit_calls=max_calls,limit_tokens=max_daily,
                day=charge_day,max_usd=float(cfg.get('max_cost_per_group_day_usd',0) or 0),
                input_price=float(cfg.get('input_price_usd_per_million',0) or 0),
                output_price=float(cfg.get('output_price_usd_per_million',0) or 0),
                expected_output=max(0,int(cfg.get('estimated_output_tokens_per_run',1500)))):
                return {'status':'budget_exceeded','estimated_input':estimate}
            rid=''
            counted_in=estimate;counted_out=0;is_estimated=True;failed=True
            try:
                rid=self.store.begin_run(s,cursor,end,str(cfg['extraction_provider_id']),estimate,dry_run)
                if v2_enabled:
                    v2,est,in_tok,out_tok,is_estimated=await analyze_v2(
                        self.context,cfg['extraction_provider_id'],s,batch,existing,topics,observations)
                    changes=v2['changes']
                else:
                    v2=None
                    changes,est,in_tok,out_tok,is_estimated=await analyze(
                        self.context,cfg['extraction_provider_id'],s,batch,existing)
                counted_in=in_tok;counted_out=out_tok
                if self._stop.is_set() or s.room not in self.allowed_rooms or not cfg.get('enabled') or self.store.options(s):
                    raise PermissionError('Room disabled during evolution')
                if not self.store.lease_held(s,holder):raise RuntimeError('Lost Dreaming lease')
                report=apply(self.store,s,rid,cursor,end,changes,batch,existing,dry_run=dry_run,
                             max_changes=max(1,min(10,int(cfg.get('max_changes_per_run',8)))),
                             v2=v2,existing_topics=topics,existing_observations=observations,
                             replay_job_id=replay_job_id)
                failed=False
                self.store.finish_run(rid,'preview' if dry_run else 'committed',report['changed'],
                                      actual_in=in_tok if not is_estimated else None,
                                      actual_out=out_tok if not is_estimated else None,
                                      output_est=out_tok if is_estimated else 0)
                if not dry_run and cfg.get('embedding_enabled'):
                    try:
                        from .embeddings import index_missing
                        await index_missing(self.store,s,cfg,limit=8)
                    except Exception as embedding_exc:
                        logger.warning('[wechat_memory] embedding index error: %s',type(embedding_exc).__name__)
                report.update({'status':'preview' if dry_run else 'committed','run_id':rid,
                               'estimated_input':estimate,'input_tokens':in_tok,
                               'output_tokens':out_tok,'usage_estimated':is_estimated})
                return report
            except Exception as exc:
                self.store.finish_run(rid,'failed',error=type(exc).__name__)
                if not replay_job_id:
                    self.store.fail_scope(s,type(exc).__name__,max(120,int(cfg.get('retry_delay_seconds',600))))
                logger.warning('[wechat_memory] Dreaming failed room=%s type=%s',s.room,type(exc).__name__)
                return {'status':'failed','run_id':rid,'reason':type(exc).__name__}
            finally:
                self.store.budget_finish(s,estimate,counted_in,counted_out,purpose='dream',
                                         estimated_usage=is_estimated,error=failed,day=charge_day)
        finally:self.store.release(s,holder)
