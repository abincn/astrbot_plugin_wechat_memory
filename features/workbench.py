"""Read models for the management screen. SQL remains outside HTTP/UI."""
from ..core.types import Failure


class Workbench:
    def __init__(self, db):
        self.db = db

    def overview(self, scope):
        return {table: self.db.rows(f'SELECT COUNT(*) n FROM {table} WHERE scope=?', (scope.key,))[0]['n']
                for table in ('messages','claims','events','tasks','deferred')}

    def list(self, scope, entity, cursor='', limit=50):
        table = {'events':'events','links':'links','versions':'versions','audits':'audits','deferred':'deferred'}[entity]
        key = 'raw_id' if table=='deferred' else 'id'
        rows = self.db.rows(f'SELECT * FROM {table} WHERE scope=? AND {key}>? ORDER BY {key} LIMIT ?',
                            (scope.key, cursor or (0 if table in ('versions','audits','deferred') else ''), limit+1))
        return {'items':rows[:limit], 'has_more':len(rows)>limit, 'next_cursor':rows[limit-1][key] if len(rows)>limit else None}

    def room(self, scope):
        rows = self.db.rows('SELECT * FROM rooms WHERE scope=?', (scope.key,))
        if not rows:
            raise Failure('not_found','当前群尚未创建归档')
        return rows[0]
