"""Transactional SQLite storage; every record is account- and room-scoped."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .identity import Scope
from .policy import fingerprint, safe_content, topic_key


def now() -> int:
    return int(time.time())


def day_key() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


class MemoryStore:
    def __init__(self, path: str | Path):
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), isolation_level=None, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        with self.transaction():
            self.db.execute("UPDATE pending SET status='pending', batch_id='' WHERE status='processing'")

    @contextlib.contextmanager
    def transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def _init_schema(self):
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS pending (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              message_id TEXT NOT NULL, sender_id TEXT NOT NULL,
              text TEXT NOT NULL, message_at INTEGER NOT NULL, received_at INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','done')),
              attempts INTEGER NOT NULL DEFAULT 0, retry_after INTEGER NOT NULL DEFAULT 0,
              urgent INTEGER NOT NULL DEFAULT 0, batch_id TEXT NOT NULL DEFAULT '',
              UNIQUE(platform,owner,room,message_id)
            );
            CREATE INDEX IF NOT EXISTS pending_ready ON pending(platform,owner,room,status,retry_after,received_at);
            CREATE TABLE IF NOT EXISTS memories (
              id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL,
              room TEXT NOT NULL, subject_id TEXT NOT NULL DEFAULT '',
              scope TEXT NOT NULL CHECK(scope IN ('group','member','global')),
              kind TEXT NOT NULL, topic_key TEXT NOT NULL, fingerprint TEXT NOT NULL,
              content TEXT NOT NULL, status TEXT NOT NULL
                CHECK(status IN ('active','review','superseded','deleted')),
              importance INTEGER NOT NULL DEFAULT 2,
              origin TEXT NOT NULL DEFAULT 'extractor',
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              supersedes_id TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS memories_scope ON memories(platform,owner,room,status,updated_at);
            CREATE INDEX IF NOT EXISTS memories_topic ON memories(platform,owner,room,scope,subject_id,topic_key,status);
            CREATE TABLE IF NOT EXISTS memory_sources (
              memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
              message_id TEXT NOT NULL, sender_id TEXT NOT NULL, created_at INTEGER NOT NULL,
              PRIMARY KEY(memory_id,message_id)
            );
            CREATE TABLE IF NOT EXISTS memory_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              memory_id TEXT NOT NULL, action TEXT NOT NULL, previous_content TEXT NOT NULL,
              actor TEXT NOT NULL, occurred_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_usage_daily (
              day TEXT NOT NULL, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              calls INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
              output_tokens INTEGER NOT NULL DEFAULT 0, errors INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(day,platform,owner,room)
            );
            CREATE TABLE IF NOT EXISTS room_options (
              platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
              paused INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY(platform,owner,room)
            );
            CREATE TABLE IF NOT EXISTS profile_permissions (
              platform TEXT NOT NULL, owner TEXT NOT NULL, user_id TEXT NOT NULL,
              room TEXT NOT NULL, allowed INTEGER NOT NULL, changed_at INTEGER NOT NULL,
              PRIMARY KEY(platform,owner,user_id,room)
            );
        """)

    def enqueue(self, scope: Scope, message_id: str, sender_id: str, text: str,
                timestamp: int, urgent: bool, max_queue: int = 10000) -> bool:
        if not (isinstance(message_id, str) and 0 < len(message_id) <= 220):
            return False
        if not text or len(text) > 1600:
            return False
        with self.transaction():
            count = self.db.execute("SELECT COUNT(*) FROM pending WHERE platform=? AND owner=? AND room=? AND status!='done'",
                                    (scope.platform, scope.owner, scope.room)).fetchone()[0]
            if self.db.execute("SELECT 1 FROM pending WHERE platform=? AND owner=? AND room=? AND message_id=?",
                    (scope.platform,scope.owner,scope.room,message_id)).fetchone():
                return False
            if count >= max_queue:
                raise OverflowError("Pending queue full; check memory database and extraction settings")
            cur = self.db.execute("""INSERT OR IGNORE INTO pending
                (platform,owner,room,message_id,sender_id,text,message_at,received_at,urgent)
                VALUES(?,?,?,?,?,?,?,?,?)""", (scope.platform,scope.owner,scope.room,message_id,sender_id,text,
                                                  timestamp, now(),int(urgent)))
            return cur.rowcount > 0

    def scopes(self) -> list[Scope]:
        with self._lock:
            rows = self.db.execute("""SELECT platform,owner,room FROM pending
                UNION SELECT platform,owner,room FROM memories WHERE room!=''
                ORDER BY owner,room""").fetchall()
            return [Scope(r['platform'],r['owner'],r['room']) for r in rows]

    def due_scopes(self) -> list[Scope]:
        with self._lock:
            rows = self.db.execute("""SELECT DISTINCT platform,owner,room FROM pending
                WHERE status='pending' AND retry_after<=? ORDER BY received_at LIMIT 100""", (now(),)).fetchall()
            return [Scope(r['platform'],r['owner'],r['room']) for r in rows]

    def ready(self, scope: Scope, threshold: int, wait_minutes: int, minimum: int) -> bool:
        with self._lock:
            options = self.db.execute("SELECT paused FROM room_options WHERE platform=? AND owner=? AND room=?",
                    (scope.platform,scope.owner,scope.room)).fetchone()
            if options and options['paused']:
                return False
            row = self.db.execute("""SELECT COUNT(*) AS count, MIN(received_at) AS oldest,
                MAX(urgent) AS urgent FROM pending WHERE platform=? AND owner=? AND room=?
                AND status='pending' AND retry_after<=?""",
                (scope.platform,scope.owner,scope.room,now())).fetchone()
            count = int(row['count'])
            return count > 0 and (bool(row['urgent']) or count >= threshold or
                    (count >= minimum and row['oldest'] + wait_minutes*60 <= now()))

    def reserve(self, scope: Scope, max_items: int, max_chars: int, daily_limit: int) -> tuple[str, list[dict]]:
        """Atomically consume one daily attempt and mark a bounded batch processing."""
        with self.transaction():
            option = self.db.execute("SELECT paused FROM room_options WHERE platform=? AND owner=? AND room=?",
                (scope.platform,scope.owner,scope.room)).fetchone()
            if option and option['paused']:
                return '', []
            today = day_key()
            self.db.execute("""INSERT OR IGNORE INTO memory_usage_daily(day,platform,owner,room)
                VALUES(?,?,?,?)""",(today,scope.platform,scope.owner,scope.room))
            used = self.db.execute("""SELECT calls FROM memory_usage_daily
                WHERE day=? AND platform=? AND owner=? AND room=?""",
                (today,scope.platform,scope.owner,scope.room)).fetchone()[0]
            if used >= daily_limit:
                return '', []
            rows = self.db.execute("""SELECT id,message_id,sender_id,text,message_at,attempts FROM pending
                WHERE platform=? AND owner=? AND room=? AND status='pending' AND retry_after<=?
                ORDER BY received_at,id LIMIT ?""",
                (scope.platform,scope.owner,scope.room,now(),max_items)).fetchall()
            selected = []
            length = 0
            for row in rows:
                if length + len(row['text']) > max_chars:
                    break
                selected.append(dict(row))
                length += len(row['text'])
            if not selected:
                return '', []
            batch_id = uuid.uuid4().hex
            self.db.executemany("UPDATE pending SET status='processing', batch_id=?,attempts=attempts+1 WHERE id=?",
                                [(batch_id,r['id']) for r in selected])
            self.db.execute("""UPDATE memory_usage_daily SET calls=calls+1
                WHERE day=? AND platform=? AND owner=? AND room=?""",
                (today,scope.platform,scope.owner,scope.room))
            return batch_id,selected

    def complete(self, scope: Scope, batch_id: str, items: list[dict], rows: list[dict]) -> dict:
        source_ids = {m['message_id']: m for m in rows}
        stats = {'added':0,'merged':0,'review':0,'rejected':0}
        with self.transaction():
            batch_ids = {r['message_id'] for r in self.db.execute("""SELECT message_id FROM pending
                WHERE platform=? AND owner=? AND room=? AND status='processing' AND batch_id=?""",
                (scope.platform,scope.owner,scope.room,batch_id))}
            if batch_ids != set(source_ids):
                raise RuntimeError('Batch lease no longer matches messages')
            for item in items:
                try:
                    action = self._insert_candidate(scope, item, source_ids)
                    stats[action] += 1
                except (ValueError, TypeError, KeyError, sqlite3.IntegrityError):
                    stats['rejected'] += 1
            self.db.execute("UPDATE pending SET status='done',batch_id='' WHERE batch_id=? AND status='processing'",
                            (batch_id,))
        return stats

    def _insert_candidate(self, s: Scope, item: dict, sources: dict[str,dict]) -> str:
        from .policy import KINDS
        if not isinstance(item, dict) or item.get('kind') not in KINDS:
            raise ValueError('Invalid memory kind')
        content = safe_content(item.get('content'))
        kind = item['kind']
        scope = 'member' if kind == 'profile' else 'group'
        subject = item.get('subject_id','') if scope == 'member' else ''
        refs = item.get('source_message_ids')
        if not isinstance(refs,list) or not refs or len(refs)>10 or any(not isinstance(r,str) or r not in sources for r in refs):
            raise ValueError('Untrusted or missing message evidence')
        refs = list(dict.fromkeys(refs))
        if scope == 'member' and (not isinstance(subject,str) or not subject or any(sources[r]['sender_id']!=subject for r in refs)):
            raise ValueError('Profile subject and source authors differ')
        key = topic_key(item.get('topic_key'),content)
        finger = fingerprint(kind,content,subject)
        duplicate = self.db.execute("""SELECT id FROM memories WHERE platform=? AND owner=? AND room=? AND
            scope=? AND subject_id=? AND fingerprint=? AND status IN ('active','review') LIMIT 1""",
            (s.platform,s.owner,s.room,scope,subject,finger)).fetchone()
        if duplicate:
            mid = duplicate['id']
            action = 'merged'
        else:
            conflict = self.db.execute("""SELECT id FROM memories WHERE platform=? AND owner=? AND room=?
                AND scope=? AND subject_id=? AND topic_key=? AND status='active' LIMIT 1""",
                (s.platform,s.owner,s.room,scope,subject,key)).fetchone()
            state = 'review' if conflict or item.get('certainty') != 'confirmed' else 'active'
            mid = uuid.uuid4().hex
            self.db.execute("""INSERT INTO memories
              (id,platform,owner,room,subject_id,scope,kind,topic_key,fingerprint,content,status,
               importance,origin,created_at,updated_at,supersedes_id)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (mid,s.platform,s.owner,s.room,subject,scope,kind,key,finger,content,state,
               int(max(1,min(5,item.get('importance',2) if type(item.get('importance',2)) is int else 2))),
               'extractor',now(),now(),conflict['id'] if conflict else ''))
            action = 'review' if state=='review' else 'added'
        for rid in refs:
            m = sources[rid]
            self.db.execute("INSERT OR IGNORE INTO memory_sources(memory_id,message_id,sender_id,created_at) VALUES(?,?,?,?)",
                            (mid,rid,m['sender_id'],m['message_at']))
        return action

    def failed(self, scope: Scope, batch_id: str, error: str, delay: int) -> None:
        """Retry with fixed capped backoff; failure counts as a model attempt."""
        with self.transaction():
            self.db.execute("UPDATE pending SET status='pending',batch_id='',retry_after=? WHERE batch_id=? AND status='processing'",
                            (now()+delay,batch_id))
            self.db.execute("""UPDATE memory_usage_daily SET errors=errors+1 WHERE day=? AND platform=? AND owner=? AND room=?""",
                            (day_key(),scope.platform,scope.owner,scope.room))

    def record_usage(self, scope: Scope, tokens_in: int, tokens_out: int) -> None:
        with self.transaction():
            self.db.execute("""UPDATE memory_usage_daily SET input_tokens=input_tokens+?,output_tokens=output_tokens+?
                WHERE day=? AND platform=? AND owner=? AND room=?""",
                (max(tokens_in,0),max(tokens_out,0),day_key(),scope.platform,scope.owner,scope.room))

    def upsert_manual(self, scope: Scope, *, content: str, kind: str='fact', subject: str='', topic: str='', actor: str='dashboard') -> str:
        from .policy import KINDS
        if kind not in KINDS:
            raise ValueError('Invalid kind')
        text = safe_content(content)
        sc = 'member' if kind=='profile' else 'group'
        if sc=='member' and not subject:
            raise ValueError('Profile requires a member ID')
        subject = subject if sc=='member' else ''
        key = topic_key(topic,text)
        with self.transaction():
            existing=self.db.execute("""SELECT id FROM memories WHERE platform=? AND owner=? AND room=?
                AND scope=? AND subject_id=? AND topic_key=? AND status='active' LIMIT 1""",
                (scope.platform,scope.owner,scope.room,sc,subject,key)).fetchone()
            state='review' if existing else 'active'
            mid=uuid.uuid4().hex
            self.db.execute("""INSERT INTO memories
                (id,platform,owner,room,subject_id,scope,kind,topic_key,fingerprint,content,status,importance,origin,created_at,updated_at,supersedes_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mid,scope.platform,scope.owner,scope.room,subject,sc,kind,key,fingerprint(kind,text,subject),text,state,3,
                 'manual:'+actor[:80],now(),now(),existing['id'] if existing else ''))
            return mid

    def revise(self, scope: Scope, mid: str, *, action: str, actor: str, content: str='') -> bool:
        if action not in ('approve','edit','disable','delete'):
            raise ValueError('Invalid action')
        with self.transaction():
            row=self.db.execute("SELECT * FROM memories WHERE id=? AND platform=? AND owner=? AND room=? AND scope!='global'",
                                (mid,scope.platform,scope.owner,scope.room)).fetchone()
            if not row:
                return False
            old=dict(row)
            if action=='approve':
                if old['status']!='review':
                    raise ValueError('Only review records can be approved')
                conflict=self.db.execute("""SELECT id,content FROM memories WHERE platform=? AND owner=? AND room=? AND scope=?
                    AND subject_id=? AND topic_key=? AND status='active' AND id!=?""",
                    (scope.platform,scope.owner,scope.room,old['scope'],old['subject_id'],old['topic_key'],mid)).fetchall()
                for c in conflict:
                    self.db.execute("UPDATE memories SET status='superseded',updated_at=? WHERE id=?",(now(),c['id']))
                    self.db.execute("INSERT INTO memory_versions(memory_id,action,previous_content,actor,occurred_at) VALUES(?,?,?,?,?)",
                                    (c['id'],'superseded',c['content'],actor,now()))
                self.db.execute("UPDATE memories SET status='active',updated_at=? WHERE id=?",(now(),mid))
            elif action=='edit':
                text=safe_content(content)
                self.db.execute("UPDATE memories SET content=?,fingerprint=?,updated_at=? WHERE id=?",
                                (text,fingerprint(old['kind'],text,old['subject_id']),now(),mid))
            else:
                newstate='deleted' if action=='delete' else 'superseded'
                self.db.execute("UPDATE memories SET status=?,updated_at=? WHERE id=?",(newstate,now(),mid))
            self.db.execute("INSERT INTO memory_versions(memory_id,action,previous_content,actor,occurred_at) VALUES(?,?,?,?,?)",
                            (mid,action,old['content'],actor[:120],now()))
            return True

    def global_add(self, platform: str, owner: str, user: str, content: str, source_message_id: str) -> str:
        text=safe_content(content,300)
        key=topic_key('',text)
        finger=fingerprint('profile',text,user)
        with self.transaction():
            existing=self.db.execute("SELECT id FROM memories WHERE platform=? AND owner=? AND room='' AND subject_id=? AND fingerprint=? AND status='active'",
                (platform,owner,user,finger)).fetchone()
            if existing:
                return existing['id']
            mid=uuid.uuid4().hex
            self.db.execute("""INSERT INTO memories
                (id,platform,owner,room,subject_id,scope,kind,topic_key,fingerprint,content,status,importance,origin,created_at,updated_at)
                VALUES(?,?,?,'',?,'global','profile',?,?,?,'active',3,'user_dm',?,?)""",
                (mid,platform,owner,user,key,finger,text,now(),now()))
            return mid

    def global_list(self, platform: str, owner: str, user: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute("""SELECT id,content,created_at FROM memories
                WHERE platform=? AND owner=? AND room='' AND scope='global' AND subject_id=? AND status='active'
                ORDER BY created_at DESC LIMIT 100""",(platform,owner,user))]

    def global_delete(self, platform: str, owner: str, user: str, mid: str) -> bool:
        with self.transaction():
            row=self.db.execute("""SELECT content FROM memories WHERE id=? AND platform=? AND owner=? AND room=''
                AND scope='global' AND subject_id=? AND status='active'""",(mid,platform,owner,user)).fetchone()
            if not row:
                return False
            self.db.execute("UPDATE memories SET status='deleted',updated_at=? WHERE id=?",(now(),mid))
            self.db.execute("INSERT INTO memory_versions(memory_id,action,previous_content,actor,occurred_at) VALUES(?,?,?,?,?)",
                            (mid,'user_delete',row['content'],user,now()))
            return True

    def set_permission(self, platform: str, owner: str, user: str, room: str, allowed: bool) -> None:
        with self.transaction():
            self.db.execute("""INSERT INTO profile_permissions VALUES(?,?,?,?,?,?)
                ON CONFLICT(platform,owner,user_id,room) DO UPDATE SET
                allowed=excluded.allowed,changed_at=excluded.changed_at""",(platform,owner,user,room,int(allowed),now()))

    def list_permissions(self, platform: str, owner: str, user: str) -> list[str]:
        with self._lock:
            return [r['room'] for r in self.db.execute("""SELECT room FROM profile_permissions
                WHERE platform=? AND owner=? AND user_id=? AND allowed=1 ORDER BY room""",
                (platform,owner,user))]

    def revoke_all(self, platform: str, owner: str, user: str) -> int:
        with self.transaction():
            return self.db.execute("UPDATE profile_permissions SET allowed=0,changed_at=? WHERE platform=? AND owner=? AND user_id=? AND allowed=1",
                (now(),platform,owner,user)).rowcount

    def get_retrieval_candidates(self, s: Scope, user_id: str, include_global: bool, limit: int=1000) -> list[dict]:
        limit = min(max(1,limit),2000)
        with self._lock:
            items=[dict(r) for r in self.db.execute("""SELECT * FROM memories WHERE
                platform=? AND owner=? AND room=? AND status='active'
                AND (scope='group' OR (scope='member' AND subject_id=?))
                ORDER BY updated_at DESC LIMIT ?""",(s.platform,s.owner,s.room,user_id,limit))]
            if include_global:
                granted=self.db.execute("""SELECT 1 FROM profile_permissions WHERE platform=? AND owner=? AND
                    user_id=? AND room=? AND allowed=1""",(s.platform,s.owner,user_id,s.room)).fetchone()
                if granted:
                    items.extend(dict(r) for r in self.db.execute("""SELECT * FROM memories WHERE platform=? AND owner=?
                        AND room='' AND scope='global' AND subject_id=? AND status='active' ORDER BY updated_at DESC LIMIT 100""",
                        (s.platform,s.owner,user_id)))
        return items

    def list_memories(self,s: Scope, *, status: str='', kind: str='',query: str='',limit: int=100) -> list[dict]:
        with self._lock:
            sql="""SELECT m.*, (SELECT COUNT(*) FROM memory_sources src WHERE src.memory_id=m.id) AS source_count
                FROM memories m WHERE platform=? AND owner=? AND room=? AND scope!='global'"""
            args=[s.platform,s.owner,s.room]
            if status and status in ('active','review','superseded','deleted'):
                sql+=' AND status=?';args.append(status)
            if kind and kind in ('fact','decision','context','preference','profile'):
                sql+=' AND kind=?';args.append(kind)
            if query:
                sql+=' AND content LIKE ?';args.append('%'+query[:100].replace('%','\\%').replace('_','\\_')+'%')
                sql=sql.replace('content LIKE ?', "content LIKE ? ESCAPE '\\'")
            sql+=' ORDER BY updated_at DESC,id DESC LIMIT ?';args.append(min(max(1,limit),200))
            return [dict(r) for r in self.db.execute(sql,args)]

    def sources(self, s:Scope,mid:str)->list[dict]:
        with self._lock:
            return [dict(r) for r in self.db.execute("""SELECT src.message_id,src.sender_id,src.created_at
                FROM memory_sources src JOIN memories m ON m.id=src.memory_id
                WHERE m.id=? AND m.platform=? AND m.owner=? AND m.room=? ORDER BY src.created_at LIMIT 40""",
                (mid,s.platform,s.owner,s.room))]

    def options(self, s:Scope, *, paused: bool | None=None)->bool:
        with self.transaction() if paused is not None else self._lock:
            if paused is not None:
                self.db.execute("""INSERT INTO room_options VALUES(?,?,?,?) ON CONFLICT(platform,owner,room)
                    DO UPDATE SET paused=excluded.paused""",(s.platform,s.owner,s.room,int(paused)))
            r=self.db.execute("SELECT paused FROM room_options WHERE platform=? AND owner=? AND room=?",
                              (s.platform,s.owner,s.room)).fetchone()
            return bool(r and r['paused'])

    def stats(self, s:Scope)->dict:
        with self._lock:
            data={}
            for key,query in {
                'pending':"SELECT COUNT(*) FROM pending WHERE platform=? AND owner=? AND room=? AND status!='done'",
                'processed':"SELECT COUNT(*) FROM pending WHERE platform=? AND owner=? AND room=? AND status='done'",
                'active':"SELECT COUNT(*) FROM memories WHERE platform=? AND owner=? AND room=? AND status='active'",
                'review':"SELECT COUNT(*) FROM memories WHERE platform=? AND owner=? AND room=? AND status='review'"
            }.items():
                data[key]=self.db.execute(query,(s.platform,s.owner,s.room)).fetchone()[0]
            r=self.db.execute("""SELECT calls,input_tokens,output_tokens,errors FROM memory_usage_daily
                WHERE day=? AND platform=? AND owner=? AND room=?""",
                (day_key(),s.platform,s.owner,s.room)).fetchone()
            data['today_usage']=dict(r) if r else {'calls':0,'input_tokens':0,'output_tokens':0,'errors':0}
            return data

    def cleanup(self,retention_days:int)->int:
        cutoff=now()-max(1,retention_days)*86400
        with self.transaction():
            return self.db.execute("DELETE FROM pending WHERE status='done' AND received_at<?",(cutoff,)).rowcount

    def close(self):
        with self._lock:
            self.db.close()
