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
            'reason':reason,'target':target,'revision':revision,
            'observation_id':c.get('observation_id','')})
    return output



# V2 content is validated before acquiring a database write transaction.
def _validate_v2(s, payload, messages, existing_topics, existing_observations):
    from .policy import safe_content
    by_message = {m['message_id']: m for m in messages}
    old_topics = {r['id']: r for r in existing_topics}
    old_observations = {r['id']: r for r in existing_observations}

    def refs(row):
        value = row.get('source_message_ids')
        if not isinstance(value,list) or not 1 <= len(value) <= 20 or any(
                not isinstance(mid,str) or mid not in by_message for mid in value):
            raise ValueError('Invalid V2 source_message_ids')
        return list(dict.fromkeys(value))

    topics = []
    topic_refs = set(old_topics)
    for row in payload['topics']:
        ident = row.get('id') or ''
        if not isinstance(ident,str) or (ident and ident not in old_topics):
            raise ValueError('V2 topic outside selected group/context')
        expected = int(row.get('revision',0))
        if ident and expected != old_topics[ident]['revision']:
            raise ValueError('Stale V2 topic version')
        title = safe_content(row.get('title'),100)
        summary = safe_content(row.get('summary'),1200)
        state = row.get('state')
        if state not in ('active','concluded','disputed','closed'):
            raise ValueError('Invalid V2 topic state')
        alias = row.get('ref','')
        if not isinstance(alias,str) or len(alias)>32 or (alias and not re.fullmatch(r'[A-Za-z0-9_-]+',alias)):
            raise ValueError('Invalid V2 topic reference')
        if alias and alias in topic_refs:
            raise ValueError('Duplicate V2 topic reference')
        if alias: topic_refs.add(alias)
        topics.append((ident,expected,title,summary,state,refs(row),alias))
    observations = []
    for row in payload['observations']:
        ident = row.get('id') or ''
        if not isinstance(ident,str) or (ident and ident not in old_observations):
            raise ValueError('V2 observation outside selected group/context')
        expected = int(row.get('revision',0))
        if ident and expected != old_observations[ident]['revision']:
            raise ValueError('Stale V2 observation version')
        scope = row.get('scope')
        subject = row.get('subject_id','')
        signal_type = row.get('signal_type')
        evidence_type = row.get('evidence_type')
        if scope not in ('group','member') or not isinstance(subject,str) or len(subject)>256:
            raise ValueError('Invalid V2 observation scope')
        if (scope=='group' and subject) or (scope=='member' and not subject):
            raise ValueError('Invalid V2 observation subject')
        if signal_type not in ('explicit_fact','explicit_preference','long_term_plan',
                               'group_decision','ongoing_task','topic_participation'):
            raise ValueError('Invalid V2 observation type')
        if evidence_type not in ('self_report','group_decision','other'):
            raise ValueError('Invalid V2 observation evidence')
        evidence = refs(row)
        if scope == 'member' and (evidence_type != 'self_report' or
                any(by_message[mid]['sender_id'] != subject for mid in evidence)):
            raise ValueError('Member observations require same-author direct evidence')
        if ident and (old_observations[ident]['scope'] != scope or
                      old_observations[ident]['subject_id'] != subject or
                      old_observations[ident]['signal_type'] != signal_type):
            raise ValueError('V2 observation identity mismatch')
        topic_ref=row.get('topic_ref','')
        if not isinstance(topic_ref,str) or (topic_ref and topic_ref not in topic_refs):
            raise ValueError('Observation references unknown topic')
        observations.append((ident,expected,scope,subject,signal_type,
                             safe_content(row.get('content'),500),evidence_type,evidence,topic_ref))
    return topics, observations


