"""Group-scoped identities and deterministic, source-linked member projections."""
import time
import uuid


def key(scope):
    return scope.platform, scope.owner, scope.room


def migrate(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS member_aliases (
          id TEXT PRIMARY KEY, platform TEXT, owner TEXT, room TEXT, subject_id TEXT,
          name TEXT NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
          status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
          first_seen INTEGER, last_seen INTEGER,
          UNIQUE(platform,owner,room,subject_id,name,kind))''')
        db.execute('''CREATE TABLE IF NOT EXISTS alias_versions (
          alias_id TEXT, revision INTEGER, previous_status TEXT, actor TEXT, occurred_at INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS profile_progress (
          platform TEXT,owner TEXT,room TEXT,subject_id TEXT,cursor INTEGER DEFAULT 0,
          PRIMARY KEY(platform,owner,room,subject_id))''')
        progress_columns={r[1] for r in db.execute('PRAGMA table_info(profile_progress)')}
        if 'offset' not in progress_columns:
            db.execute('ALTER TABLE profile_progress ADD COLUMN offset INTEGER NOT NULL DEFAULT 0')
        columns={r[1] for r in db.execute('PRAGMA table_info(memory_claims)')}
        for name,definition in [('purpose',"TEXT NOT NULL DEFAULT 'unclassified'"),
                                ('purpose_reason',"TEXT NOT NULL DEFAULT ''"),
                                ('valid_until','INTEGER')]:
            if name not in columns:
                db.execute(f'ALTER TABLE memory_claims ADD COLUMN {name} {definition}')
        db.execute('''CREATE TABLE IF NOT EXISTS fragment_progress (
            job_id TEXT,raw_id INTEGER,offset INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(job_id,raw_id))''')
        db.execute('''CREATE TABLE IF NOT EXISTS claim_spans (
            claim_id TEXT REFERENCES memory_claims(id) ON DELETE CASCADE,
            raw_id INTEGER REFERENCES raw_messages(id) ON DELETE CASCADE,
            start_offset INTEGER,end_offset INTEGER,
            PRIMARY KEY(claim_id,raw_id,start_offset,end_offset))''')
        db.execute('''CREATE TABLE IF NOT EXISTS event_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT REFERENCES memory_events(id) ON DELETE CASCADE,
          revision INTEGER,summary TEXT,state TEXT,actor TEXT,occurred_at INTEGER)''')
        db.execute('''CREATE TABLE IF NOT EXISTS claim_links (
          source_id TEXT REFERENCES memory_claims(id) ON DELETE CASCADE,
          target_id TEXT REFERENCES memory_claims(id) ON DELETE CASCADE,
          relation TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',
          PRIMARY KEY(source_id,target_id,relation))''')
        fragment_columns={r[1] for r in db.execute('PRAGMA table_info(fragment_progress)')}
        if 'chunk_chars' not in fragment_columns:db.execute('ALTER TABLE fragment_progress ADD COLUMN chunk_chars INTEGER NOT NULL DEFAULT 45000')
        if 'unresolved' not in fragment_columns:
            db.execute('ALTER TABLE fragment_progress ADD COLUMN unresolved INTEGER NOT NULL DEFAULT 0')
        alias_columns={r[1] for r in db.execute('PRAGMA table_info(member_aliases)')}
        for name,definition in [('valid_to','INTEGER'),('source_message_id',"TEXT NOT NULL DEFAULT ''")]:
            if name not in alias_columns:db.execute(f'ALTER TABLE member_aliases ADD COLUMN {name} {definition}')
        db.execute('''CREATE TABLE IF NOT EXISTS alias_sources (
          alias_id TEXT REFERENCES member_aliases(id) ON DELETE CASCADE,
          raw_id INTEGER REFERENCES raw_messages(id) ON DELETE CASCADE,
          PRIMARY KEY(alias_id,raw_id))''')
        room_columns={r[1] for r in db.execute('PRAGMA table_info(room_state)')}
        if 'room_name' not in room_columns:db.execute("ALTER TABLE room_state ADD COLUMN room_name TEXT NOT NULL DEFAULT ''")
        if 'name_updated_at' not in room_columns:db.execute('ALTER TABLE room_state ADD COLUMN name_updated_at INTEGER NOT NULL DEFAULT 0')
        if 'privacy_epoch' not in room_columns:db.execute('ALTER TABLE room_state ADD COLUMN privacy_epoch INTEGER NOT NULL DEFAULT 0')
        raw_columns={r[1] for r in db.execute('PRAGMA table_info(raw_messages)')}
        if 'identity_json' not in raw_columns:db.execute("ALTER TABLE raw_messages ADD COLUMN identity_json TEXT NOT NULL DEFAULT '{}'")
        if 'disclosure' not in raw_columns:db.execute("ALTER TABLE raw_messages ADD COLUMN disclosure TEXT NOT NULL DEFAULT 'unreviewed'")
        audit_columns={r[1] for r in db.execute('PRAGMA table_info(retrieval_audit)')}
        if 'diagnostics' not in audit_columns:db.execute("ALTER TABLE retrieval_audit ADD COLUMN diagnostics TEXT NOT NULL DEFAULT '{}'")
        ledger_columns={r[1] for r in db.execute('PRAGMA table_info(analysis_ledger)')}
        for name,definition in [('auto_attempts','INTEGER NOT NULL DEFAULT 0'),('last_attempt','INTEGER'),('resolved_at','INTEGER')]:
            if name not in ledger_columns:db.execute(f'ALTER TABLE analysis_ledger ADD COLUMN {name} {definition}')
        job_columns={r[1] for r in db.execute('PRAGMA table_info(reanalysis_jobs)')}
        for name,definition in [('attempts','INTEGER NOT NULL DEFAULT 0'),('next_attempt','INTEGER NOT NULL DEFAULT 0'),('blocked_reason',"TEXT NOT NULL DEFAULT ''")]:
            if name not in job_columns:db.execute(f'ALTER TABLE reanalysis_jobs ADD COLUMN {name} {definition}')
        from .budget import migrate as migrate_budget
        from .claim_vectors import migrate as migrate_vectors
        migrate_budget(db);migrate_vectors(db)
        db.execute('PRAGMA user_version=6')
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK')
        raise


def aliases(store,scope,subject=''):
    with store._lock:
        rows=store.db.execute('SELECT * FROM member_aliases WHERE platform=? AND owner=? AND room=?'
            +(' AND subject_id=?' if subject else '')+' ORDER BY last_seen DESC,id',
            (*key(scope),*((subject,) if subject else ())))
        return [dict(r) for r in rows if not store.is_opted_out(scope,r['subject_id'])]


def observe(store,scope,subject,name,kind='nickname',source='adapter',confirmed=True,source_message_id=''):
    if store.is_opted_out(scope,subject):
        return
    if not isinstance(name,str):return
    name=name.strip()
    if not name or len(name)>100:
        return
    stamp=int(time.time())
    with store.transaction():
        if kind in ('nickname','card'):
            store.db.execute('UPDATE member_aliases SET valid_to=? WHERE platform=? AND owner=? AND room=? AND subject_id=? AND kind=? AND name!=? AND valid_to IS NULL',(stamp,*key(scope),subject,kind,name))
        store.db.execute('''INSERT INTO member_aliases
          (id,platform,owner,room,subject_id,name,kind,source,status,first_seen,last_seen,source_message_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(platform,owner,room,subject_id,name,kind)
          DO UPDATE SET last_seen=excluded.last_seen,valid_to=NULL''',
          (uuid.uuid4().hex,*key(scope),subject,name,kind,source,
           'confirmed' if confirmed else 'candidate',stamp,stamp,source_message_id))


def resolve(store,scope,question):
    matches=[r for r in aliases(store,scope) if r['status']=='confirmed' and r['name'] in question]
    names={r['name'] for r in matches}
    names={name for name in names if not any(name!=other and name in other for other in names)}
    groups=[{r['subject_id'] for r in matches if r['name']==name} for name in names]
    return sorted(set().union(*groups) if groups else set()),any(len(group)>1 for group in groups)


def classify(claim):
    text=claim['content']
    if claim['claim_type'] in ('event_detail','task','group_decision','topic_context') or claim.get('event_id'):
        return 'event','关联活动或任务'
    if any(w in text for w in ('要存钱','准备','打算','当时','录视频','锤子','过去要','一个多小时')):
        return 'recent','阶段性计划或临时行为，当前有效性未知'
    if claim['claim_type']=='preference':
        return 'long_term','明确表达的偏好，仍需核对原文'
    return 'uncertain','需人工确认长期使用价值'


def members(store,scope,subjects=None):
    with store._lock:
        clause=(' AND subject_id IN ('+','.join('?' for _ in subjects)+')') if subjects else ''
        rows=[dict(r) for r in store.db.execute('''SELECT * FROM memory_claims
          WHERE platform=? AND owner=? AND room=? AND subject_type='member'
          AND status IN ('active','candidate','review')'''+clause+' ORDER BY last_supported DESC,id',(*key(scope),*(subjects or ())))]
    names=aliases(store,scope)
    result={}
    for subject in subjects or {r['subject_id'] for r in rows}:
        if store.is_opted_out(scope,subject):continue
        own=[a for a in names if a['subject_id']==subject and a['status']=='confirmed']
        own.sort(key=lambda a:(bool(a['valid_to']),{'admin':0,'card':1,'nickname':2}.get(a['kind'],3),-a['last_seen']))
        result[subject]={'id':subject,'subject_id':subject,'display_name':own[0]['name'] if own else subject,
            'aliases':[a for a in names if a['subject_id']==subject and a['status']!='retracted'],'claims':[],'status':'active'}
    for claim in rows:
        subject=claim['subject_id']
        if store.is_opted_out(scope,subject):
            continue
        if subject not in result:
            own=[a for a in names if a['subject_id']==subject and a['status']=='confirmed']
            own.sort(key=lambda a:(bool(a['valid_to']),{'admin':0,'card':1,'nickname':2}.get(a['kind'],3),-a['last_seen']))
            result[subject]={'id':subject,'subject_id':subject,
                'display_name':own[0]['name'] if own else subject,'aliases':[a for a in names if a['subject_id']==subject and a['status']!='retracted'],'claims':[],'status':'active'}
        result[subject]['claims'].append(claim)
    for member in result.values():
        useful=[c['content'] for c in member['claims'] if c['purpose']=='long_term'
                and c['status']=='active' and c['sensitivity']!='restricted']
        member['content']='；'.join(useful[:3]) or '长期资料待整理'
        member['updated_at']=max([c['last_supported'] for c in member['claims']]+[a['last_seen'] for a in member['aliases']]+[0])
    return list(result.values())
