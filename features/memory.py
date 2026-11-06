import json
from ..core.types import Failure, ident, now

PURPOSES = ('long_term', 'recent', 'event', 'uncertain')


class Memory:
    def __init__(self, db):
        self.db = db

    def list(self, scope, cursor='', query='', member='', limit=50):
        rows = self.db.rows('''SELECT * FROM claims WHERE scope=? AND id>? AND instr(content,?)>0
            AND (?='' OR subject=?) ORDER BY id LIMIT ?''', (scope.key, cursor, query, member, member, limit + 1))
        return {'items': rows[:limit], 'has_more': len(rows) > limit,
                'next_cursor': rows[limit-1]['id'] if len(rows) > limit else None}

    def context(self, scope, subjects=(), evidence_text=''):
        rows = self.db.rows("SELECT * FROM claims WHERE scope=? AND status='active'", (scope.key,))
        rows = [r for r in rows if not subjects or r['subject'] in subjects]
        terms = {evidence_text[i:i+2] for i in range(len(evidence_text)-1)}
        rows.sort(key=lambda r: (-sum(t in r['content'] for t in terms), -r['updated_at'], r['id']))
        return rows[:60]

    def events_context(self, scope, evidence_text=''):
        rows = self.db.rows("SELECT * FROM events WHERE scope=? AND status='active'", (scope.key,))
        rows.sort(key=lambda r: (r['title'] not in evidence_text, -r['at'],r['id']))
        return rows[:60]

    def sources(self, scope, claim_id):
        return self.db.rows('''SELECT m.*,e.start,e.end FROM evidence e JOIN messages m ON m.id=e.raw_id
            JOIN claims c ON c.id=e.claim_id WHERE c.scope=? AND c.id=? ORDER BY m.at''', (scope.key, claim_id))

    def event_sources(self, scope, event_id):
        return self.db.rows('''SELECT m.* FROM messages m JOIN event_evidence s ON m.id=s.raw_id
            JOIN events e ON s.event_id=e.id WHERE e.scope=? AND e.id=? ORDER BY m.at''', (scope.key,event_id))

    def revise_event(self, scope, event_id, revision, content, actor):
        with self.db.transaction() as tx:
            row = tx.execute('SELECT * FROM events WHERE scope=? AND id=?', (scope.key,event_id)).fetchone()
            if row is None or row['revision'] != revision:
                raise Failure('revision_conflict', '事件已被修订，请刷新')
            self.version(tx, scope, 'event', row, actor)
            tx.execute("UPDATE events SET content=?,status='active',revision=revision+1 WHERE id=?", (content,event_id))

    def members(self, scope, names, query='', cursor='', limit=50):
        rows = self.db.rows('''SELECT DISTINCT t.member FROM (
            SELECT sender member FROM messages WHERE scope=? UNION SELECT subject FROM claims WHERE scope=? AND subject!='')
            t WHERE t.member>? AND NOT EXISTS(SELECT 1 FROM preferences p WHERE p.scope=? AND p.member=t.member AND p.opted_out=1)
            ORDER BY t.member''', (scope.key, scope.key, cursor, scope.key))
        members = [r['member'] for r in rows if query in names.get(r['member'], r['member']) or query in r['member']]
        items = []
        for member in members[:limit]:
            claims = self.db.rows("SELECT * FROM claims WHERE scope=? AND subject=? AND status='active' ORDER BY updated_at DESC", (scope.key, member))
            items.append({'member': member, 'name': names.get(member, member),
                          'claims': claims, 'count': len(claims)})
        return {'items': items, 'has_more': len(members) > limit,
                'next_cursor': members[limit-1] if len(members) > limit else None}

    def revise(self, scope, claim_id, revision, changes, actor):
        with self.db.transaction() as tx:
            row = tx.execute('SELECT * FROM claims WHERE scope=? AND id=?', (scope.key, claim_id)).fetchone()
            if row is None or row['revision'] != revision:
                raise Failure('revision_conflict', '记忆已被修改，请刷新后重新操作')
            self.version(tx, scope, 'claim', row, actor)
            tx.execute('''UPDATE claims SET content=?,purpose=?,status=?,restricted=?,origin='manual',
                revision=revision+1,updated_at=? WHERE id=?''',
                (changes.get('content', row['content']), changes.get('purpose', row['purpose']),
                 changes.get('status', row['status']), changes.get('restricted', row['restricted']), now(), claim_id))

    def add_manual(self, scope, subject, content, purpose='long_term', restricted=False):
        claim_id = ident()
        with self.db.transaction() as tx:
            tx.execute('''INSERT INTO claims(id,scope,subject,content,purpose,attribution,status,restricted,origin,created_at,updated_at)
                VALUES(?,?,?,?,?,'self','active',?,'manual',?,?)''',
                (claim_id, scope.key, subject, content, purpose, int(restricted), now(), now()))
        return claim_id

    def own(self, scope, subject, claim_id=''):
        return self.db.rows("SELECT * FROM claims WHERE scope=? AND subject=? AND status='active' AND (?='' OR id=?) ORDER BY created_at,id", (scope.key,subject,claim_id,claim_id))

    def version(self, tx, scope, entity, row, actor):
        tx.execute('INSERT INTO versions(scope,entity,entity_id,revision,previous,actor,at) VALUES(?,?,?,?,?,?,?)',
                   (scope.key, entity, row['id'], row['revision'], json.dumps(dict(row), ensure_ascii=False), actor, now()))

    def commit(self, tx, scope, proposal, spans):
        events = {}
        for event in proposal['events']:
            event_id = event['existing_id'] or ident()
            events[event['ref']] = event_id
            if event['existing_id']:
                old = tx.execute('SELECT * FROM events WHERE scope=? AND id=?', (scope.key,event_id)).fetchone()
                manual = tx.execute("SELECT 1 FROM versions WHERE entity='event' AND entity_id=? AND actor!='analysis'", (event_id,)).fetchone()
                if old is None or old['revision'] != event['revision'] or old['status']!='active' or manual:
                    raise Failure('revision_conflict', '事件已被修订，请重新分析')
                self.version(tx, scope, 'event', old, 'analysis')
                tx.execute('UPDATE events SET content=?,revision=revision+1 WHERE id=?', (event['content'],event_id))
            else:
                # Exact same-source replay is idempotent; same title on another date stays separate.
                old = tx.execute('''SELECT e.id FROM events e WHERE e.scope=? AND e.title=? AND e.at=? AND e.status='active'
                    AND EXISTS(SELECT 1 FROM event_evidence s WHERE s.event_id=e.id AND s.raw_id=?)''',
                    (scope.key,event['title'],event['at'],event['sources'][0])).fetchone()
                if old:
                    event_id = old['id']
                    events[event['ref']] = event_id
                else:
                    tx.execute('INSERT INTO events(id,scope,title,content,at) VALUES(?,?,?,?,?)',
                               (event_id, scope.key, event['title'], event['content'], event['at']))
            tx.executemany('INSERT OR IGNORE INTO event_evidence VALUES(?,?)', [(event_id, raw) for raw in event['sources']])
        for claim in proposal['claims']:
            existing = claim.get('existing_id')
            event_id = events.get(claim.get('event_ref'))
            if existing:
                old = tx.execute('SELECT * FROM claims WHERE scope=? AND id=?', (scope.key, existing)).fetchone()
                if old is None or old['revision'] != claim['revision'] or old['status'] != 'active':
                    raise Failure('revision_conflict', '已有记忆被人工修改、撤回或版本已变化')
                # Equivalent replay only adds evidence. Text changes become review candidates.
                if old['content'] != claim['content']:
                    existing = None
            if existing:
                claim_id = existing
            else:
                retired = tx.execute("SELECT 1 FROM claims WHERE scope=? AND subject=? AND content=? AND status IN ('deleted','superseded')",
                                     (scope.key,claim['subject'],claim['content'])).fetchone()
                if retired:
                    continue
                same = tx.execute("SELECT id FROM claims WHERE scope=? AND subject=? AND content=? AND status='active' AND event_id IS ?",
                                  (scope.key, claim['subject'], claim['content'], event_id)).fetchone()
                claim_id = same['id'] if same else ident()
                if not same:
                    tx.execute('''INSERT INTO claims(id,scope,subject,content,purpose,reason,attribution,status,restricted,origin,event_id,valid_until,created_at,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,'analysis',?,?,?,?)''',
                        (claim_id, scope.key, claim['subject'], claim['content'], claim['purpose'], claim['reason'],
                         claim['attribution'], 'review' if claim.get('existing_id') or claim['purpose']=='uncertain' else 'active',
                         int(claim['restricted']), event_id, claim['valid_until'], now(), now()))
                if claim.get('existing_id'):
                    tx.execute('INSERT OR IGNORE INTO links(id,scope,source,target,kind) VALUES(?,?,?,?,?)',
                               (ident(), scope.key, claim_id, claim['existing_id'], 'change_candidate'))
            for raw in claim['sources']:
                start, end = spans[raw]
                tx.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?)', (claim_id, raw, start, end))
        return len(proposal['claims'])

    def resolve_link(self, scope, link_id, revisions, action, actor):
        with self.db.transaction() as tx:
            link = tx.execute("SELECT * FROM links WHERE scope=? AND id=? AND status='pending'", (scope.key, link_id)).fetchone()
            if link is None:
                raise Failure('revision_conflict', '该关联已处理')
            source, target = [tx.execute('SELECT * FROM claims WHERE id=?', (link[k],)).fetchone() for k in ('source', 'target')]
            if [source['revision'], target['revision']] != revisions:
                raise Failure('revision_conflict', '关联记忆版本已变化')
            for row in (source, target):
                self.version(tx, scope, 'claim', row, actor)
            if action == 'merge':
                tx.execute('INSERT OR IGNORE INTO evidence SELECT ?,raw_id,start,end FROM evidence WHERE claim_id=?', (target['id'], source['id']))
                tx.execute("UPDATE claims SET restricted=?,revision=revision+1,origin='manual' WHERE id=?", (max(source['restricted'],target['restricted']),target['id']))
                tx.execute("UPDATE claims SET status='superseded',revision=revision+1,origin='manual' WHERE id=?", (source['id'],))
            else:
                tx.execute("UPDATE claims SET status='active',revision=revision+1,origin='manual' WHERE id=?", (source['id'],))
                if action == 'replace':
                    tx.execute("UPDATE claims SET status='superseded',revision=revision+1,origin='manual' WHERE id=?", (target['id'],))
            tx.execute('UPDATE links SET status=? WHERE id=?', (action, link_id))

    def purge_member(self, tx, scope, member, erase):
        affected = {r[0] for r in tx.execute('''SELECT id FROM claims WHERE scope=? AND (subject=? OR id IN (
            SELECT claim_id FROM evidence e JOIN messages m ON m.id=e.raw_id WHERE m.scope=? AND m.sender=?))''',
            (scope.key, member, scope.key, member))}
        affected_events = {r[0] for r in tx.execute('''SELECT event_id FROM event_evidence e JOIN messages m ON m.id=e.raw_id
            WHERE m.scope=? AND m.sender=?''', (scope.key, member))}
        for cid in affected:
            tx.execute("DELETE FROM versions WHERE scope=? AND entity='claim' AND entity_id=?", (scope.key, cid))
            tx.execute('DELETE FROM claims WHERE id=?', (cid,))
        for eid in affected_events:
            tx.execute("DELETE FROM versions WHERE scope=? AND entity='event' AND entity_id=?", (scope.key, eid))
            # A shared summary cannot retain conclusions derived from the removed source.
            tx.execute("UPDATE events SET title='来源已移除，需重新整理',content='',status='review',revision=revision+1 WHERE id=?", (eid,))
        tx.execute('DELETE FROM progress WHERE scope=? AND member=?', (scope.key, member))
        if erase:
            tx.execute('DELETE FROM messages WHERE scope=? AND sender=?', (scope.key, member))
