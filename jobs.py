"""Scheduler-owned task lifetime and persisted history diagnostics."""
import asyncio
import time


class TaskCoordinator:
    def __init__(self,scheduler):
        self.scheduler=scheduler;self.store=scheduler.store
        self.active=True;self.tasks=set();self.replay_tasks={}

    def spawn(self,coro):
        task=asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self._finished)
        return {'scheduled':True,'running_tasks':len(self.tasks)}

    def _finished(self,task):
        self.tasks.discard(task)
        if not task.cancelled():
            exc=task.exception()
            if exc:
                from astrbot.api import logger
                logger.error('[wechat_memory] background task failed: %s',type(exc).__name__)

    async def close(self):
        self.active=False
        tasks=tuple(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    def start_replay(self,s,job_id):
        current=self.replay_tasks.get(job_id)
        if current and not current.done():
            return {'scheduled':False,'job_id':job_id}
        task=asyncio.create_task(self._replay_worker(s,job_id))
        self.replay_tasks[job_id]=task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda finished:self.replay_tasks.pop(job_id,None)
                               if self.replay_tasks.get(job_id) is finished else None)
        return {'scheduled':True,'job_id':job_id}

    async def _replay_worker(self,s,job_id):
        try:
            coverage_retries=0
            transient_retries=0
            while self.active:
                job=self.store.replay_job(s,job_id)
                if not job or job['status']!='running':return
                if job['next_raw_id']>job['end_raw_id']:
                    self.store.replay_status(s,job_id,'completed')
                    return
                with self.store.transaction():
                    if job.get('actor')=='automatic_deferred':
                        ledger=self.store.db.execute('SELECT auto_attempts FROM analysis_ledger WHERE platform=? AND owner=? AND room=? AND raw_id=?',(*self.store._key(s),job['start_raw_id'])).fetchone()
                        if ledger and ledger['auto_attempts']>=2:
                            self.store.db.execute("UPDATE reanalysis_jobs SET status='paused',blocked_reason='auto_attempt_limit',last_error='每条消息最多自动尝试两次' WHERE id=?",(job_id,))
                            return
                        self.store.db.execute('UPDATE analysis_ledger SET auto_attempts=auto_attempts+1,last_attempt=? WHERE platform=? AND owner=? AND room=? AND raw_id=?',(int(time.time()),*self.store._key(s),job['start_raw_id']))
                    self.store.db.execute('UPDATE reanalysis_jobs SET attempts=attempts+1 WHERE id=?',(job_id,))
                result=await self.scheduler.run_scope(
                    s,force=True,dry_run=job['mode']=='preview',replay_job_id=job_id)
                current=self.store.replay_job(s,job_id)
                if not current or current['status']!='running':
                    return
                if result['status'] in ('committed','preview','empty'):
                    coverage_retries=0
                    transient_retries=0
                    continue
                if result['status']=='transient':
                    attempts=int(current.get('attempts',0))
                    delay=min(3600,60*2**min(attempts,6))
                    with self.store.transaction():
                        self.store.db.execute('UPDATE reanalysis_jobs SET next_attempt=?,blocked_reason=? WHERE id=?',(__import__('time').time()+delay,'upstream_transient',job_id))
                    transient_retries+=1
                    if transient_retries>2:
                        self.store.replay_status(s,job_id,'paused','上游暂时不可用；自动重试已达上限')
                        return
                    until=time.time()+delay
                    while self.active and time.time()<until:
                        current=self.store.replay_job(s,job_id)
                        if not current or current['status']!='running':return
                        await asyncio.sleep(min(5,max(0,until-time.time())))
                    continue
                if result['status']=='retryable':
                    coverage_retries+=1
                    if coverage_retries<=2:
                        # Source cursor is unchanged; V3 will reread a smaller batch.
                        # Each attempt consumes real model quota, so retries are bounded.
                        continue
                    self.store.replay_status(
                        s,job_id,'paused',
                        str(result.get('reason','覆盖校验不完整'))+'；本轮自动缩批重试已达上限')
                    return
                with self.store.transaction():
                    self.store.db.execute('UPDATE reanalysis_jobs SET blocked_reason=? WHERE id=?',(result['status'],job_id))
                status='paused' if result['status'] in ('busy','budget_exceeded','oversize','insufficient') else 'failed'
                self.store.replay_status(s,job_id,status,str(result.get('reason',result['status'])))
                return
        except asyncio.CancelledError:
            job=self.store.replay_job(s,job_id)
            if job and job['status']=='running':
                self.store.replay_status(s,job_id,'paused','interrupted_shutdown')
            raise
        except Exception as exc:
            from astrbot.api import logger
            logger.exception('[wechat_memory] history worker failed: %s',type(exc).__name__)
            # A concurrent pause/cancel may already have changed the job state.
            job=self.store.replay_job(s,job_id)
            if job and job['status']=='running':
                self.store.replay_status(s,job_id,'failed',type(exc).__name__)