def _write_v2(store,s,topics,observations,messages,existing_topics):
    """Called within apply() transaction, before advancing dream_cursor."""
    from .policy import fingerprint
    by_message = {m['message_id']:m for m in messages}
    key = store._key(s)
    topics_written = 0
    observations_written = 0
    topic_links={t['id']:t['id'] for t in existing_topics}
    topic_sources={}
    for ident,expected,title,summary,state,refs,alias in topics:
        timestamps = [by_message[mid]['message_at'] for mid in refs]
        if ident:
            cur=store.db.execute(
                """UPDATE topic_states SET title=?,summary=?,state=?,revision=revision+1,
                   last_message_at=?,updated_at=?
                   WHERE id=? AND platform=? AND owner=? AND room=? AND revision=?""",
                (title,summary,state,max(timestamps),now(),ident,*key,expected))
            if cur.rowcount != 1:
                raise RuntimeError('V2 topic changed during extraction')
        else:
            ident = uuid.uuid4().hex
            store.db.execute(
                """INSERT INTO topic_states(id,platform,owner,room,title,summary,state,
                    first_message_at,last_message_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (ident,*key,title,summary,state,min(timestamps),max(timestamps),now(),now()))
        topic_links[ident]=ident
        if alias:topic_links[alias]=ident
        topic_sources[ident]=set(refs)
        for mid in refs:
            store.db.execute(
                "INSERT OR IGNORE INTO topic_sources(topic_id,raw_id) VALUES(?,?)",
                (ident,by_message[mid]['id']))
        topics_written += 1
    for ident,expected,scope,subject,kind,content,evidence_type,refs,topic_ref in observations:
        if scope == 'member' and store.is_opted_out(s,subject):
            raise PermissionError('Member has opted out of observation collection')
        timestamps = [by_message[mid]['message_at'] for mid in refs]
        signal_key = fingerprint(kind,content,subject)
        linked_topic=topic_links.get(topic_ref) if topic_ref else None
        if not linked_topic:
            matched=[tid for tid,trefs in topic_sources.items() if trefs.intersection(refs)]
            if len(matched)==1:linked_topic=matched[0]
        if ident:
            cur=store.db.execute(
                """UPDATE observation_signals SET last_seen_at=?,updated_at=?,
                   topic_id=COALESCE(?,topic_id),revision=revision+1
                   WHERE id=? AND platform=? AND owner=? AND room=?
                   AND revision=? AND status='observing'""",
                (max(timestamps),now(),linked_topic,ident,*key,expected))
            if cur.rowcount != 1:
                raise RuntimeError('V2 observation changed during extraction')
        else:
            row=store.db.execute(
                """SELECT id FROM observation_signals WHERE platform=? AND owner=? AND room=?
                   AND scope=? AND subject_id=? AND signal_key=?""",
                (*key,scope,subject,signal_key)).fetchone()
            if row:
                ident=row['id']
                store.db.execute(
                    """UPDATE observation_signals SET last_seen_at=?,updated_at=?,
                       topic_id=COALESCE(?,topic_id),revision=revision+1
                       WHERE id=? AND status='observing'""",
                    (max(timestamps),now(),linked_topic,ident))
            else:
                ident=uuid.uuid4().hex
                store.db.execute(
                    """INSERT INTO observation_signals
                       (id,platform,owner,room,scope,subject_id,signal_type,signal_key,
                        content,status,evidence_type,topic_id,first_seen_at,last_seen_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ident,*key,scope,subject,kind,signal_key,content,'observing',
                     evidence_type,linked_topic,min(timestamps),max(timestamps),now(),now()))
        for mid in refs:
            store.db.execute(
                """INSERT OR IGNORE INTO observation_sources
                   (observation_id,raw_id,relation) VALUES(?,?,'support')""",
                (ident,by_message[mid]['id']))
        observations_written += 1
    return topics_written, observations_written



