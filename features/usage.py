from datetime import datetime, timezone
from ..core.types import Failure, ident, now


class Usage:
    def __init__(self, db, config):
        self.db, self.config = db, config
        with db.transaction() as tx:
            tx.execute("UPDATE calls SET status='needs_reconciliation' WHERE status='reserved'")

    def reserve(self, scope, task_id, input_tokens):
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        output = int(self.config.get('estimated_output_tokens_per_run', 4500))
        with self.db.transaction() as tx:
            totals = tx.execute('SELECT COUNT(*) n,COALESCE(SUM(input+output),0) tokens,COALESCE(SUM(input),0) inp,COALESCE(SUM(output),0) out FROM calls WHERE scope=? AND day=?', (scope.key, day)).fetchone()
            price_in = float(self.config.get('input_price_usd_per_million', 0))
            price_out = float(self.config.get('output_price_usd_per_million', 0))
            cost = ((totals['inp']+input_tokens)*price_in + (totals['out']+output)*price_out)/1_000_000
            cap = float(self.config.get('max_cost_per_group_day_usd', 0))
            if totals['n'] >= int(self.config.get('max_calls_per_group_day', 2)) or totals['tokens']+input_tokens+output > int(self.config.get('max_tokens_per_group_day', 40000)) or (cap and cost > cap):
                raise Failure('budget', '当前群今日模型调用预算不足，可在 UTC 零点（北京时间08:00）重置后恢复')
            call_id = ident()
            tx.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (call_id, task_id, scope.key, day, 'reserved', self.config['extraction_provider_id'], input_tokens, output, 1, '', now()))
        return call_id

    def observe(self, call_id, input_tokens, output_tokens, estimated):
        with self.db.transaction() as tx:
            tx.execute("UPDATE calls SET input=?,output=?,estimated=?,status='settled' WHERE id=? AND status='reserved'",
                       (input_tokens, output_tokens, int(estimated), call_id))

    def fail(self, call_id, error):
        with self.db.transaction() as tx:
            tx.execute("UPDATE calls SET status=CASE WHEN status='reserved' THEN 'needs_reconciliation' ELSE status END,error=? WHERE id=?", (error, call_id))

    def list(self, scope, cursor='', limit=50):
        rows = self.db.rows("SELECT * FROM calls WHERE scope=? AND (?='' OR id>?) ORDER BY id LIMIT ?", (scope.key,cursor,cursor,limit+1))
        return {'items':rows[:limit], 'has_more':len(rows)>limit, 'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}
