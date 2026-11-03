"""Validate untrusted LLM proposals and atomically commit only scoped changes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid

from .policy import safe_content, topic_key
from .storage import now

KINDS={'fact','decision','context','preference','profile'}
OPS={'add','revise','supersede','review'}


def _validated(scope,changes,messages,existing,max_changes:int):
    if len(changes)>max_changes:raise ValueError('Run change limit exceeded')
    source={m['message_id']:m for m in messages}
    active={m['id']:m for m in existing}
    output=[]
    for i,c in enumerate(changes):
        op=c.get('op');kind=c.get('kind');level=c.get('scope')
        if op not in OPS or kind not in KINDS or level not in ('group','member'):
            raise ValueError('Invalid operation, memory type or scope')
        subject=c.get('subject_id','')
        if not isinstance(subject,str) or len(subject)>256:raise ValueError('Invalid subject')
        if level=='group' and subject:raise ValueError('Group fact cannot target a member')
        refs=c.get('source_message_ids')
        if not isinstance(refs,list) or not 1<=len(refs)<=20 or not all(isinstance(r,str) and r in source for r in refs):
            raise ValueError('Unknown evidence source')
        refs=list(dict.fromkeys(refs))
        certainty=c.get('certainty','uncertain')
        evidence_type=c.get('evidence_type','other')
        if certainty not in ('explicit','uncertain') or evidence_type not in ('self_report','group_decision','other'):
            raise ValueError('Invalid evidence classification')
        if level=='member':
            if any(source[r].get('source')=='media_derived' for r in refs):
                certainty='uncertain'
            if not subject or kind!='profile' or evidence_type!='self_report' or any(source[r]['sender_id']!=subject for r in refs):
                raise ValueError('Member profile requires evidence authored by the same member')
        if any(source[r].get('source')=='media_derived' for r in refs):
            certainty='uncertain'
        content=safe_content(c.get('content'),1000)
        key=topic_key(c.get('topic_key'),content)
        reason=str(c.get('reason',''))[:300]
        target='';revision=None
        if op in ('revise','supersede'):
            target=c.get('memory_id','')
            revision=c.get('expected_revision')
            if not isinstance(target,str) or target not in active or type(revision) is not int:
                raise ValueError('Target or expected revision missing')
            m=active[target]
            if m['scope']!=level or m['subject_id']!=subject or m['revision']!=revision:
                raise ValueError('Target scope/version mismatch')
        elif c.get('memory_id'):
            raise ValueError('Add/review cannot target existing memory')
        output.append({'index':i,'op':op,'scope':level,'subject':subject,'kind':kind,'key':key,
            'content':content,'refs':refs,'certainty':certainty,'evidence_type':evidence_type,
            'reason':reason,'target':target,'revision':revision})
    return output


def apply(store,s,run_id,from_cursor,to_cursor,changes,messages,existing,*,dry_run=False,max_changes=8):
    """All DB writes (including success cursor) are one atomic commit. No calls to LLM."""
    parsed=_validated(s,changes,messages,existing,max_changes)
    for c in parsed:
        if c['scope']=='member' and store.is_opted_out(s,c['subject']):
            raise PermissionError('Member has opted out of group profiling')
    touched=set();changed=0;reviewed=0;ignored=0
    sources=store.raw_by_ids(s,[i for c in parsed for i in c['refs']])
    if any(r not in sources for c in parsed for r in c['refs']):
        raise ValueError('Raw source disappeared before commit')
    with store.transaction():
        state=store.db.execute('''SELECT dream_cursor FROM room_state WHERE platform=? AND owner=? AND room=?''',
            store._key(s)).fetchone()
        if not state or state['dream_cursor']!=from_cursor:raise RuntimeError('Stale Dreaming cursor')
        for c in parsed:
            old=None
            if c['target']:
                old=store.db.execute('''SELECT * FROM memories WHERE id=? AND platform=? AND owner=? AND room=?
                   AND status='active' ''',(c['target'],*store._key(s))).fetchone()
                if not old or old['revision']!=c['revision'] or old['scope']!=c['scope'] or old['subject_id']!=c['subject']:
                    raise RuntimeError('Stale memory revision: manual edit wins')
            cid=uuid.uuid4().hex
            previous=old['content'] if old else ''
            status='proposed' if dry_run else 'applied'
            applied_id=''
            if not dry_run:
                if c['op']=='add' and c['certainty']=='explicit':
                    # Only exact normalized duplicates are safe to deduplicate without another LLM call.
                    normalized=''.join(c['content'].lower().split())
                    existing_row=store.db.execute('''SELECT * FROM memories WHERE platform=? AND owner=? AND room=?
                        AND scope=? AND subject_id=? AND status='active' ''',
                        (*store._key(s),c['scope'],c['subject'])).fetchall()
                    dup=next((r for r in existing_row if ''.join(r['content'].lower().split())==normalized),None)
                    if dup:
                        applied_id=dup['id'];status='noop';ignored+=1
                if not applied_id:
                    if c['op']=='revise' and c['certainty']=='explicit':
                        store._version(old,'dream_revise','dream',run_id)
                        store.db.execute('''UPDATE memories SET content=?,topic_key=?,revision=revision+1,
                           updated_at=? WHERE id=?''',(c['content'],c['key'],now(),old['id']))
                        applied_id=old['id'];changed+=1
                        store.db.execute('DELETE FROM embedding_vectors WHERE memory_id=?',(applied_id,))
                    else:
                        # Supersede/review/uncertain changes are *not* silently promoted.
                        is_review=c['op'] in ('supersede','review') or c['certainty']=='uncertain'
                        mid=uuid.uuid4().hex;applied_id=mid
                        store.db.execute('''INSERT INTO memories(id,platform,owner,room,scope,subject_id,kind,topic_key,
                            content,status,origin,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (mid,*store._key(s),c['scope'],c['subject'],c['kind'],c['key'],c['content'],
                             'review' if is_review else 'active','dream',now(),now()))
                        status='review' if is_review else 'applied'
                        reviewed+=int(is_review);changed+=int(not is_review)
                for m in c['refs']:
                    store.db.execute('INSERT OR IGNORE INTO memory_sources(memory_id,raw_id) VALUES(?,?)',
                                     (applied_id,sources[m]['id']))
                touched.add(c['subject'])
            store.db.execute('''INSERT INTO dream_candidates(id,run_id,op_index,action,memory_id,subject_id,scope,
                 old_content,new_content,reason,sources_json,expected_revision,status,applied_memory_id,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (cid,run_id,c['index'],c['op'],c['target'],c['subject'],c['scope'],previous,c['content'],
                  c['reason'],json.dumps(c['refs'],ensure_ascii=False),c['revision'],status,applied_id,now()))
        if not dry_run:
            store.db.execute('''UPDATE room_state SET dream_cursor=?,last_success=?,next_attempt=0,last_error=''
                WHERE platform=? AND owner=? AND room=? AND dream_cursor=?''',
                (to_cursor,now(),*store._key(s),from_cursor))
        store.db.execute('''UPDATE dream_runs SET status=?,changes_count=?,finished_at=? WHERE id=?''',
                         ('preview' if dry_run else 'committed',changed,now(),run_id))
    if not dry_run:
        for subject in touched:store.refresh_snapshot(s,subject)
    return {'changed':changed,'review':reviewed,'noop':ignored,'proposals':len(parsed),'cursor':to_cursor}


def resolve_candidate(store,s,candidate_id,action,actor):
    """Manual approval/rejection of an uncommitted review record with CAS."""
    if action not in ('approve','reject'):raise ValueError('Unknown review action')
    with store.transaction():
        c=store.db.execute('''SELECT c.*,r.platform,r.owner,r.room FROM dream_candidates c
            JOIN dream_runs r ON r.id=c.run_id WHERE c.id=? AND r.platform=? AND r.owner=? AND r.room=?''',
            (candidate_id,*store._key(s))).fetchone()
        if not c or c['status']!='review':raise ValueError('Candidate not pending review')
        memory=store.db.execute('''SELECT * FROM memories WHERE id=? AND platform=? AND owner=? AND room=?''',
                                (c['applied_memory_id'],*store._key(s))).fetchone()
        if not memory or memory['status']!='review':raise ValueError('Review memory changed')
        if action=='approve':
            # Existing target remains active until approval; latest revision must still match.
            if c['memory_id']:
                prev=store.db.execute('''SELECT * FROM memories WHERE id=? AND platform=? AND owner=? AND room=?''',
                    (c['memory_id'],*store._key(s))).fetchone()
                if not prev or prev['revision']!=c['expected_revision'] or prev['status']!='active':
                    raise ValueError('Target changed; reject and rerun instead')
                store._version(prev,'review_superseded',actor,c['run_id'])
                store.db.execute("UPDATE memories SET status='superseded',revision=revision+1,updated_at=? WHERE id=?",
                                 (now(),prev['id']))
            store._version(memory,'review_approved',actor,c['run_id'])
            store.db.execute("UPDATE memories SET status='active',revision=revision+1,updated_at=? WHERE id=?",
                             (now(),memory['id']))
        else:
            store._version(memory,'review_rejected',actor,c['run_id'])
            store.db.execute("UPDATE memories SET status='deleted',revision=revision+1,updated_at=? WHERE id=?",
                             (now(),memory['id']))
        store.db.execute('UPDATE dream_candidates SET status=? WHERE id=?',
                         ('approved' if action=='approve' else 'rejected',candidate_id))
    store.refresh_snapshot(s,memory['subject_id'])
    return True
