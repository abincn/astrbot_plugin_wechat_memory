"""Incremental SQLite schema for Dreaming V2; safe for existing v0.2.2 data."""
from __future__ import annotations

import sqlite3


DDL = (
    """CREATE TABLE IF NOT EXISTS topic_states (
       id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
       title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
       state TEXT NOT NULL CHECK(state IN ('active','concluded','disputed','closed')),
       revision INTEGER NOT NULL DEFAULT 1,
       first_message_at INTEGER NOT NULL, last_message_at INTEGER NOT NULL,
       created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS topic_scope_recent
       ON topic_states(platform,owner,room,last_message_at DESC)""",
    """CREATE TABLE IF NOT EXISTS topic_sources (
       topic_id TEXT NOT NULL REFERENCES topic_states(id) ON DELETE CASCADE,
       raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
       PRIMARY KEY(topic_id,raw_id))""",
    """CREATE TABLE IF NOT EXISTS observation_signals (
       id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
       scope TEXT NOT NULL CHECK(scope IN ('group','member')),
       subject_id TEXT NOT NULL DEFAULT '',
       signal_type TEXT NOT NULL CHECK(signal_type IN
         ('explicit_fact','explicit_preference','long_term_plan',
          'group_decision','ongoing_task','topic_participation')),
       signal_key TEXT NOT NULL, content TEXT NOT NULL,
       status TEXT NOT NULL CHECK(status IN
         ('observing','review','promoted','rejected','invalidated')),
       evidence_type TEXT NOT NULL CHECK(evidence_type IN
         ('self_report','group_decision','other')),
       topic_id TEXT REFERENCES topic_states(id) ON DELETE SET NULL,
       promoted_memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL,
       revision INTEGER NOT NULL DEFAULT 1,
       first_seen_at INTEGER NOT NULL, last_seen_at INTEGER NOT NULL,
       created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
       UNIQUE(platform,owner,room,scope,subject_id,signal_key))""",
    """CREATE INDEX IF NOT EXISTS observation_scope_status
       ON observation_signals(platform,owner,room,status,subject_id)""",
    """CREATE TABLE IF NOT EXISTS observation_sources (
       observation_id TEXT NOT NULL REFERENCES observation_signals(id) ON DELETE CASCADE,
       raw_id INTEGER NOT NULL REFERENCES raw_messages(id) ON DELETE CASCADE,
       relation TEXT NOT NULL CHECK(relation IN ('support','contradict')),
       PRIMARY KEY(observation_id,raw_id,relation))""",
    """CREATE TABLE IF NOT EXISTS reanalysis_jobs (
       id TEXT PRIMARY KEY, platform TEXT NOT NULL, owner TEXT NOT NULL, room TEXT NOT NULL,
       start_raw_id INTEGER NOT NULL, end_raw_id INTEGER NOT NULL, next_raw_id INTEGER NOT NULL,
       status TEXT NOT NULL CHECK(status IN
         ('pending','running','paused','completed','failed','cancelled')),
       mode TEXT NOT NULL CHECK(mode IN ('preview','apply')),
       actor TEXT NOT NULL, batches_done INTEGER NOT NULL DEFAULT 0,
       messages_scanned INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
       created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS reanalysis_scope
       ON reanalysis_jobs(platform,owner,room,status,created_at)""",
    """CREATE TABLE IF NOT EXISTS dream_run_details (
       run_id TEXT PRIMARY KEY REFERENCES dream_runs(id) ON DELETE CASCADE,
       mode TEXT NOT NULL DEFAULT 'live',
       analyzed_messages INTEGER NOT NULL DEFAULT 0,
       topics_written INTEGER NOT NULL DEFAULT 0,
       observations_written INTEGER NOT NULL DEFAULT 0,
       proposals INTEGER NOT NULL DEFAULT 0,
       outcome TEXT NOT NULL DEFAULT '',
       empty_reason TEXT NOT NULL DEFAULT '')""",
)


def migrate(db: sqlite3.Connection) -> None:
    version = int(db.execute('PRAGMA user_version').fetchone()[0])
    if version > 6:
        raise RuntimeError('Memory database was created by a newer plugin')
    if version >= 3:
        return
    # DDL through execute(), not executescript(), to preserve transaction atomicity.
    db.execute('BEGIN IMMEDIATE')
    try:
        for statement in DDL:
            db.execute(statement)
        db.execute('PRAGMA user_version=3')
        db.execute('COMMIT')
    except BaseException:
        if db.in_transaction:
            db.execute('ROLLBACK')
        raise
