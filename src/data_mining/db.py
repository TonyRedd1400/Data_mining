"""Esquema y migraciones SQLite del pipeline."""

import sqlite3

RETRIEVAL_COLUMNS = {"retrieval_attempted_at": "TEXT"}


def init_db(path):
    db = sqlite3.connect(path)
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                provider TEXT NOT NULL,
                country TEXT NOT NULL,
                language TEXT NOT NULL,
                config_version TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS search_results (
                id INTEGER PRIMARY KEY,
                bq_id TEXT NOT NULL,
                run_id INTEGER NOT NULL REFERENCES runs(id),
                company TEXT NOT NULL,
                query_literal TEXT NOT NULL,
                position INTEGER NOT NULL,
                provider TEXT NOT NULL,
                searched_at TEXT NOT NULL,
                country TEXT NOT NULL,
                mexico_match TEXT,
                url_original TEXT NOT NULL,
                url_norm TEXT NOT NULL,
                title TEXT NOT NULL,
                snippet TEXT NOT NULL,
                content_retrieved TEXT,
                eligibility TEXT,
                retrieval_status TEXT NOT NULL DEFAULT 'pending',
                retrieval_attempted_at TEXT,
                limitation TEXT,
                exclusion_reason TEXT,
                UNIQUE (bq_id, provider, url_norm)
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id INTEGER PRIMARY KEY,
                search_result_id INTEGER NOT NULL REFERENCES search_results(id),
                url TEXT NOT NULL,
                nature TEXT,
                hypothesis_relation TEXT,
                labor_moment TEXT,
                object TEXT,
                topic TEXT,
                relation_type TEXT,
                valuation TEXT,
                confidence REAL,
                impact TEXT,
                consequence TEXT,
                information_provided TEXT,
                information_missing TEXT,
                research_question TEXT,
                exact_excerpt TEXT,
                hypothesis TEXT
            );
        """)
        _ensure_retrieval_columns(db)
        db.commit()
    finally:
        db.close()


def _ensure_retrieval_columns(db):
    columns = {row[1] for row in db.execute("PRAGMA table_info(search_results)")}
    for name, column_type in RETRIEVAL_COLUMNS.items():
        if name not in columns:
            db.execute(f"ALTER TABLE search_results ADD COLUMN {name} {column_type}")


def migrate_db(path):
    db = sqlite3.connect(path)
    try:
        _ensure_retrieval_columns(db)
        db.commit()
    finally:
        db.close()
