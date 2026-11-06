import asyncio
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo
from ..core.types import Scope, Failure, ident, now, error_data
from .analysis import SYSTEM, estimate

log = logging.getLogger(__name__)


class Tasks:
    def __init__(self, db, archive, access, memory, analysis, usage, config):
        self.db, self.archive, self.access, self.memory = db, archive, access, memory
        self.analysis, self.usage, self.config = analysis, usage, config
        self.workers = {}
        self.timer = None
        with db.transaction() as tx:
            tx.execute("UPDATE tasks SET status='paused',error=? WHERE status IN ('queued','running')",
                       (json.dumps({'code': 'restart', 'message': '服务已重启，请手动恢复任务'}),))

    def submit(self, scope, kind, member='', preview=False, start=None, end=None):
        self.access.check(scope)
        if kind=='profile' and self.access.opted_out(scope, member):
            raise Failure('opted_out', '该成员已退出画像')
        end_id = self.archive.ceiling(scope) if end is None else end
        with self.db.transaction() as tx:
            room = tx.execute('SELECT * FROM rooms WHERE scope=?', (scope.key,)).fetchone()
            cursor, offset = 0, 0
            if kind=='new':
                cursor = room['cursor']
            elif kind=='profile':
                progress = tx.execute('SELECT * FROM progress WHERE scope=? AND member=?', (scope.key, member)).fetchone()
                if progress:
                    cursor, offset = progress['cursor'], progress['offset']
            if start is not None:
                cursor = start
            task_id = ident()
            tx.execute('''INSERT INTO tasks(id,scope,kind,member,status,preview,cursor,end_id,offset,epoch,created_at,updated_at)
                VALUES(?,?,?,?,'queued',?,?,?,?,?,?,?)''',
                (task_id, scope.key, kind, member, int(preview), cursor, end_id, offset, room['epoch'], now(), now()))
        self.launch(scope, task_id)
        return {'task_id': task_id}

    def launch(self, scope, task_id):
        worker = asyncio.create_task(self.run(scope, task_id))
        self.workers[task_id] = worker
        worker.add_done_callback(lambda _: self.workers.pop(task_id, None))

    def control(self, scope, task_id, action):
        with self.db.transaction() as tx:
            row = tx.execute('SELECT * FROM tasks WHERE scope=? AND id=?', (scope.key, task_id)).fetchone()
            if row is None:
                raise Failure('not_found', '任务不存在于当前群')
            if action=='resume':
                if task_id in self.workers:
                    raise Failure('call_in_flight', '上一次模型调用尚未返回，请稍后恢复，避免重复调用')
                if row['status'] not in ('paused', 'failed'):
                    raise Failure('task_state', '只有暂停或失败任务可以恢复')
                epoch = tx.execute('SELECT epoch FROM rooms WHERE scope=?', (scope.key,)).fetchone()[0]
                if epoch != row['epoch']:
                    raise Failure('task_invalidated', '权限或资料已变化，请创建新任务')
                tx.execute("UPDATE tasks SET status='queued',error='{}',updated_at=? WHERE id=?", (now(), task_id))
            else:
                if row['status'] not in ('queued', 'running', 'paused'):
                    raise Failure('task_state', '任务已结束')
                tx.execute('UPDATE tasks SET status=?,updated_at=? WHERE id=?', ('paused' if action=='pause' else 'cancelled', now(), task_id))
        if action=='resume':
            self.launch(scope, task_id)

    def list(self, scope, cursor='', limit=50):
        rows = self.db.rows("SELECT * FROM tasks WHERE scope=? AND (?='' OR (created_at,id)<(SELECT created_at,id FROM tasks WHERE id=? AND scope=?)) ORDER BY created_at DESC,id DESC LIMIT ?",
                            (scope.key, cursor, cursor, scope.key, limit+1))
        for row in rows:
            row['result'], row['error'] = json.loads(row['result']), json.loads(row['error'])
        return {'items': rows[:limit], 'has_more': len(rows)>limit, 'next_cursor': rows[limit-1]['id'] if len(rows)>limit else None}

    def detail(self, scope, task_id):
        rows = self.db.rows('SELECT * FROM tasks WHERE scope=? AND id=?', (scope.key, task_id))
        if not rows:
            raise Failure('not_found', '任务不存在于当前群')
        row = rows[0]
        row['result'], row['error'] = json.loads(row['result']), json.loads(row['error'])
        row['batches'] = self.db.rows('SELECT * FROM batches WHERE task_id=? ORDER BY at', (task_id,))
        row['calls'] = self.db.rows('SELECT * FROM calls WHERE task_id=? ORDER BY created_at', (task_id,))
        return row

    async def run(self, scope, task_id, limit=None):
        limit = limit or min(500, int(self.config.get('max_messages_per_run', 100)))
        while True:
            call_id = None
            try:
                # The running row is the group lease. Only the owning worker can commit this task.
                while self.db.rows("SELECT 1 FROM tasks WHERE scope=? AND status='running'", (scope.key,)):
                    state = self.db.rows('SELECT status FROM tasks WHERE id=?', (task_id,))[0]['status']
                    if state != 'queued':
                        return
                    await asyncio.sleep(0.5)
                with self.db.transaction() as tx:
                    changed = tx.execute("UPDATE tasks SET status='running' WHERE id=? AND status='queued'", (task_id,)).rowcount
                if not changed:
                    return
                while True:
                    task = self.db.rows('SELECT * FROM tasks WHERE id=?', (task_id,))[0]
                    if task['status'] != 'running':
                        return
                    self.access.check(scope)
                    rows, usable, data, prompt = self.analysis.prepare(scope, task, limit)
                    if not rows:
                        with self.db.transaction() as tx:
                            tx.execute("UPDATE tasks SET status='completed',updated_at=? WHERE id=? AND status='running'", (now(), task_id))
                        return
                    proposal, rejected = {'claims': [], 'events': []}, []
                    if usable:
                        if not self.config.get('extraction_provider_id'):
                            raise Failure('provider_missing', '请先选择分析模型')
                        call_id = self.usage.reserve(scope, task_id, estimate(SYSTEM+prompt))
                        reply = await self.analysis.generate(self.config['extraction_provider_id'], SYSTEM, prompt)
                        # Usage is persisted before JSON or business validation.
                        self.usage.observe(call_id, reply['input'] if reply['input'] is not None else estimate(SYSTEM+prompt),
                                           reply['output'] if reply['output'] is not None else estimate(reply['text']),
                                           reply['input'] is None or reply['output'] is None)
                        if reply['invalid_role']:
                            raise Failure('model_contract', '分析模型返回了工具调用或非助手角色，未提交结果')
                        proposal, rejected = self.analysis.validate(reply['text'], task, data, usable)
                    self.access.check(scope)
                    with self.db.transaction() as tx:
                        current = tx.execute('SELECT status,epoch FROM tasks WHERE id=?', (task_id,)).fetchone()
                        epoch = tx.execute('SELECT epoch FROM rooms WHERE scope=?', (scope.key,)).fetchone()[0]
                        if current['status'] != 'running' or epoch != task['epoch']:
                            return  # Real race: cancel/erase during provider call.
                        last, offset = rows[-1]['id'], 0
                        if usable and usable[-1]['id']==last and usable[-1]['end'] < len(usable[-1]['text']):
                            last, offset = task['cursor'], usable[-1]['end']
                            # Earlier complete messages are safe to checkpoint before the partial final message.
                            if len(rows)>1:
                                last = rows[-2]['id']
                        result = {'changes': 0, 'rejected': rejected, 'message_ids': [r['id'] for r in rows]}
                        if task['preview']:
                            result['proposal'] = proposal
                            tx.execute("UPDATE tasks SET result=?,status='completed',updated_at=? WHERE id=?", (json.dumps(result, ensure_ascii=False), now(), task_id))
                            return
                        spans = {r['id']: (r['start'], r['end']) for r in usable}
                        result['changes'] = self.memory.commit(tx, scope, proposal, spans)
                        for row in rows:
                            if not row['text']:
                                tx.execute('INSERT OR REPLACE INTO deferred(scope,raw_id,reason) VALUES(?,?,?)',
                                           (scope.key, row['id'], '缺少原文，需重新导入' if row['kind']=='missing_original' else '媒体尚未解析'))
                            elif row['id'] in spans:
                                tx.execute('DELETE FROM deferred WHERE scope=? AND raw_id=?', (scope.key, row['id']))
                        for item in rejected:
                            for raw_id in item['sources']:
                                tx.execute('INSERT INTO deferred(scope,raw_id,reason) VALUES(?,?,?) ON CONFLICT(scope,raw_id) DO UPDATE SET reason=excluded.reason',
                                           (scope.key,raw_id,item['reason']))
                        tx.execute('INSERT INTO batches VALUES(?,?,?,?,?,?,?)', (ident(), task_id, call_id, rows[0]['id'], rows[-1]['id'], result['changes'], now()))
                        tx.execute("UPDATE tasks SET cursor=?,offset=?,processed=processed+?,attempts=0,result=?,error='{}',updated_at=? WHERE id=?",
                                   (last, offset, len(rows)-(1 if offset else 0), json.dumps(result, ensure_ascii=False), now(), task_id))
                        if task['kind']=='new':
                            tx.execute('UPDATE rooms SET cursor=? WHERE scope=?', (last, scope.key))
                        if task['kind']=='profile':
                            tx.execute('INSERT OR REPLACE INTO progress VALUES(?,?,?,?)', (scope.key, task['member'], last, offset))
                    call_id = None
            except asyncio.CancelledError:
                if call_id:
                    self.usage.fail(call_id, '服务关闭时调用中断，用量待核对')
                with self.db.transaction() as tx:
                    tx.execute("UPDATE tasks SET status='paused',error=? WHERE id=? AND status IN ('running','queued')",
                               (json.dumps({'code': 'shutdown', 'message': '服务关闭，请手动恢复'}), task_id))
                raise
            except Exception as exc:
                log.exception('记忆任务失败 task_id=%s call_id=%s', task_id, call_id)
                if call_id:
                    self.usage.fail(call_id, str(exc))
                attempts = self.db.rows('SELECT attempts FROM tasks WHERE id=?', (task_id,))[0]['attempts']
                coverage = isinstance(exc, Failure) and exc.code=='coverage'
                temporary = isinstance(exc, (TimeoutError, ConnectionError))
                retry = (coverage or temporary) and attempts<2
                with self.db.transaction() as tx:
                    tx.execute("UPDATE tasks SET status=?,attempts=attempts+1,error=?,updated_at=? WHERE id=? AND status='running'",
                               ('queued' if retry else 'paused' if isinstance(exc, Failure) and exc.code=='budget' else 'failed', json.dumps(error_data(exc), ensure_ascii=False), now(), task_id))
                if retry:
                    if temporary:
                        await asyncio.sleep(2**(attempts+1))
                    limit = max(1,limit//2) if coverage else limit
                    continue
                return

    def start(self):
        self.timer = asyncio.create_task(self.tick())

    async def tick(self):
        while True:
            if self.config.get('enabled') and self.config.get('dreaming_enabled'):
                for room in self.archive.scopes():
                    timezone = ZoneInfo(self.config.get('dream_timezone','Asia/Shanghai'))
                    local = datetime.now(timezone)
                    if room['paused'] or local.hour<int(self.config.get('dream_hour',3)) or datetime.fromtimestamp(room['last_auto'],timezone).date()==local.date():
                        continue
                    scope = Scope(room['platform'], room['owner'], room['room'])
                    if scope.room not in self.config.get('allowed_rooms', []):
                        continue
                    pending = self.archive.batch(scope, room['cursor'], self.archive.ceiling(scope), 500)
                    due = pending and (len(pending)>=int(self.config.get('min_messages_to_dream', 10)) or now()-pending[0]['at']>=int(self.config.get('low_activity_max_wait_days', 7))*86400)
                    if due:
                        self.submit(scope, 'new')
                    member = self.db.rows('''SELECT m.sender FROM messages m LEFT JOIN progress p ON p.scope=m.scope AND p.member=m.sender
                        WHERE m.scope=? AND m.text!='' AND m.id>COALESCE(p.cursor,0)
                        AND NOT EXISTS(SELECT 1 FROM preferences o WHERE o.scope=m.scope AND o.member=m.sender AND o.opted_out=1)
                        GROUP BY m.sender ORDER BY COALESCE(p.cursor,0),MIN(m.id) LIMIT 1''', (scope.key,))
                    if member:
                        self.submit(scope, 'profile', member[0]['sender'])
                    if self.config.get('deferred_auto_retry'):
                        deferred = self.db.rows('''SELECT d.raw_id FROM deferred d JOIN messages m ON m.id=d.raw_id
                            WHERE d.scope=? AND d.attempts<2 AND m.text!='' ORDER BY d.raw_id LIMIT 1''', (scope.key,))
                        if deferred:
                            raw_id = deferred[0]['raw_id']
                            self.submit(scope,'deferred',start=raw_id-1,end=raw_id)
                            with self.db.transaction() as tx:
                                tx.execute('UPDATE deferred SET attempts=attempts+1 WHERE scope=? AND raw_id=?', (scope.key,raw_id))
                    with self.db.transaction() as tx:
                        tx.execute('UPDATE rooms SET last_auto=? WHERE scope=?', (now(), scope.key))
            await asyncio.sleep(int(self.config.get('scan_seconds', 300)))

    async def close(self):
        if self.timer:
            self.timer.cancel()
        workers = list(self.workers.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, *([self.timer] if self.timer else []), return_exceptions=True)
