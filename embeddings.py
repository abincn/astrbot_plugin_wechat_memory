"""Optional OpenAI-compatible embeddings, exact scoped vectors in SQLite. Disabled by default."""
from __future__ import annotations

import asyncio
import json
import math
import os
import urllib.parse
import urllib.request

from .extraction import estimate_tokens
from .storage import now


def _endpoint(url:str)->str:
    raw=url.strip().rstrip('/')
    parsed=urllib.parse.urlsplit(raw)
    if parsed.scheme!='https' and not (parsed.scheme=='http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('Embedding endpoint must use HTTPS, or local loopback HTTP')
    if parsed.username or parsed.password or parsed.fragment or not parsed.hostname:raise ValueError('Invalid embedding endpoint')
    if raw.endswith('/embeddings'):return raw
    return raw+'/embeddings'


def _call(url:str,model:str,inputs:list[str],timeout=15):
    endpoint=_endpoint(url)
    key=os.environ.get('WECHAT_MEMORY_EMBED_API_KEY','').strip()
    payload=json.dumps({'model':model,'input':inputs},ensure_ascii=False).encode()
    headers={'Content-Type':'application/json'}
    if key:headers['Authorization']='Bearer '+key
    req=urllib.request.Request(endpoint,data=payload,headers=headers,method='POST')
    with urllib.request.urlopen(req,timeout=timeout) as r:
        if int(r.headers.get('Content-Length','0') or 0)>2097152:raise ValueError('Embedding response too large')
        data=r.read(2097153)
    if len(data)>2097152:raise ValueError('Embedding response too large')
    obj=json.loads(data)
    rows=obj.get('data')
    if not isinstance(rows,list) or len(rows)!=len(inputs):raise ValueError('Unexpected embeddings shape')
    result=[None]*len(inputs)
    for item in rows:
        i=item.get('index');v=item.get('embedding')
        if type(i) is not int or i<0 or i>=len(result) or not isinstance(v,list) or not 1<=len(v)<=4096:
            raise ValueError('Invalid vector')
        if any(not isinstance(x,(int,float)) or not math.isfinite(x) for x in v):raise ValueError('Invalid vector value')
        norm=math.sqrt(sum(float(x)**2 for x in v))
        if norm==0:raise ValueError('Zero embedding vector')
        result[i]=[float(x)/norm for x in v]
    if any(v is None for v in result):raise ValueError('Missing vector index')
    return result


async def embed(store,s,cfg,texts:list[str],purpose='embedding'):
    if not cfg.get('embedding_enabled') or not cfg.get('embedding_api_url') or not cfg.get('embedding_model'):
        return None
    estimate=sum(estimate_tokens(x) for x in texts)
    from .storage import day_key
    charge_day=day_key()
    if not store.budget_reserve(s,estimate,purpose=purpose,day=charge_day,
        limit_calls=max(1,int(cfg.get('embedding_daily_calls',20))),
        limit_tokens=max(100,int(cfg.get('embedding_daily_tokens',20000)))):
        return None
    failed=True
    try:
        vectors=await asyncio.to_thread(_call,str(cfg['embedding_api_url']),str(cfg['embedding_model']),texts)
        failed=False
        return vectors
    finally:
        store.budget_finish(s,estimate,estimate,0,purpose=purpose,estimated_usage=True,error=failed,day=charge_day)


async def index_missing(store,s,cfg,limit=12):
    if not cfg.get('embedding_enabled'):return 0
    with store._lock:
        rows=[dict(r) for r in store.db.execute('''SELECT m.id,m.content,m.revision FROM memories m
          LEFT JOIN embedding_vectors v ON v.memory_id=m.id AND v.model=? AND v.revision=m.revision
          WHERE m.platform=? AND m.owner=? AND m.room=? AND m.status='active'
          AND v.memory_id IS NULL ORDER BY m.updated_at DESC LIMIT ?''',
          (cfg.get('embedding_model',''),*store._key(s),min(limit,30)))]
    if not rows:return 0
    vectors=await embed(store,s,cfg,[r['content'] for r in rows])
    if vectors is None:return 0
    with store.transaction():
        for row,v in zip(rows,vectors):
            current=store.db.execute('SELECT revision,status FROM memories WHERE id=?',(row['id'],)).fetchone()
            if not current or current['revision']!=row['revision'] or current['status']!='active':continue
            store.db.execute('''INSERT INTO embedding_vectors(memory_id,model,revision,vector_json,updated_at)
               VALUES(?,?,?,?,?) ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model,
               revision=excluded.revision,vector_json=excluded.vector_json,updated_at=excluded.updated_at''',
               (row['id'],cfg['embedding_model'],row['revision'],json.dumps(v),now()))
    return len(rows)


async def rank(store,s,user_id,cfg,question,allowed_ids:set[str]):
    if not allowed_ids:return {}
    vectors=await embed(store,s,cfg,[question[:1000]],purpose='embedding_query')
    if not vectors:return {}
    q=vectors[0];found={}
    with store._lock:
        for row in store.db.execute('''SELECT v.memory_id,v.vector_json FROM embedding_vectors v JOIN memories m
           ON m.id=v.memory_id WHERE m.status='active' AND v.model=?''',(cfg.get('embedding_model',''),)):
            if row['memory_id'] not in allowed_ids:continue
            v=json.loads(row['vector_json'])
            if len(v)!=len(q):continue
            found[row['memory_id']]=sum(a*b for a,b in zip(q,v))
    return found
