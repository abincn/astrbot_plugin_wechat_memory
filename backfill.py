"""Opt-in, read-only import from astrbot_plugin_wechat_hook DurableInbox."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

from .identity import Scope, valid_room
from .storage import now

REQUIRED_COLUMNS={'event_id','sequence','owner','room','sender','content','kind','created_at'}


def import_inbox(store,location,platform,allowed_rooms:set[str],*,max_rows=500):
    if not location:raise ValueError('Hook Inbox path is not configured')
    path=Path(location).expanduser().resolve(strict=True)
    if not path.is_file():raise ValueError('Hook Inbox path must be a SQLite file')
    source_hash=hashlib.sha256(str(path).encode()).hexdigest()[:32]
    uri='file:'+quote(str(path))+'?mode=ro'
    conn=sqlite3.connect(uri,uri=True,timeout=10)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        columns={r['name'] for r in conn.execute('PRAGMA table_info(inbox)')}
        if not REQUIRED_COLUMNS.issubset(columns):raise ValueError('Unknown Hook Inbox schema')
        media_col='media_path' if 'media_path' in columns else 'NULL AS media_path'
        kind_col='media_kind' if 'media_kind' in columns else 'NULL AS media_kind'
        result={'imported':0,'duplicates':0,'scopes':0,'next':[]}
        scopes=conn.execute('''SELECT DISTINCT owner,room FROM inbox WHERE room!='' LIMIT 500''').fetchall()
        remaining=max(1,min(int(max_rows),5000))
        for item in scopes:
            owner=item['owner'];room=item['room']
            if room not in allowed_rooms or not valid_room(room) or not owner or remaining<=0:continue
            s=Scope(platform,owner,room)
            with store._lock:
                row=store.db.execute('SELECT last_sequence FROM import_cursors WHERE source_hash=? AND owner=? AND room=?',
                                    (source_hash,owner,room)).fetchone()
            cursor=int(row[0]) if row else 0
            batch=conn.execute(f'''SELECT event_id,sequence,owner,room,sender,content,kind,created_at,
                 {media_col},{kind_col} FROM inbox WHERE owner=? AND room=? AND sequence>?
                 ORDER BY sequence LIMIT ?''',(owner,room,cursor,remaining)).fetchall()
            if not batch:continue
            result['scopes']+=1
            for r in batch:
                kind='text' if str(r['kind']) in ('1','text') else 'media'
                text=r['content'] if isinstance(r['content'],str) else ''
                if kind!='text':text=''
                try:
                    inserted=store.enqueue(s,str(r['event_id']),str(r['sender']),text,
                       int(r['created_at']),False,kind=kind,
                       media_path=r['media_path'] or '',media_kind=r['media_kind'] or '',source='hook-inbox')
                    result['imported' if inserted else 'duplicates']+=1
                    with store.transaction():
                        store.db.execute('''INSERT INTO import_cursors(source_hash,owner,room,last_sequence,updated_at)
                            VALUES(?,?,?,?,?) ON CONFLICT(source_hash,owner,room) DO UPDATE SET
                            last_sequence=excluded.last_sequence,updated_at=excluded.updated_at''',
                            (source_hash,owner,room,int(r['sequence']),now()))
                    cursor=int(r['sequence']);remaining-=1
                except (ValueError,sqlite3.Error):
                    # Leave cursor unchanged for this row; next invocation can retry.
                    break
            result['next'].append({'room':room,'owner':owner,'sequence':cursor})
        return result
    finally:conn.close()
