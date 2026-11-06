import json
from ..core.types import Failure, ident, now


class Identity:
    def __init__(self, db):
        self.db = db

    def observe(self, scope, member, name, kind='nickname', source='adapter', confirmed=True):
        if not name or name == member:
            return
        with self.db.transaction() as tx:
            if tx.execute('SELECT 1 FROM preferences WHERE scope=? AND member=? AND opted_out=1', (scope.key, member)).fetchone():
                return
            tx.execute('''INSERT INTO aliases(id,scope,member,name,kind,source,status,first_at,last_at)
                VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,member,name,kind) DO UPDATE SET last_at=excluded.last_at''',
                (ident(), scope.key, member, name, kind, source, 'confirmed' if confirmed else 'pending', now(), now()))

    def aliases(self, scope, member=''):
        return self.db.rows('SELECT * FROM aliases WHERE scope=? AND (?=\'\' OR member=?) ORDER BY last_at DESC',
                            (scope.key, member, member))

    def page(self, scope, member='', cursor='', limit=50):
        rows = self.db.rows("SELECT * FROM aliases WHERE scope=? AND (?='' OR member=?) AND id>? ORDER BY id LIMIT ?", (scope.key,member,member,cursor,limit+1))
        return {'items':rows[:limit], 'has_more':len(rows)>limit, 'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}

    def names(self, scope):
        priority = {'admin': 0, 'card': 1, 'nickname': 2, 'self': 3, 'alias': 4}
        result = {}
        for row in sorted(self.aliases(scope), key=lambda r: (priority.get(r['kind'], 4), -r['last_at'])):
            if row['status'] == 'confirmed':
                result.setdefault(row['member'], row['name'])
        return result

    def resolve(self, scope, question):
        matches = {}
        for row in self.aliases(scope):
            if row['status'] == 'confirmed' and row['name'] in question:
                matches.setdefault(row['name'], set()).add(row['member'])
        ambiguous = [name for name, ids in matches.items() if len(ids) > 1]
        return {'members': sorted(set().union(*matches.values())) if matches and not ambiguous else [], 'ambiguous': ambiguous}

    def revise(self, scope, alias_id, revision, status, actor):
        with self.db.transaction() as tx:
            row = tx.execute('SELECT * FROM aliases WHERE scope=? AND id=?', (scope.key, alias_id)).fetchone()
            if row is None or row['revision'] != revision:
                raise Failure('revision_conflict', '称呼已被修改，请刷新后重新操作')
            tx.execute('INSERT INTO alias_versions(alias_id,revision,previous,actor,at) VALUES(?,?,?,?,?)',
                       (alias_id, revision, json.dumps(dict(row), ensure_ascii=False), actor, now()))
            tx.execute('UPDATE aliases SET status=?,revision=revision+1 WHERE id=?', (status, alias_id))

    def set_room_name(self, scope, name):
        with self.db.transaction() as tx:
            tx.execute('UPDATE rooms SET name=? WHERE scope=?', (name, scope.key))

    def delete_member(self, tx, scope, member):
        tx.execute('DELETE FROM aliases WHERE scope=? AND member=?', (scope.key, member))
        tx.execute('DELETE FROM progress WHERE scope=? AND member=?', (scope.key, member))
