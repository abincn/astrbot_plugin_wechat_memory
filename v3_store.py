"""Dreaming V3 transactional event, claim and evidence repository.

The raw archive and legacy manual memories remain independent and untouched.
"""
from __future__ import annotations

import json
import sqlite3
import uuid

from .storage import now


def key(s):
    return s.platform,s.owner,s.room


def context(store,s,senders,*,event_limit=14,claim_limit=60):
    with store._lock:
        events=[dict(r) for r in store.db.execute(
            """SELECT id,title,event_type,anchor_date,state,summary,revision
               FROM memory_events WHERE platform=? AND owner=? AND room=?
               ORDER BY updated_at DESC LIMIT ?""",(*key(s),event_limit))]
        members=sorted(x for x in senders if not store.is_opted_out(s,x))[:60]
        conditions="subject_type!='member'"
        if members:
            placeholders=','.join('?' for _ in members)
            conditions+=f" OR (subject_type='member' AND subject_id IN ({placeholders}))"
        claims=[dict(r) for r in store.db.execute(
            f"""SELECT id,event_id,subject_type,subject_id,claim_type,predicate,content,
                       attribution,confidence,sensitivity,revision
                FROM memory_claims WHERE platform=? AND owner=? AND room=?
                AND status IN ('active','candidate','review') AND ({conditions})
                ORDER BY updated_at DESC LIMIT ?""",
            (*key(s),*members,claim_limit))]
        return events,claims


def _in_scope(db,s,table,ident):
    return db.execute(
        f"SELECT * FROM {table} WHERE id=? AND platform=? AND owner=? AND room=?",
        (ident,*key(s))).fetchone()


