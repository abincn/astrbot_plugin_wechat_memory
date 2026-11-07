"""Revision- and endpoint-bound optional V3 vectors, scoped before reading."""
import hashlib
import json


def endpoint_key(cfg):
    return hashlib.sha256(str(cfg.get('embedding_api_url','')).rstrip('/').encode()).hexdigest()


def migrate(db):
    db.execute('''CREATE TABLE IF NOT EXISTS claim_vectors (
      claim_id TEXT REFERENCES memory_claims(id) ON DELETE CASCADE,
      model TEXT,endpoint TEXT,revision INTEGER,vector_json TEXT,
      PRIMARY KEY(claim_id,model,endpoint))''')


async def index_missing(store,s,cfg,limit=12):
    if not cfg.get('embedding_enabled'):return 0
    from .embeddings import embed
    from .people import key
    endpoint=endpoint_key(cfg);model=cfg.get('embedding_model','')
    with store._lock:
        rows=[dict(r) for r in store.db.execute('''SELECT c.id,c.content,c.revision FROM memory_claims c
            LEFT JOIN claim_vectors v ON v.claim_id=c.id AND v.model=? AND v.endpoint=? AND v.revision=c.revision
            WHERE c.platform=? AND c.owner=? AND c.room=? AND c.status='active'
            AND c.sensitivity!='restricted' AND v.claim_id IS NULL
            AND NOT EXISTS(SELECT 1 FROM profile_optouts o WHERE o.platform=c.platform AND o.owner=c.owner AND o.room=c.room AND o.user_id=c.subject_id AND o.disabled=1)
            ORDER BY c.updated_at DESC,c.id LIMIT ?''',(model,endpoint,*key(s),max(1,min(30,limit))))]
    if not rows:return 0
    vectors=await embed(store,s,cfg,[r['content'] for r in rows])
    if vectors is None:return 0
    with store.transaction():
        for r,v in zip(rows,vectors):
            current=store.db.execute('SELECT revision,status FROM memory_claims WHERE id=?',(r['id'],)).fetchone()
            if not current or current['revision']!=r['revision'] or current['status']!='active':continue
            store.db.execute('INSERT OR REPLACE INTO claim_vectors VALUES(?,?,?,?,?)',(r['id'],model,endpoint,r['revision'],json.dumps(v)))
    return len(rows)


def scores(store,s,cfg,query_vector,allowed):
    from .people import key
    result={}
    with store._lock:
        rows=store.db.execute('''SELECT c.id,v.vector_json FROM claim_vectors v JOIN memory_claims c ON c.id=v.claim_id
          WHERE c.platform=? AND c.owner=? AND c.room=? AND c.status='active' AND c.sensitivity!='restricted'
          AND v.revision=c.revision AND v.model=? AND v.endpoint=?''',(*key(s),cfg.get('embedding_model',''),endpoint_key(cfg)))
        for r in rows:
            if r['id'] not in allowed:continue
            vector=json.loads(r['vector_json'])
            if len(vector)==len(query_vector):result[r['id']]=sum(a*b for a,b in zip(vector,query_vector))
    return result
