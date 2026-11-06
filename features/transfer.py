"""Read-only source import. No old schema or execution compatibility in the kernel."""
import hashlib
import json
import sqlite3
from pathlib import Path
from ..core.types import Scope, Failure


class Transfer:
    def __init__(self, db, archive, memory, identity, config):
        self.db, self.archive, self.memory, self.identity, self.config = db, archive, memory, identity, config

    def import_file(self, path, scope, limit=500):
        path = Path(path).resolve(strict=True)
        source_id = hashlib.sha256(str(path).encode()).hexdigest()
        result = {'imported': 0, 'duplicates': 0, 'skipped': 0, 'errors': []}
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as source:
            source.row_factory = sqlite3.Row
            # backup() produces a consistent source snapshot while an old instance is collecting messages.
            snapshot = sqlite3.connect(':memory:')
            source.backup(snapshot)
        snapshot.row_factory = sqlite3.Row
        try:
            tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'raw_messages' in tables:
                self.import_settings(snapshot, tables, scope)
                sql = 'SELECT id,message_id,sender_id sender,text,message_at at,kind,reply_to_id reply FROM raw_messages WHERE platform=? AND owner=? AND room=? AND id>? ORDER BY id LIMIT ?'
                args = [scope.platform, scope.owner, scope.room]
            elif 'inbox' in tables:
                sql = "SELECT sequence id,event_id message_id,sender,content text,created_at at,kind,'' reply FROM inbox WHERE owner=? AND room=? AND sequence>? ORDER BY sequence LIMIT ?"
                args = [scope.owner, scope.room]
            else:
                raise Failure('import_schema', '来源没有 raw_messages 或 inbox 表')
            previous = self.db.rows('SELECT cursor FROM imports WHERE source=? AND scope=?', (source_id, scope.key))
            cursor = previous[0]['cursor'] if previous else 0
            rows = snapshot.execute(sql, (*args, cursor, limit)).fetchall()
            for row in rows:
                if not row['message_id'] or not row['sender']:
                    result['skipped'] += 1
                    result['errors'].append({'source_id': row['id'], 'reason': '缺少消息或发言者ID'})
                else:
                    text = row['text'] or ''
                    kind = str(row['kind'])
                    new_kind = 'text' if kind in ('text','1') else 'media'
                    if not text and kind in ('text','1','oversized','missing_original'):
                        new_kind = 'missing_original'
                    inserted = self.archive.append(scope, str(row['message_id']), row['sender'], text, int(row['at']),
                                                   new_kind, row['reply'] or '')
                    result['imported' if inserted else 'duplicates'] += 1
                cursor = row['id']
            with self.db.transaction() as tx:
                tx.execute('INSERT OR REPLACE INTO imports VALUES(?,?,?)', (source_id, scope.key, cursor))
            result.update(cursor=cursor, has_more=len(rows)==limit)
            return result
        finally:
            snapshot.close()

    def import_settings(self, source, tables, scope):
        # Permissions and opt-outs are imported before any content.
        with self.db.transaction() as tx:
            if 'profile_optouts' in tables:
                for row in source.execute('SELECT user_id,disabled FROM profile_optouts WHERE platform=? AND owner=? AND room=?', (scope.platform, scope.owner, scope.room)):
                    tx.execute('INSERT OR IGNORE INTO preferences VALUES(?,?,?)', (scope.key, row['user_id'], row['disabled']))
            if 'profile_permissions' in tables:
                for row in source.execute('SELECT * FROM profile_permissions WHERE platform=? AND owner=?', (scope.platform, scope.owner)):
                    tx.execute('INSERT OR IGNORE INTO permissions VALUES(?,?,?,?,?)', (scope.platform, scope.owner, row['user_id'], row['room'], row['allowed']))
        if 'member_aliases' in tables:
            for row in source.execute("SELECT * FROM member_aliases WHERE platform=? AND owner=? AND room=? AND status='confirmed' ORDER BY last_seen", (scope.platform, scope.owner, scope.room)):
                self.identity.observe(scope, row['subject_id'], row['name'], row['kind'], 'legacy_import')
        excluded = {r['member'] for r in self.db.rows('SELECT member FROM preferences WHERE scope=? AND opted_out=1',(scope.key,))}
        if 'memories' in tables:
            for row in source.execute("SELECT * FROM memories WHERE platform=? AND owner=? AND (room=? OR scope='global') AND origin IN ('manual','user_dm') AND status='active'", (scope.platform, scope.owner, scope.room)):
                if row['scope']!='global' and row['subject_id'] in excluded:
                    continue
                target = Scope(scope.platform, scope.owner, '') if row['scope']=='global' else scope
                self.archive.ensure_room(target)
                with self.db.transaction() as tx:
                    tx.execute('''INSERT OR IGNORE INTO claims(id,scope,subject,content,purpose,attribution,status,origin,created_at,updated_at)
                        VALUES(?,?,?,?,'long_term','self','active','manual',?,?)''',
                        ('import-'+row['id'], target.key, row['subject_id'], row['content'], row['created_at'], row['updated_at']))
        if 'memory_claim_versions' in tables:
            for row in source.execute("""SELECT c.* FROM memory_claims c WHERE c.platform=? AND c.owner=? AND c.room=?
                AND c.status='active' AND EXISTS(SELECT 1 FROM memory_claim_versions v WHERE v.claim_id=c.id AND v.reason='edit')""",
                (scope.platform,scope.owner,scope.room)):
                if row['subject_id'] in excluded:
                    continue
                with self.db.transaction() as tx:
                    tx.execute('''INSERT OR IGNORE INTO claims(id,scope,subject,content,purpose,reason,attribution,status,restricted,origin,created_at,updated_at)
                        VALUES(?,?,?,?,'uncertain',?,?,'review',?,'manual',?,?)''',
                        ('import-'+row['id'],scope.key,row['subject_id'] if row['subject_type']=='member' else '', row['content'],
                         '旧库人工修订；重新确认分类与来源后启用',
                         {'self_statement':'self','observed_behavior':'observed'}.get(row['attribution'],'third_party'),
                         int(row['sensitivity']=='restricted' or row['use_policy']=='restricted_output'),row['created_at'],row['updated_at']))

    def export(self, scope, path):
        # The lock holds a consistent read transaction; export writes incrementally, without a row cap.
        counts = {}
        with self.db.transaction() as tx, Path(path).open('w', encoding='utf-8') as output:
            for table in ('rooms','messages','aliases','alias_versions','preferences','permissions','claims','events','evidence','event_evidence','versions','links','tasks','progress','deferred','calls','batches','audits','imports'):
                if table in ('alias_versions',):
                    sql, args = 'SELECT v.* FROM alias_versions v JOIN aliases a ON a.id=v.alias_id WHERE a.scope=?', (scope.key,)
                elif table in ('evidence','event_evidence'):
                    sql, args = f'SELECT e.* FROM {table} e JOIN messages m ON m.id=e.raw_id WHERE m.scope=?', (scope.key,)
                elif table=='batches':
                    sql, args = 'SELECT b.* FROM batches b JOIN tasks t ON t.id=b.task_id WHERE t.scope=?', (scope.key,)
                elif table=='permissions':
                    sql, args = 'SELECT * FROM permissions WHERE platform=? AND owner=? AND room=?', (scope.platform, scope.owner, scope.room)
                else:
                    sql, args = f'SELECT * FROM {table} WHERE scope=?', (scope.key,)
                count = 0
                for row in tx.execute(sql, args):
                    output.write(json.dumps({'table':table,'row':dict(row)}, ensure_ascii=False)+'\n')
                    count += 1
                counts[table] = count
            output.write(json.dumps({'manifest': counts})+'\n')
        return {'path': str(path), 'counts': counts, 'note': '此文件是记录导出；SQLite备份另行保留'}
