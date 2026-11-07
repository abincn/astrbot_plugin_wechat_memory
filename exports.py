"""Bounded management exports from a consistent SQLite snapshot."""
import json
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

TABLES=('memories','memory_events','memory_claims','analysis_ledger','raw_messages',
        'member_aliases','profile_progress','model_calls','retrieval_audit')
LINKS={'claim_evidence':('memory_claims','claim_id'),
       'claim_spans':('memory_claims','claim_id'),
       'claim_links':('memory_claims','source_id'),
       'memory_claim_versions':('memory_claims','claim_id'),
       'event_evidence':('memory_events','event_id'),
       'event_versions':('memory_events','event_id'),
       'memory_sources':('memories','memory_id'),
       'memory_versions':('memories','memory_id'),
       'fragment_progress':('raw_messages','raw_id'),
       'alias_sources':('member_aliases','alias_id'),
       'alias_versions':('member_aliases','alias_id')}


class ExportSessions:
    def __init__(self,store):
        self.store=store;self.sessions={};self.lock=threading.RLock()
        self.directory=tempfile.TemporaryDirectory(prefix='wechat_memory_export_')

    def close(self):
        with self.lock:
            self.sessions.clear();self.directory.cleanup()

    def _prune(self):
        for token,item in list(self.sessions.items()):
            if item['expires']<time.time():
                item['path'].unlink(missing_ok=True);del self.sessions[token]

    def prepare(self,s,actor):
        from .people import key
        with self.lock:
            self._prune()
            if len(self.sessions)>=4:raise ValueError('导出任务过多，请完成或取消已有导出')
            token=uuid.uuid4().hex;path=Path(self.directory.name)/(token+'.sqlite3')
            target=sqlite3.connect(str(path))
            try:
                with self.store._lock:self.store.db.backup(target)
                epoch=target.execute('SELECT privacy_epoch FROM room_state WHERE platform=? AND owner=? AND room=?',key(s)).fetchone()[0]
            finally:target.close()
            self.sessions[token]={'epoch':epoch,'path':path,'scope':key(s),'actor':actor,'expires':time.time()+600,'position':(0,0)}
            return {'token':token,'expires_in':600,'format':'jsonl','created_at':int(time.time())}

    def next(self,s,actor,token,position):
        from .people import key
        with self.lock:
            self._prune();item=self.sessions.get(token)
            if not item or item['scope']!=key(s) or item['actor']!=actor:raise PermissionError('Invalid export session')
            with self.store._lock:
                epoch=self.store.db.execute('SELECT privacy_epoch FROM room_state WHERE platform=? AND owner=? AND room=?',key(s)).fetchone()[0]
            if epoch!=item['epoch']:
                item['path'].unlink(missing_ok=True);del self.sessions[token]
                raise ValueError('资料权限或清除状态变化，请重新导出')
            expected=tuple(position) if isinstance(position,list) else (0,0)
            if expected!=item['position']:raise ValueError('Export position changed')
            index,offset=expected;names=(*TABLES,*LINKS)
            if index>=len(names):return {'lines':[],'done':True,'position':list(expected)}
            table=names[index]
            db=sqlite3.connect(str(item['path']));db.row_factory=sqlite3.Row
            try:
                if table in TABLES:
                    query='SELECT t.* FROM '+table+' t WHERE t.platform=? AND t.owner=? AND t.room=? ORDER BY t.rowid LIMIT 200 OFFSET ?'
                else:
                    parent,column=LINKS[table]
                    query='SELECT t.* FROM '+table+' t JOIN '+parent+' p ON p.id=t.'+column+' WHERE p.platform=? AND p.owner=? AND p.room=? ORDER BY t.rowid LIMIT 200 OFFSET ?'
                rows=[dict(r) for r in db.execute(query,(*key(s),offset))]
            finally:db.close()
            # Bound both records and response size, including very long raw messages.
            lines=[];size=0
            for row in rows:
                line=json.dumps({'table':table,'record':row},ensure_ascii=False)
                if lines and size+len(line)>500000:break
                lines.append(line);size+=len(line)
            position=(index,offset+len(lines)) if lines else (index+1,0)
            item['position']=position;item['expires']=time.time()+600
            return {'lines':lines,'done':position[0]>=len(names),'position':list(position)}

    def cancel(self,s,actor,token):
        from .people import key
        with self.lock:
            item=self.sessions.get(token)
            if not item:return
            if item['scope']!=key(s) or item['actor']!=actor:raise PermissionError('Invalid export session')
            item['path'].unlink(missing_ok=True);del self.sessions[token]