def apply(store,s,run_id,from_cursor,to_cursor,changes,messages,existing,*,dry_run=False,max_changes=8,
          v2=None, existing_topics=(), existing_observations=(), replay_job_id=''):
    """All DB writes (including success cursor) are one atomic commit. No calls to LLM."""
    parsed=_validated(s,changes,messages,existing,max_changes)
    v2_parts = _validate_v2(s,v2,messages,existing_topics,existing_observations) if v2 is not None else None
    valid_observations={r['id'] for r in existing_observations} if v2 is not None else set()
    for c in parsed:
        obs_id=c['observation_id']
        if obs_id and (not isinstance(obs_id,str) or obs_id not in valid_observations):
            raise ValueError('Unknown observation source for promotion')
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
        if replay_job_id:
            job=store.db.execute(
                """SELECT next_raw_id,status FROM reanalysis_jobs WHERE id=?
                   AND platform=? AND owner=? AND room=?""",
                (replay_job_id,*store._key(s))).fetchone()
            if not job or job['status']!='running' or job['next_raw_id']!=from_cursor+1:
                raise RuntimeError('History replay position changed')
        elif not state or state['dream_cursor']!=from_cursor:
            raise RuntimeError('Stale Dreaming cursor')
        t_written = o_written = 0
        if v2_parts is not None and not dry_run:
            t_written,o_written=_write_v2(store,s,*v2_parts,messages,existing_topics)
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
                if c['observation_id'] and c['op']=='add' and c['certainty']=='explicit' and status in ('applied','noop'):
                    obs_id=c['observation_id']
                    promoted=store.db.execute(
                        """UPDATE observation_signals SET status='promoted',
                           promoted_memory_id=?,revision=revision+1,updated_at=?
                           WHERE id=? AND platform=? AND owner=? AND room=?
                           AND scope=? AND subject_id=? AND status='observing'""",
                        (applied_id,now(),obs_id,*store._key(s),c['scope'],c['subject']))
                    if promoted.rowcount!=1:
                        raise RuntimeError('Observation changed before promotion')
                    store.db.execute(
                        """INSERT OR IGNORE INTO memory_sources(memory_id,raw_id)
                           SELECT ?,raw_id FROM observation_sources WHERE observation_id=?
                           AND relation='support'""",(applied_id,obs_id))
            store.db.execute('''INSERT INTO dream_candidates(id,run_id,op_index,action,memory_id,subject_id,scope,
                 old_content,new_content,reason,sources_json,expected_revision,status,applied_memory_id,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                 (cid,run_id,c['index'],c['op'],c['target'],c['subject'],c['scope'],previous,c['content'],
                  c['reason'],json.dumps(c['refs'],ensure_ascii=False),c['revision'],status,applied_id,now()))
        if replay_job_id:
            cur=store.db.execute(
                """UPDATE reanalysis_jobs SET next_raw_id=?,batches_done=batches_done+1,
                   messages_scanned=messages_scanned+?,updated_at=?
                   WHERE id=? AND platform=? AND owner=? AND room=? AND status='running'
                   AND next_raw_id=?""",
                (to_cursor+1,len(messages),now(),replay_job_id,*store._key(s),from_cursor+1))
            if cur.rowcount!=1:raise RuntimeError('History job changed')
        elif not dry_run:
            store.db.execute('''UPDATE room_state SET dream_cursor=?,last_success=?,next_attempt=0,last_error=''
                WHERE platform=? AND owner=? AND room=? AND dream_cursor=?''',
                (to_cursor,now(),*store._key(s),from_cursor))
        if v2_parts is not None and dry_run:
            # Preserve the exact source-backed candidate set for admin inspection.
            # No topic, observation or active memory is modified by preview mode.
            store.db.execute(
                """INSERT INTO dream_run_previews(run_id,payload_json,created_at)
                   VALUES(?,?,?)""",
                (run_id,json.dumps({
                    'topics':v2['topics'],
                    'observations':v2['observations'],
                    'changes':v2['changes'],
                    'empty_reason':v2.get('empty_reason','')
                },ensure_ascii=False),now()))
        if v2_parts is not None:
            store.db.execute(
                """INSERT INTO dream_run_details(run_id,mode,analyzed_messages,topics_written,
                   observations_written,proposals,outcome,empty_reason)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (run_id,('replay_preview' if dry_run else 'replay_apply') if replay_job_id else ('preview' if dry_run else 'live'),len(messages),
                 t_written if not dry_run else len(v2_parts[0]),
                 o_written if not dry_run else len(v2_parts[1]),len(parsed),
                 'promoted' if changed else 'observed' if v2_parts[1] or v2_parts[0]
                 else 'no_signal',str(v2.get('empty_reason',''))[:80]))
        store.db.execute('''UPDATE dream_runs SET status=?,changes_count=?,finished_at=? WHERE id=?''',
                         ('preview' if dry_run else 'committed',changed,now(),run_id))
    if not dry_run:
        for subject in touched:store.refresh_snapshot(s,subject)
    return {'changed':changed,'review':reviewed,'noop':ignored,'proposals':len(parsed),'cursor':to_cursor,
            'topics':t_written,'observations':o_written}


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
                if c['action']=='revise':
                    # A revision updates the existing fact; it does not supersede it.
                    store._version(prev,'review_revise',actor,c['run_id'])
                    store.db.execute(
                        """UPDATE memories SET content=?,revision=revision+1,updated_at=?
                           WHERE id=?""",(memory['content'],now(),prev['id']))
                    store.db.execute(
                        """INSERT OR IGNORE INTO memory_sources(memory_id,raw_id)
                           SELECT ?,raw_id FROM memory_sources WHERE memory_id=?""",
                        (prev['id'],memory['id']))
                    store.db.execute('DELETE FROM embedding_vectors WHERE memory_id=?',(prev['id'],))
                    store._version(memory,'review_applied_to_existing',actor,c['run_id'])
                    store.db.execute(
                        "UPDATE memories SET status='deleted',revision=revision+1,updated_at=? WHERE id=?",
                        (now(),memory['id']))
                    store.db.execute(
                        'UPDATE dream_candidates SET applied_memory_id=? WHERE id=?',
                        (prev['id'],candidate_id))
                elif c['action']=='supersede':
                    store._version(prev,'review_superseded',actor,c['run_id'])
                    store.db.execute(
                        "UPDATE memories SET status='superseded',revision=revision+1,updated_at=? WHERE id=?",
                        (now(),prev['id']))
                else:
                    raise ValueError('Unsupported targeted review action')
            if not (c['memory_id'] and c['action']=='revise'):
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
