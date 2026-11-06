"""Whitelisted, scoped keyset pagination for management lists."""
TABLES={
    'list':('memories','content','status','kind'),
    'v3_claims':('memory_claims','content','status','claim_type'),
    'v3_events':('memory_events','title','state','event_type'),
    'history':('raw_messages','text','status','kind'),
    'runs':('dream_runs','error','status','provider'),
    'topics':('topic_states','summary','state',''),
    'observations':('observation_signals','content','status','signal_type'),
    'model_calls':('model_calls','purpose','status','purpose'),
    'retrieval_audit':('retrieval_audit','requester','','mode'),
}


def read(store,s,operation,payload):
    from .people import key, members
    limit=max(1,min(100,int(payload.get('limit',50))))
    cursor=str(payload.get('cursor',''))[:256]
    query=str(payload.get('query',''))[:100].lower()
    status=str(payload.get('status',''))[:32]
    kind=str(payload.get('kind',''))[:40]
    if operation=='v3_profiles':
        where=['r.platform=?','r.owner=?','r.room=?','r.sender_id>?',
               'NOT EXISTS(SELECT 1 FROM profile_optouts o WHERE o.platform=r.platform AND o.owner=r.owner AND o.room=r.room AND o.user_id=r.sender_id AND o.disabled=1)']
        args=[*key(s),cursor]
        if query:
            where.append('''(instr(lower(r.sender_id),?)>0 OR EXISTS(
                SELECT 1 FROM memory_claims c WHERE c.platform=r.platform AND c.owner=r.owner AND c.room=r.room AND c.subject_id=r.sender_id AND instr(lower(c.content),?)>0) OR EXISTS(
                SELECT 1 FROM member_aliases a WHERE a.platform=r.platform AND a.owner=r.owner
                AND a.room=r.room AND a.subject_id=r.sender_id AND a.status='confirmed' AND instr(lower(a.name),?)>0))''')
            args.extend([query]*3)
        with store._lock:
            ids=[r[0] for r in store.db.execute('SELECT DISTINCT r.sender_id FROM raw_messages r WHERE '
                +' AND '.join(where)+' ORDER BY r.sender_id LIMIT ?',(*args,limit+1))]
        rows=members(store,s,ids[:limit]) if ids else []
        rows.sort(key=lambda r:r['id'])
        return {'items':rows,'has_more':len(ids)>limit,'next_cursor':ids[limit-1] if len(ids)>limit else None}
    if operation=='deferred_list':
        with store._lock:
            rows=[dict(r) for r in store.db.execute('''SELECT l.raw_id id,l.reason,l.result status,
                l.auto_attempts,l.last_attempt,l.updated_at,r.text content,r.kind,r.sender_id,r.message_at
                FROM analysis_ledger l JOIN raw_messages r ON r.id=l.raw_id
                WHERE l.platform=? AND l.owner=? AND l.room=? AND l.result='deferred'
                AND l.raw_id>? AND (instr(lower(l.reason),?)>0 OR instr(lower(r.text),?)>0)
                ORDER BY l.raw_id LIMIT ?''',(*key(s),int(cursor or 0),query,query,limit+1))]
        return {'items':rows[:limit],'has_more':len(rows)>limit,'next_cursor':str(rows[limit-1]['id']) if len(rows)>limit else None}
    table,text,status_col,kind_col=TABLES[operation]
    numeric=operation in ('history','retrieval_audit')
    date_column={'list':'updated_at','v3_claims':'updated_at','v3_events':'updated_at','runs':'created_at','topics':'updated_at','observations':'updated_at','model_calls':'created_at'}.get(operation)
    where=['t.platform=?','t.owner=?','t.room=?']
    args=[*key(s)]
    if cursor:
        if date_column:
            stamp,ident=cursor.split(':',1)
            where.append('(t.'+date_column+'<? OR (t.'+date_column+'=? AND t.id<?))')
            args.extend([int(stamp),int(stamp),ident])
        else:
            where.append('t.id<?');args.append(int(cursor) if numeric else cursor)
    ordering=('t.'+date_column+' DESC,' if date_column else '')+'t.id DESC'
    if query:
        where.append(f'instr(lower(t.{text}),?)>0');args.append(query)
    for column,value in ((status_col,status),(kind_col,kind)):
        if column and value:where.append(f't.{column}=?');args.append(value)
    if operation=='v3_claims':
        for column in ('event_id','subject_id'):
            if payload.get(column):where.append('t.'+column+'=?');args.append(str(payload[column])[:256])
        where.append("NOT EXISTS(SELECT 1 FROM profile_optouts o WHERE o.platform=t.platform AND o.owner=t.owner AND o.room=t.room AND o.user_id=t.subject_id AND o.disabled=1 AND t.subject_type='member')")
    with store._lock:
        rows=[dict(r) for r in store.db.execute('SELECT t.* FROM '+table+' t WHERE '
              +' AND '.join(where)+' ORDER BY '+ordering+' LIMIT ?',(*args,limit+1))]
        if operation=='runs':
            for r in rows:
                v3=store.db.execute('SELECT * FROM v3_run_details WHERE run_id=?',(r['id'],)).fetchone()
                if v3:r['v3_detail']=dict(v3)
                v2=store.v2_run_detail(s,r['id'])
                if v2:r['v2_detail']=v2
    return {'items':rows[:limit],'has_more':len(rows)>limit,
            'next_cursor':((str(rows[limit-1][date_column])+':') if date_column else '')+str(rows[limit-1]['id']) if len(rows)>limit else None}
