from ..core.types import Failure


class Access:
    def __init__(self, db, config):
        self.db, self.config = db, config

    def check(self, scope):
        if not self.config.get('enabled') or scope.platform != self.config.get('platform_id', 'wechat_pc_hook') or scope.room not in self.config.get('allowed_rooms', []):
            raise Failure('scope_denied', '插件未启用或当前群未获授权')
        if self.paused(scope):
            raise Failure('group_paused', '当前群已暂停')

    def paused(self, scope):
        return bool(self.db.rows('SELECT 1 FROM rooms WHERE scope=? AND paused=1', (scope.key,)))

    def opted_out(self, scope, member):
        return bool(self.db.rows('SELECT 1 FROM preferences WHERE scope=? AND member=? AND opted_out=1', (scope.key, member)))

    def excluded(self, scope):
        return {r['member'] for r in self.db.rows('SELECT member FROM preferences WHERE scope=? AND opted_out=1', (scope.key,))}

    def set_permission(self, platform, owner, member, room, allowed):
        with self.db.transaction() as tx:
            tx.execute('INSERT OR REPLACE INTO permissions VALUES(?,?,?,?,?)', (platform, owner, member, room, int(allowed)))

    def permissions(self, platform, owner, member):
        return [r['room'] for r in self.db.rows('SELECT room FROM permissions WHERE platform=? AND owner=? AND member=? AND allowed=1', (platform, owner, member))]

    def pause(self, scope, paused):
        with self.db.transaction() as tx:
            tx.execute('UPDATE rooms SET paused=?,epoch=epoch+1 WHERE scope=?', (int(paused), scope.key))

    def invalidate(self, tx, scope):
        tx.execute('UPDATE rooms SET epoch=epoch+1 WHERE scope=?', (scope.key,))
        tx.execute("UPDATE tasks SET status='cancelled',result='{}' WHERE scope=? AND status IN ('queued','running','paused')", (scope.key,))
        tx.execute("UPDATE tasks SET result='{}' WHERE scope=? AND preview=1", (scope.key,))
        tx.execute('DELETE FROM audits WHERE scope=?', (scope.key,))
