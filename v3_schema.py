"""Dreaming V3 independent event/claim/evidence schema; preserves legacy records."""
from __future__ import annotations

import sqlite3

DDL = (
    """CREATE TABLE IF NOT EXISTS memory_events (
      id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
      title TEXT NOT NULL, event_type TEXT NOT NULL, anchor_date TEXT NOT NULL DEFAULT '',
      state TEXT NOT NULL DEFAULT 'active', summary TEXT NOT NULL DEFAULT '',
      first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
      revision INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS event_scope_v3
      ON memory_events(platform,owner,room,updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS memory_claims (
      id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
      event_id TEXT REFERENCES memory_events(id) ON DELETE SET NULL,
      subject_type TEXT NOT NULL CHECK(subject_type IN ('group','member','event')),
      subject_id TEXT NOT NULL DEFAULT '',
      claim_type TEXT NOT NULL CHECK(claim_type IN
        ('person_fact','preference','behavior_pattern','relation_claim','group_decision',
         'task','event_detail','topic_context','third_party_statement')),
      predicate TEXT NOT NULL, content TEXT NOT NULL,
      attribution TEXT NOT NULL CHECK(attribution IN
        ('self_statement','third_party_statement','observed_behavior','group_decision')),
      speaker_id TEXT NOT NULL DEFAULT '',
      confidence TEXT NOT NULL CHECK(confidence IN
        ('confirmed','supported','reported','uncertain','contradicted')),
      sensitivity TEXT NOT NULL CHECK(sensitivity IN ('normal','personal','restricted')),
      use_policy TEXT NOT NULL CHECK(use_policy IN ('contextual','referable','restricted_output')),
      status TEXT NOT NULL CHECK(status IN ('candidate','active','review','superseded','expired','retracted')),
      revision INTEGER NOT NULL DEFAULT 1,
      first_seen INTEGER NOT NULL, last_supported INTEGER NOT NULL,
      created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS claim_scope_v3
      ON memory_claims(platform,owner,room,subject_type,subject_id,status,updated_at DESC)""",
    """CREATE INDEX IF NOT EXISTS claim_event_v3 ON memory_claims(event_id)""",
    """CREATE TABLE IF NOT EXISTS claim_evidence (
      claim_id TEXT NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
      raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
      relation TEXT NOT NULL CHECK(relation IN ('support','contradict','reported')),
      PRIMARY KEY(claim_id,raw_id,relation))""",
    """CREATE INDEX IF NOT EXISTS claim_evidence_raw_v3 ON claim_evidence(raw_id)""",
    """CREATE TABLE IF NOT EXISTS event_evidence (
      event_id TEXT NOT NULL REFERENCES memory_events(id) ON DELETE CASCADE,
      raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
      PRIMARY KEY(event_id,raw_id))""",
    """CREATE TABLE IF NOT EXISTS analysis_ledger (
      platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
      raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
      pipeline_version INTEGER NOT NULL DEFAULT 3,
      result TEXT NOT NULL CHECK(result IN
        ('analyzed_with_evidence','analyzed_no_signal','deferred','excluded_by_policy')),
      reason TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
      updated_at INTEGER NOT NULL,
      PRIMARY KEY(platform,owner,room,raw_id,pipeline_version))""",
    """CREATE INDEX IF NOT EXISTS ledger_scope_v3
      ON analysis_ledger(platform,owner,room,result,raw_id)""",
    """CREATE TABLE IF NOT EXISTS consolidation_queue (
      platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
      subject_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      last_raw_id INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
      PRIMARY KEY(platform,owner,room,subject_id))""",
    """CREATE TABLE IF NOT EXISTS v3_batch_hints (
      platform TEXT NOT NULL,owner TEXT NOT NULL,room TEXT NOT NULL,
      max_messages INTEGER NOT NULL,updated_at INTEGER NOT NULL,
      PRIMARY KEY(platform,owner,room))""",
    """CREATE TABLE IF NOT EXISTS memory_claim_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,claim_id TEXT NOT NULL,
      revision INTEGER NOT NULL,previous_content TEXT NOT NULL,
      previous_status TEXT NOT NULL,actor TEXT NOT NULL,
      reason TEXT NOT NULL DEFAULT '',occurred_at INTEGER NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS claim_versions_v3 ON memory_claim_versions(claim_id,id DESC)""",
    """CREATE TABLE IF NOT EXISTS v3_run_details (
      run_id TEXT PRIMARY KEY REFERENCES dream_runs(id) ON DELETE CASCADE,
      analyzed INTEGER NOT NULL DEFAULT 0, no_signal INTEGER NOT NULL DEFAULT 0,
      events_written INTEGER NOT NULL DEFAULT 0, claims_written INTEGER NOT NULL DEFAULT 0,
      sources_added INTEGER NOT NULL DEFAULT 0,
      mode TEXT NOT NULL, preview_json TEXT NOT NULL DEFAULT '')""",
)

def migrate(db: sqlite3.Connection) -> None:
    version = int(db.execute('PRAGMA user_version').fetchone()[0])
    if version > 5:
        raise RuntimeError('Memory database is newer than Dreaming V3')
    db.execute('BEGIN IMMEDIATE')
    try:
        for statement in DDL:
            db.execute(statement)
        if version < 5:
            db.execute('PRAGMA user_version=5')
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
