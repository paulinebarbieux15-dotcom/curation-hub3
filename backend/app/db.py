"""SQLite storage: one file (data/curation.db), nothing to install.

Tables
- users      profiles; soft delete via deleted_at (rows are never removed)
- links      every collected link and its working state
- registry   immutable, hash-chained record of approved links
- jobs       background job queue (capture + AI analysis)
- events     audit trail of every action
- reports    generated executive reports
- settings   small key/value store (themes, toggles, round-robin pointer)
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    antbox_redactor   TEXT,                 -- value used for the 'Attachment redactor' field
    available         INTEGER NOT NULL DEFAULT 1,   -- 0 = away: skipped by auto-distribution
    created_at        TEXT NOT NULL,
    deleted_at        TEXT                   -- soft delete
);

CREATE TABLE IF NOT EXISTS links (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    url                TEXT NOT NULL,
    normalized_url     TEXT NOT NULL UNIQUE,
    final_url          TEXT,                -- after redirects (lnkd.in, t.co...)
    canonical_url      TEXT,                -- <link rel=canonical>
    source             TEXT NOT NULL DEFAULT 'manual',   -- manual, paste, csv, social
    status             TEXT NOT NULL DEFAULT 'queued',
    assignee_id        INTEGER REFERENCES users(id),     -- NULL = global queue
    title              TEXT,
    text               TEXT,
    simhash            TEXT,
    near_duplicate_of  INTEGER REFERENCES links(id),
    duplicate_of       INTEGER REFERENCES links(id),
    pdf_file           TEXT,
    summary_html       TEXT,
    metadata           TEXT NOT NULL DEFAULT '{}',   -- AntBox fields  {"Domain": ["..."]}
    metadata_ai        TEXT NOT NULL DEFAULT '{}',   -- AI proposal (to measure acceptance)
    info               TEXT NOT NULL DEFAULT '{}',   -- author, published_date, source, country, companies
    themes             TEXT NOT NULL DEFAULT '[]',   -- dashboard-only tags (never exported)
    themes_ai          TEXT NOT NULL DEFAULT '[]',
    ai_details         TEXT NOT NULL DEFAULT '{}',   -- confidence + evidence per value
    error              TEXT,
    locked_by          INTEGER REFERENCES users(id),
    locked_until       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    approved_at        TEXT,
    approved_by        INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS ix_links_status ON links(status);
CREATE INDEX IF NOT EXISTS ix_links_assignee ON links(assignee_id);

CREATE TABLE IF NOT EXISTS registry (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id       INTEGER NOT NULL REFERENCES links(id),
    approved_at   TEXT NOT NULL,
    approved_by   INTEGER REFERENCES users(id),
    operator_name TEXT NOT NULL,
    title         TEXT,
    url           TEXT NOT NULL,
    metadata      TEXT NOT NULL,
    summary_text  TEXT,
    pdf_file      TEXT,
    csv_file      TEXT,
    pdf_sha256    TEXT,
    csv_sha256    TEXT,
    prev_hash     TEXT NOT NULL,
    entry_hash    TEXT NOT NULL
);
-- The registry is append-only: the database itself refuses edits and deletions.
CREATE TRIGGER IF NOT EXISTS registry_no_update BEFORE UPDATE ON registry
BEGIN SELECT RAISE(ABORT, 'registry is immutable'); END;
CREATE TRIGGER IF NOT EXISTS registry_no_delete BEFORE DELETE ON registry
BEGIN SELECT RAISE(ABORT, 'registry is immutable'); END;

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id     INTEGER NOT NULL REFERENCES links(id),
    domain      TEXT NOT NULL,
    is_social   INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending, running, done, failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    run_after   TEXT NOT NULL,
    last_error  TEXT,
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, run_after);

CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    link_id  INTEGER,
    user_id  INTEGER,
    action   TEXT NOT NULL,
    detail   TEXT,
    at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_link ON events(link_id);

CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    granularity  TEXT NOT NULL,      -- general, theme, user
    link_count   INTEGER NOT NULL,
    created_by   INTEGER REFERENCES users(id),
    created_at   TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    html_file    TEXT,
    pdf_file     TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

TODO_STATUSES = ("queued", "processing", "to_review", "error")
DONE_STATUSES = ("approved", "rejected", "duplicate")
JSON_FIELDS = ("metadata", "metadata_ai", "info", "themes", "themes_ai", "ai_details")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


@contextmanager
def tx():
    """One connection per transaction; commits on success, rolls back on error."""
    with _lock:
        con = connect()
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()


def init() -> None:
    with tx() as con:
        con.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row | None, with_text: bool = False) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for f in JSON_FIELDS:
        if f in d:
            d[f] = json.loads(d[f] or ("[]" if f.startswith("themes") else "{}"))
    if not with_text:
        d.pop("text", None)
    return d


def log(con: sqlite3.Connection, action: str, link_id: int | None = None,
        user_id: int | None = None, detail: str = "") -> None:
    con.execute("INSERT INTO events (link_id, user_id, action, detail, at) VALUES (?,?,?,?,?)",
                (link_id, user_id, action, detail, now()))


def get_setting(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_setting(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
