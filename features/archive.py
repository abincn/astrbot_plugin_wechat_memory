import json


class Archive:
    def __init__(self, db):
        self.db = db

    def ensure_room(self, scope):
        with self.db.transaction() as tx:
            tx.execute('INSERT OR IGNORE INTO rooms(scope,platform,owner,room) VALUES(?,?,?,?)',
                       (scope.key, scope.platform, scope.owner, scope.room))

    def append(self, scope, message_id, sender, text, at, kind='text', reply='', mentions=(), disclosure='private'):
        # The old collector silently discarded oversized text. Preserve a visible missing-original record instead.
        if len(text.encode('utf-8')) > 1_500_000:
            text, kind = '', 'missing_original'
        with self.db.transaction() as tx:
            tx.execute('INSERT OR IGNORE INTO rooms(scope,platform,owner,room) VALUES(?,?,?,?)',
                       (scope.key, scope.platform, scope.owner, scope.room))
            row = tx.execute('''INSERT OR IGNORE INTO messages(scope,message_id,sender,text,at,kind,reply,mentions,disclosure)
                VALUES(?,?,?,?,?,?,?,?,?)''', (scope.key, message_id, sender, text, at, kind, reply, json.dumps(mentions), disclosure))
            return row.rowcount == 1

    def scopes(self):
        return self.db.rows('SELECT * FROM rooms ORDER BY owner,room')

    def list(self, scope, cursor=0, query='', limit=50):
        rows = self.db.rows('SELECT * FROM messages WHERE scope=? AND id>? AND instr(text,?)>0 ORDER BY id LIMIT ?',
                            (scope.key, cursor, query, limit + 1))
        return {'items': rows[:limit], 'has_more': len(rows) > limit,
                'next_cursor': rows[limit-1]['id'] if len(rows) > limit else None}

    def batch(self, scope, cursor, end_id, limit, member=''):
        # Third-party evidence is included only when an existing claim already links it to this member.
        return self.db.rows('''SELECT m.* FROM messages m WHERE m.scope=? AND m.id>? AND m.id<=?
            AND (?='' OR sender=? OR EXISTS(SELECT 1 FROM evidence e JOIN claims c ON c.id=e.claim_id
            WHERE e.raw_id=m.id AND c.scope=m.scope AND c.subject=?)) ORDER BY m.id LIMIT ?''',
            (scope.key, cursor, end_id, member, member, member, limit))

    def ceiling(self, scope):
        return self.db.rows('SELECT COALESCE(MAX(id),0) n FROM messages WHERE scope=?', (scope.key,))[0]['n']

    def members(self, scope):
        return self.db.rows('SELECT DISTINCT sender FROM messages WHERE scope=?', (scope.key,))

    def disclose(self, scope, raw_id, value):
        with self.db.transaction() as tx:
            tx.execute('UPDATE messages SET disclosure=? WHERE scope=? AND id=?', (value, scope.key, raw_id))

    def delete_member(self, tx, scope, member):
        tx.execute('DELETE FROM messages WHERE scope=? AND sender=?', (scope.key, member))
