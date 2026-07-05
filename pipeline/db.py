"""SQLite schema and helpers for LearnPulse.

The database at data/learnpulse.db is the system of record:
  cursors        — per-product incremental ingest cursor (ISO timestamp)
  commits_seen   — dedupe set of (sha, product_id) already ingested/skipped
  change_records — one row per commit per product, raw + triaged + summarized
"""
from __future__ import annotations

import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "learnpulse.db")
DOCS_DATA_DIR = os.path.join(ROOT, "docs", "data")
DIGESTS_DIR = os.path.join(ROOT, "digests")
PRODUCTS_YML = os.path.join(ROOT, "products.yml")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cursors (
    product_id  TEXT PRIMARY KEY,
    last_iso_ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commits_seen (
    sha        TEXT NOT NULL,
    product_id TEXT NOT NULL,
    seen_at    TEXT NOT NULL,
    PRIMARY KEY (sha, product_id)
);

CREATE TABLE IF NOT EXISTS change_records (
    id                 TEXT PRIMARY KEY,
    product            TEXT NOT NULL,
    date               TEXT NOT NULL,
    kind               TEXT,
    title              TEXT,
    summary            TEXT,
    reasons_json       TEXT,
    files_json         TEXT,
    doc_urls_json      TEXT,
    commit_url         TEXT,
    sha                TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    is_noise           INTEGER,
    raw_commit_message TEXT,
    raw_patch_summary  TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_product_date
    ON change_records (product, date);
CREATE INDEX IF NOT EXISTS idx_records_sha ON change_records (sha);
"""


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Open (and initialize if needed) the LearnPulse database."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load_products():
    """Load the product watchlist from products.yml (list of dicts)."""
    import yaml  # stdlib + pyyaml only

    with open(PRODUCTS_YML, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg["products"]


# ---------------------------------------------------------------- cursors

def get_cursor(conn: sqlite3.Connection, product_id: str):
    row = conn.execute(
        "SELECT last_iso_ts FROM cursors WHERE product_id = ?", (product_id,)
    ).fetchone()
    return row["last_iso_ts"] if row else None


def set_cursor(conn: sqlite3.Connection, product_id: str, iso_ts: str) -> None:
    conn.execute(
        "INSERT INTO cursors (product_id, last_iso_ts) VALUES (?, ?) "
        "ON CONFLICT(product_id) DO UPDATE SET last_iso_ts = excluded.last_iso_ts",
        (product_id, iso_ts),
    )


# ----------------------------------------------------------- commits_seen

def is_seen(conn: sqlite3.Connection, sha: str, product_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM commits_seen WHERE sha = ? AND product_id = ?",
        (sha, product_id),
    ).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, sha: str, product_id: str, seen_at: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO commits_seen (sha, product_id, seen_at) VALUES (?, ?, ?)",
        (sha, product_id, seen_at),
    )


# --------------------------------------------------------- change_records

def next_record_id(conn: sqlite3.Connection, sha: str) -> str:
    """id = <sha[:8]>-<n>; n counts existing records for this sha so a commit
    spanning multiple products gets -0, -1, ... (stable once written)."""
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM change_records WHERE sha = ?", (sha,)
    ).fetchone()["c"]
    return f"{sha[:8]}-{n}"


def insert_raw_record(conn, *, record_id, product, date, commit_url, sha,
                      created_at, raw_commit_message, raw_patch_summary) -> None:
    conn.execute(
        "INSERT INTO change_records "
        "(id, product, date, commit_url, sha, created_at, raw_commit_message, raw_patch_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (record_id, product, date, commit_url, sha, created_at,
         raw_commit_message, raw_patch_summary),
    )


def untriaged_records(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM change_records WHERE is_noise IS NULL"
    ).fetchall()


def apply_triage(conn, record_id, *, kind, title, reasons_json, files_json,
                 doc_urls_json, is_noise) -> None:
    conn.execute(
        "UPDATE change_records SET kind = ?, title = ?, reasons_json = ?, "
        "files_json = ?, doc_urls_json = ?, is_noise = ? WHERE id = ?",
        (kind, title, reasons_json, files_json, doc_urls_json, is_noise, record_id),
    )


def unsummarized_records(conn: sqlite3.Connection):
    return conn.execute(
        "SELECT * FROM change_records "
        "WHERE is_noise = 0 AND (summary IS NULL OR summary = '')"
    ).fetchall()


def set_summary(conn, record_id, *, kind, title, summary) -> None:
    conn.execute(
        "UPDATE change_records SET kind = ?, title = ?, summary = ? WHERE id = ?",
        (kind, title, summary, record_id),
    )


def signal_records(conn: sqlite3.Connection, product: str | None = None):
    """Non-noise records, newest first."""
    if product:
        return conn.execute(
            "SELECT * FROM change_records WHERE is_noise = 0 AND product = ? "
            "ORDER BY date DESC, created_at DESC, id",
            (product,),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM change_records WHERE is_noise = 0 "
        "ORDER BY date DESC, created_at DESC, id"
    ).fetchall()
