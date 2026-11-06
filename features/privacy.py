class Privacy:
    def __init__(self, db, archive, access, memory, identity, export_directory):
        self.db, self.archive, self.access, self.memory, self.identity = db, archive, access, memory, identity
        self.export_directory = export_directory

    def change(self, scope, member, opted_out, erase=False):
        self.archive.ensure_room(scope)
        with self.db.transaction() as tx:
            tx.execute('INSERT OR REPLACE INTO preferences VALUES(?,?,?)', (scope.key, member, int(opted_out)))
            if opted_out or erase:
                self.access.invalidate(tx, scope)
                self.memory.purge_member(tx, scope, member, erase)
                self.identity.delete_member(tx, scope, member)
        if opted_out or erase:
            # These are server-generated transient exports, not administrator backups.
            for path in self.export_directory.glob('*.jsonl'):
                path.unlink()
