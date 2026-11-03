"""Read-only, administrator-triggered import from Hook DurableInbox.

No connection to the Windows Hook or network requests. The source SQLite file must
be accessible from the AstrBot process (including its container namespace).
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from .identity import Scope, valid_room
from .storage import now

REQUIRED_COLUMNS = {
    'event_id', 'sequence', 'owner', 'room', 'sender', 'content',
    'kind', 'created_at',
}
DEFAULT_INBOX = 'data/wechat_hook_inbox.sqlite3'


def import_inbox(store, location, platform, allowed_rooms: set[str], *, max_rows=500, selected_owner=''):
    """Import only allowlisted group events; never alter Hook's inbox or cursor."""
    if not allowed_rooms:
        raise ValueError('allowed_rooms 为空，没有允许补录的群')
    source_location = str(location or '').strip() or DEFAULT_INBOX
    try:
        path = Path(source_location).expanduser().resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(
            '找不到 Hook Inbox：' + source_location +
            '；请在 hook_inbox_db 填写与 Hook 连接器 inbox_db 相同的路径'
            '（Docker 必须使用容器内路径）'
        ) from exc
    if not path.is_file():
        raise ValueError('Hook Inbox 路径不是文件：' + str(path))

    source_hash = hashlib.sha256(str(path).encode()).hexdigest()[:32]
    # Path.as_uri() correctly handles Windows drive letters and escaped paths.
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        tables = {r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'inbox' not in tables:
            raise ValueError('此 SQLite 文件没有 inbox 表，可能选错了数据库')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(inbox)')}
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise ValueError('Hook Inbox 缺少字段：' + ', '.join(missing))

        media_col = 'media_path' if 'media_path' in columns else 'NULL AS media_path'
        media_kind_col = 'media_kind' if 'media_kind' in columns else 'NULL AS media_kind'
        result = {
            'imported': 0, 'duplicates': 0, 'skipped': 0, 'scopes': 0,
            'errors': [], 'next': [], 'source_rows': 0, 'source_rooms': 0,
            'matching_rows': 0, 'note': '', 'using_default_path': not bool(str(location or '').strip()),
        }
        result['source_rows'] = int(conn.execute('SELECT COUNT(*) FROM inbox').fetchone()[0])
        # Filter in Python rather than LIMIT 500 on unfiltered DISTINCT: the
        # allowlisted room might otherwise be hidden behind unrelated groups.
        scopes = conn.execute(
            "SELECT DISTINCT owner,room FROM inbox WHERE room!=''").fetchall()
        result['source_rooms'] = len(scopes)
        remaining = max(1, min(int(max_rows), 5000))

        for item in scopes:
            owner, room = item['owner'], item['room']
            if room not in allowed_rooms or not valid_room(room) or not owner or (selected_owner and owner != selected_owner):
                continue
            s = Scope(platform, str(owner), str(room))
            result['matching_rows'] += int(conn.execute(
                'SELECT COUNT(*) FROM inbox WHERE owner=? AND room=?',
                (owner, room)).fetchone()[0])

            if remaining <= 0:
                continue
            with store._lock:
                state = store.db.execute(
                    'SELECT last_sequence FROM import_cursors '
                    'WHERE source_hash=? AND owner=? AND room=?',
                    (source_hash, owner, room)).fetchone()
            cursor = int(state[0]) if state else 0
            rows = conn.execute(f"""
                SELECT event_id,sequence,owner,room,sender,content,kind,
                       created_at,{media_col},{media_kind_col}
                  FROM inbox
                 WHERE owner=? AND room=? AND sequence>?
                 ORDER BY sequence,event_id LIMIT ?
            """, (owner, room, cursor, remaining)).fetchall()
            if not rows:
                continue
            result['scopes'] += 1
            for row in rows:
                try:
                    message_id = str(row['event_id'] or '')
                    sender = str(row['sender'] or '')
                    text = row['content'] if isinstance(row['content'], str) else ''
                    text_type = str(row['kind']).lower() in ('1', 'text')
                    media_kind = str(row['media_kind'] or '')
                    media_path = str(row['media_path'] or '')
                    if (text_type and text.strip()) or media_kind or media_path:
                        kind = 'text' if text_type else 'media'
                        inserted = store.enqueue(
                            s, message_id, sender, text if text_type else '',
                            int(row['created_at']), False,
                            kind=kind, media_path=media_path,
                            media_kind=media_kind, source='hook-inbox',
                        )
                        if inserted:
                            result['imported'] += 1
                        else:
                            # Store refuses invalid or empty rows as well as duplicates.
                            with store._lock:
                                exists = store.db.execute(
                                    'SELECT 1 FROM raw_messages WHERE platform=? '
                                    'AND owner=? AND room=? AND message_id=?',
                                    (platform, owner, room, message_id),
                                ).fetchone()
                            result['duplicates' if exists else 'skipped'] += 1
                    else:
                        # Service notices and unsupported non-text payloads
                        # are not falsely counted as imported memories.
                        result['skipped'] += 1

                    # Advance only after handling this source row. Successful
                    # insertion and cursor write are in separate DBs; retrying
                    # after a crash remains idempotent via message_id.
                    with store.transaction():
                        store.db.execute(
                            'INSERT INTO import_cursors '
                            '(source_hash,owner,room,last_sequence,updated_at) '
                            'VALUES(?,?,?,?,?) '
                            'ON CONFLICT(source_hash,owner,room) DO UPDATE SET '
                            'last_sequence=excluded.last_sequence, '
                            'updated_at=excluded.updated_at',
                            (source_hash, owner, room, int(row['sequence']), now()),
                        )
                    cursor = int(row['sequence'])
                    remaining -= 1
                except (ValueError, TypeError, sqlite3.Error) as exc:
                    if len(result['errors']) < 5:
                        result['errors'].append({
                            'room': room, 'sequence': row['sequence'],
                            'error': str(exc)[:220],
                        })
                    # Do not skip the failing sequence or consume remaining rows.
                    break
            result['next'].append({
                'room': room, 'owner': owner, 'sequence': cursor,
            })

        if not result['source_rows']:
            result['note'] = 'Hook Inbox 数据库为空：先确认连接器接收过消息。'
        elif not result['matching_rows']:
            result['note'] = (
                'Inbox 有记录，但没有匹配 allowed_rooms 的群消息；'
                '请核对真实群 ID、是否选中正确的 Hook 数据库。'
            )
        elif not result['imported'] and not result['errors']:
            result['note'] = '没有新消息：可能已经补录过，或只包含重复/不支持的消息。'
        return result
    finally:
        conn.close()