def commit(store,s,run_id,cursor,end,messages,proposal,*,dry_run=False,replay_job_id='',skipped=(),consolidation_member=''):
    """One atomic event/claim/evidence/ledger/cursor commit. Caller owns model budget."""
    raw={m['message_id']:m for m in messages}
    rejected=list(proposal.get('rejected_claims',[]))
    changed_events=changed_claims=added_sources=0
    with store.transaction():
        if consolidation_member:
            if store.is_opted_out(s,consolidation_member):
                raise PermissionError('Member opted out of profiling')
        elif replay_job_id:
            job=store.db.execute(
                """SELECT status,next_raw_id FROM reanalysis_jobs
                   WHERE id=? AND platform=? AND owner=? AND room=?""",
                (replay_job_id,*key(s))).fetchone()
            if not job or job['status']!='running' or int(job['next_raw_id'])!=cursor+1:
                raise RuntimeError('History job position changed')
        else:
            state=store.db.execute(
                """SELECT dream_cursor FROM room_state
                   WHERE platform=? AND owner=? AND room=?""",key(s)).fetchone()
            if not state or int(state['dream_cursor'])!=cursor:
                raise RuntimeError('Production cursor changed')

        links={}
        # Preview persists only the proposed JSON and independent job progress.
        if not dry_run:
            for item in proposal['events']:
                ident=item.get('existing_id') or ''
                source=[raw[x] for x in item['source_message_ids']]
                times=[m['message_at'] for m in source]
                if ident:
                    row=_in_scope(store.db,s,'memory_events',ident)
                    if not row:raise ValueError('Unknown event in committed scope')
                    if row['revision']!=item.get('revision'):raise RuntimeError('Stale V3 event revision')
                elif item.get('anchor_date'):
                    # Exact event title AND explicit date, never merging unrelated gatherings.
                    row=store.db.execute(
                        """SELECT * FROM memory_events WHERE platform=? AND owner=? AND room=?
                           AND title=? AND anchor_date=? AND event_type=?
                           ORDER BY updated_at DESC LIMIT 1""",
                        (*key(s),item['title'],item['anchor_date'],item['event_type'])).fetchone()
                    if row:ident=row['id']
                if ident:
                    store.db.execute(
                        """UPDATE memory_events SET summary=?,state=?,last_seen=MAX(last_seen,?),
                           updated_at=?,revision=revision+1
                           WHERE id=? AND platform=? AND owner=? AND room=?""",
                        (item['summary'],item['state'],max(times),now(),ident,*key(s)))
                else:
                    ident=uuid.uuid4().hex
                    store.db.execute(
                        """INSERT INTO memory_events
                           (id,platform,owner,room,title,event_type,anchor_date,state,summary,
                            first_seen,last_seen,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (ident,*key(s),item['title'],item['event_type'],
                         item.get('anchor_date',''),item['state'],item['summary'],
                         min(times),max(times),now(),now()))
                links[item.get('ref') or ident]=ident
                links[ident]=ident
                for m in source:
                    cur=store.db.execute(
                        'INSERT OR IGNORE INTO event_evidence(event_id,raw_id) VALUES(?,?)',
                        (ident,m['id']))
                    added_sources+=cur.rowcount
                changed_events+=1

            for item in proposal['claims']:
                subject=item.get('subject_id','')
                if item['subject_type']=='member':
                    # A person mentioned by nickname is not automatically a stable WeChat ID.
                    known=store.db.execute(
                        """SELECT 1 FROM raw_messages WHERE platform=? AND owner=? AND room=?
                           AND sender_id=? LIMIT 1""",(*key(s),subject)).fetchone()
                    if not known:
                        rejected.append({
                            'reason':'unknown_member_identity',
                            'subject_id':subject,'claim_type':item['claim_type'],
                            'content':item['content'],
                            'source_message_ids':item['source_message_ids'],
                        })
                        continue
                    if store.is_opted_out(s,subject):
                        continue
                evidence=[raw[mid] for mid in item['source_message_ids']]
                if item['attribution']=='self_statement' and (
                    item['subject_type']!='member' or any(m['sender_id']!=subject for m in evidence)):
                    raise ValueError('Invalid own-author claim')
                if item['attribution']=='observed_behavior' and item['subject_type']=='member':
                    if not any(m['sender_id']==subject for m in evidence):
                        raise ValueError('Unsubstantiated behavior')
                event_ref=item.get('event_ref','')
                event_id=links.get(event_ref,event_ref) if event_ref else None
                if event_id and not _in_scope(store.db,s,'memory_events',event_id):
                    raise ValueError('Cross-group event')
                ident=item.get('existing_id','')
                if ident:
                    row=_in_scope(store.db,s,'memory_claims',ident)
                    if not row:raise ValueError('Cross-group claim')
                    if row['revision']!=item.get('revision'):raise RuntimeError('Stale V3 claim revision')
                    if row['subject_type']!=item['subject_type'] or row['subject_id']!=subject or row['claim_type']!=item['claim_type']:
                        raise ValueError('Claim identity changed')
                else:
                    row=store.db.execute(
                        """SELECT * FROM memory_claims WHERE platform=? AND owner=? AND room=?
                           AND subject_type=? AND subject_id=? AND claim_type=? AND
                           predicate=? AND content=? AND COALESCE(event_id,'')=?
                           ORDER BY updated_at DESC LIMIT 1""",
                        (*key(s),item['subject_type'],subject,item['claim_type'],
                         item['predicate'],item['content'],event_id or '')).fetchone()
                    if row:ident=row['id']
                times=[m['message_at'] for m in evidence]
                sensitivity=item['sensitivity']
                policy='restricted_output' if sensitivity=='restricted' else 'contextual'
                confidence=item['confidence']
                status='active' if confidence in ('confirmed','supported') else 'candidate'
                if item['attribution']=='third_party_statement' and confidence=='confirmed':
                    confidence='reported';status='candidate'
                # A changed factual statement is a separate reviewable candidate, not a
                # silent overwrite of established evidence or an invisible rejected change.
                conflicting=bool(ident and row and row['content']!=item['content'])
                if conflicting:
                    ident=''
                    status='review'
                if ident:
                    # Do not silently overwrite an edited or retracted record.
                    row=_in_scope(store.db,s,'memory_claims',ident)
                    if row['status'] in ('retracted','superseded'):continue
                    manually_reviewed=store.db.execute(
                        """SELECT 1 FROM memory_claim_versions WHERE claim_id=? LIMIT 1""",
                        (ident,)).fetchone()
                    if row['confidence']!=confidence and not manually_reviewed:
                        store.db.execute(
                            """UPDATE memory_claims SET confidence=?,status=?,
                               last_supported=MAX(last_supported,?),updated_at=?,
                               revision=revision+1 WHERE id=?""",
                            (confidence,status,max(times),now(),ident))
                    else:
                        store.db.execute(
                            'UPDATE memory_claims SET last_supported=MAX(last_supported,?),updated_at=? WHERE id=?',
                            (max(times),now(),ident))
                else:
                    ident=uuid.uuid4().hex
                    store.db.execute(
                        """INSERT INTO memory_claims
                           (id,platform,owner,room,event_id,subject_type,subject_id,
                            claim_type,predicate,content,attribution,speaker_id,confidence,
                            sensitivity,use_policy,status,first_seen,last_supported,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (ident,*key(s),event_id,item['subject_type'],subject,item['claim_type'],
                         item['predicate'],item['content'],item['attribution'],
                         item.get('speaker_id',''),confidence,sensitivity,policy,status,
                         min(times),max(times),now(),now()))
                relation='reported' if item['attribution']=='third_party_statement' else 'support'
                for m in evidence:
                    cur=store.db.execute(
                        'INSERT OR IGNORE INTO claim_evidence(claim_id,raw_id,relation) VALUES(?,?,?)',
                        (ident,m['id'],relation))
                    added_sources+=cur.rowcount
                if item['subject_type']=='member':
                    store.db.execute(
                        """INSERT INTO consolidation_queue(platform,owner,room,subject_id,status,last_raw_id,updated_at)
                           VALUES(?,?,?,?,'pending',?,?)
                           ON CONFLICT(platform,owner,room,subject_id) DO UPDATE SET
                            status='pending',last_raw_id=MAX(last_raw_id,excluded.last_raw_id),
                            updated_at=excluded.updated_at""",
                        (*key(s),subject,max(m['id'] for m in evidence),now()))
                changed_claims+=1

            # Rejected attribution is not silently counted as 'no memory signal'.
            # It stays traceable as a deferred source even when another valid claim
            # references the same raw message.
            sourced={mid for e in proposal['events'] for mid in e['source_message_ids']}
            sourced.update(mid for c in proposal['claims'] for mid in c['source_message_ids'])
            rejected_sources={mid for claim in rejected for mid in claim['source_message_ids']}
            for m in ([] if consolidation_member else messages):
                mid=m['message_id']
                result=('deferred' if mid in rejected_sources
                        else 'analyzed_with_evidence' if mid in sourced
                        else 'analyzed_no_signal')
                reason='claim_attribution_requires_reanalysis' if result=='deferred' else ''
                store.db.execute(
                    """INSERT INTO analysis_ledger
                       (platform,owner,room,raw_id,pipeline_version,result,reason,run_id,updated_at)
                       VALUES(?,?,?,?,3,?,?,?,?)
                       ON CONFLICT(platform,owner,room,raw_id,pipeline_version) DO UPDATE SET
                         result=excluded.result,reason=excluded.reason,
                         run_id=excluded.run_id,updated_at=excluded.updated_at""",
                    (*key(s),m['id'],result,reason,run_id,now()))

        if not dry_run:
            for m in skipped:
                store.db.execute(
                    """INSERT INTO analysis_ledger
                       (platform,owner,room,raw_id,pipeline_version,result,reason,run_id,updated_at)
                       VALUES(?,?,?,?,3,?,?,?,?)
                       ON CONFLICT(platform,owner,room,raw_id,pipeline_version) DO UPDATE SET
                         result=excluded.result,reason=excluded.reason,
                         run_id=excluded.run_id,updated_at=excluded.updated_at""",
                    (*key(s),m['id'],m['reason'],m['detail'],run_id,now()))
        if consolidation_member:
            store.db.execute(
                """UPDATE consolidation_queue SET status='completed',updated_at=?
                   WHERE platform=? AND owner=? AND room=? AND subject_id=?""",
                (now(),*key(s),consolidation_member))
        elif replay_job_id:
            cur=store.db.execute(
                """UPDATE reanalysis_jobs SET next_raw_id=?,batches_done=batches_done+1,
                   messages_scanned=messages_scanned+?,updated_at=?
                   WHERE id=? AND platform=? AND owner=? AND room=?
                   AND status='running' AND next_raw_id=?""",
                (end+1,len(messages),now(),replay_job_id,*key(s),cursor+1))
            if cur.rowcount!=1:raise RuntimeError('History job changed during commit')
        elif not dry_run:
            cur=store.db.execute(
                """UPDATE room_state SET dream_cursor=?,last_success=?,next_attempt=0,last_error=''
                   WHERE platform=? AND owner=? AND room=? AND dream_cursor=?""",
                (end,now(),*key(s),cursor))
            if cur.rowcount!=1:raise RuntimeError('Production cursor changed during V3 commit')
        sourced={mid for e in proposal['events'] for mid in e['source_message_ids']}
        sourced.update(mid for c in proposal['claims'] for mid in c['source_message_ids'])
        rejected_sources={mid for claim in rejected for mid in claim['source_message_ids']}
        summary={
            'events':changed_events,'claims':changed_claims,
            'sources':added_sources,'analyzed':len(messages),
            'no_signal':len(messages)-len(sourced|rejected_sources),
            'deferred':len(rejected_sources),
            'rejected_claims':len(rejected),
        }
        store.db.execute(
            """INSERT INTO v3_run_details
               (run_id,analyzed,no_signal,events_written,claims_written,sources_added,mode,preview_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (run_id,summary['analyzed'],summary['no_signal'],changed_events,changed_claims,
             added_sources,'replay_preview' if dry_run and replay_job_id else
             'preview' if dry_run else 'profile_consolidation' if consolidation_member else 'replay_apply' if replay_job_id else 'live',
             json.dumps(proposal if dry_run else {'rejected_claims':rejected},
                        ensure_ascii=False)[:100000] if dry_run or rejected else ''))
        store.db.execute(
            """UPDATE dream_runs SET status=?,changes_count=?,finished_at=? WHERE id=?""",
            ('preview' if dry_run else 'committed',changed_claims,now(),run_id))
        return summary


def profiles(store,s,limit=150,member=''):
    with store._lock:
        clause=' AND subject_id=?' if member else ''
        rows=store.db.execute(
            """SELECT * FROM memory_claims
               WHERE platform=? AND owner=? AND room=?
                 AND subject_type='member' AND status IN ('active','candidate','review')"""
            +clause+" ORDER BY last_supported DESC LIMIT ?",
            (*key(s),*((member,) if member else ()),max(1,min(limit,200)))).fetchall()
        return [dict(x) for x in rows if not store.is_opted_out(s,x['subject_id'])]


def events(store,s,limit=100):
    with store._lock:
        return [dict(x) for x in store.db.execute(
            'SELECT * FROM memory_events WHERE platform=? AND owner=? AND room=? ORDER BY updated_at DESC LIMIT ?',
            (*key(s),max(1,min(limit,200))))]


def claims(store,s,limit=150,event_id='',subject=''):
    with store._lock:
        conditions=['platform=?','owner=?','room=?']
        args=list(key(s))
        if event_id:conditions.append('event_id=?');args.append(event_id)
        if subject:conditions.append("subject_type='member' AND subject_id=?");args.append(subject)
        sql='SELECT * FROM memory_claims WHERE '+' AND '.join(conditions)+' ORDER BY updated_at DESC LIMIT ?'
        rows=[dict(r) for r in store.db.execute(sql,(*args,max(1,min(limit,250))))]
        return [x for x in rows if x['subject_type']!='member' or not store.is_opted_out(s,x['subject_id'])]


def sources(store,s,claim_id,limit=80):
    with store._lock:
        if not _in_scope(store.db,s,'memory_claims',claim_id):raise ValueError('Claim not in this group')
        return [dict(r) for r in store.db.execute(
            """SELECT r.message_id,r.sender_id,r.message_at,r.text,x.relation
               FROM claim_evidence x JOIN raw_messages r ON r.id=x.raw_id
               WHERE x.claim_id=? AND r.platform=? AND r.owner=? AND r.room=?
               ORDER BY r.message_at DESC LIMIT ?""",
            (claim_id,*key(s),max(1,min(limit,100))))]


def coverage(store,s):
    with store._lock:
        return [dict(r) for r in store.db.execute(
            """SELECT result,COUNT(*) count FROM analysis_ledger
               WHERE platform=? AND owner=? AND room=? AND pipeline_version=3
               GROUP BY result""",key(s))]


def stats(store,s):
    with store._lock:
        return {
            'events':store.db.execute('SELECT COUNT(*) FROM memory_events WHERE platform=? AND owner=? AND room=?',key(s)).fetchone()[0],
            'claims':store.db.execute('SELECT COUNT(*) FROM memory_claims WHERE platform=? AND owner=? AND room=?',key(s)).fetchone()[0],
            'profiles':store.db.execute("""SELECT COUNT(DISTINCT subject_id) FROM memory_claims
               WHERE platform=? AND owner=? AND room=? AND subject_type='member'
               AND status='active'""",key(s)).fetchone()[0],
            'pending_consolidation':store.db.execute("""SELECT COUNT(*) FROM consolidation_queue
               WHERE platform=? AND owner=? AND room=? AND status='pending'""",key(s)).fetchone()[0],
            'coverage':coverage(store,s),
        }


def erase_member(store,s,user):
    """Called from the existing per-room erase path, in its existing transaction."""
    store.db.execute('DELETE FROM memory_claims WHERE platform=? AND owner=? AND room=? AND subject_type=? AND subject_id=?',
                     (*key(s),'member',user))
    store.db.execute('DELETE FROM consolidation_queue WHERE platform=? AND owner=? AND room=? AND subject_id=?',
                     (*key(s),user))
    # Remove any claim whose evidence vanished after raw-message deletion.
    store.db.execute(
        """UPDATE memory_claims SET status='review',updated_at=?,revision=revision+1
           WHERE platform=? AND owner=? AND room=?
           AND status IN ('active','candidate') AND
           NOT EXISTS(SELECT 1 FROM claim_evidence e WHERE e.claim_id=memory_claims.id)""",
        (now(),*key(s)))


def revise_claim(store,s,ident,expected,action,actor,content=''):
    """Administrator CAS edit/approve/retract with a durable previous-version record."""
    if action not in ('edit','approve','retract'):
        raise ValueError('Unsupported V3 claim action')
    if not isinstance(expected,int):raise ValueError('Expected revision required')
    with store.transaction():
        r=_in_scope(store.db,s,'memory_claims',ident)
        if not r:raise ValueError('Claim not in group')
        if r['revision']!=expected:raise ValueError('Claim revision changed')
        if action=='edit':
            if not isinstance(content,str) or not 1<=len(content.strip())<=600:
                raise ValueError('Invalid edited claim content')
            new_content=content.strip()
            status='active'
        elif action=='approve':
            if r['status'] not in ('candidate','review'):raise ValueError('Claim is not pending review')
            new_content=r['content'];status='active'
        else:
            new_content=r['content'];status='retracted'
        store.db.execute(
            """INSERT INTO memory_claim_versions
               (claim_id,revision,previous_content,previous_status,actor,reason,occurred_at)
               VALUES(?,?,?,?,?,?,?)""",
            (ident,r['revision'],r['content'],r['status'],actor[:100],action,now()))
        cur=store.db.execute(
            """UPDATE memory_claims SET content=?,status=?,revision=revision+1,updated_at=?
               WHERE id=? AND platform=? AND owner=? AND room=? AND revision=?""",
            (new_content,status,now(),ident,*key(s),expected))
        if cur.rowcount!=1:raise RuntimeError('Concurrent claim edit')
        return {'id':ident,'revision':expected+1,'status':status}


def claim_versions(store,s,ident,limit=60):
    with store._lock:
        if not _in_scope(store.db,s,'memory_claims',ident):
            raise ValueError('Claim not in group')
        return [dict(r) for r in store.db.execute(
            """SELECT revision,previous_content,previous_status,actor,reason,occurred_at
               FROM memory_claim_versions WHERE claim_id=? ORDER BY id DESC LIMIT ?""",
            (ident,max(1,min(limit,100))))]


def event_sources(store,s,event_id,limit=80):
    with store._lock:
        if not _in_scope(store.db,s,'memory_events',event_id):
            raise ValueError('Event is outside this group')
        return [dict(r) for r in store.db.execute(
            """SELECT r.message_id,r.sender_id,r.message_at,r.text
               FROM event_evidence e JOIN raw_messages r ON r.id=e.raw_id
               WHERE e.event_id=? AND r.platform=? AND r.owner=? AND r.room=?
               ORDER BY r.message_at DESC LIMIT ?""",
            (event_id,*key(s),max(1,min(limit,100))))]
