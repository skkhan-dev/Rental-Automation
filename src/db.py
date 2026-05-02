from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

# All 4 main tables carry a `platform` column. Default 'facebook' keeps
# pre-existing rows working when this code lands on a DB created before
# the multi-platform refactor (see _migrate()).
SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    platform TEXT NOT NULL DEFAULT 'facebook',
    listing_title TEXT,
    counterparty TEXT,
    first_seen_ts INTEGER NOT NULL,
    last_seen_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_threads_platform ON threads(platform);

CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id),
    platform TEXT NOT NULL DEFAULT 'facebook',
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, ts);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id),
    platform TEXT NOT NULL DEFAULT 'facebook',
    in_reply_to_msg_id TEXT NOT NULL,
    body TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'rejected')),
    created_ts INTEGER NOT NULL,
    decided_ts INTEGER,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_pending ON drafts(status, thread_id);

CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL DEFAULT 'facebook',
    started_ts INTEGER NOT NULL,
    ended_ts INTEGER,
    status TEXT NOT NULL DEFAULT 'running',  -- running | success | failure | partial
    threads_scanned INTEGER NOT NULL DEFAULT 0,
    unread_found INTEGER NOT NULL DEFAULT 0,
    replies_sent INTEGER NOT NULL DEFAULT 0,
    replies_queued INTEGER NOT NULL DEFAULT 0,
    error_msg TEXT
);
CREATE INDEX IF NOT EXISTS idx_cycles_started ON cycles(started_ts DESC);
CREATE INDEX IF NOT EXISTS idx_cycles_platform ON cycles(platform, started_ts DESC);
"""


def _migrate(c: sqlite3.Connection) -> None:
    """Add the `platform` column to existing tables if missing.
    Idempotent — safe to run on every connection."""
    for table in ("threads", "messages", "drafts", "cycles"):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
        if "platform" not in cols:
            c.execute(
                f"ALTER TABLE {table} ADD COLUMN platform TEXT NOT NULL DEFAULT 'facebook'"
            )


@contextmanager
def conn(db_path: Path):
    db_path.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(db_path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 5000")
    # Migrate first so any pre-existing tables get the `platform` column
    # before SCHEMA's CREATE INDEX ON ... (platform) runs.
    _migrate(c)
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def now() -> int:
    return int(time.time())


def upsert_thread(
    c,
    thread_id: str,
    listing_title: str | None,
    counterparty: str | None,
    platform: str = "facebook",
):
    ts = now()
    c.execute(
        """
        INSERT INTO threads(thread_id, platform, listing_title, counterparty, first_seen_ts, last_seen_ts)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_seen_ts=excluded.last_seen_ts,
            listing_title=COALESCE(threads.listing_title, excluded.listing_title),
            counterparty=COALESCE(threads.counterparty, excluded.counterparty),
            platform=COALESCE(NULLIF(threads.platform, ''), excluded.platform)
        """,
        (thread_id, platform, listing_title, counterparty, ts, ts),
    )


def message_seen(c, msg_id: str) -> bool:
    return c.execute("SELECT 1 FROM messages WHERE msg_id=?", (msg_id,)).fetchone() is not None


def insert_message(
    c,
    msg_id: str,
    thread_id: str,
    direction: str,
    body: str,
    ts: int | None = None,
    platform: str = "facebook",
):
    c.execute(
        "INSERT OR IGNORE INTO messages(msg_id, thread_id, platform, direction, body, ts) VALUES (?, ?, ?, ?, ?, ?)",
        (msg_id, thread_id, platform, direction, body, ts or now()),
    )


def thread_history(c, thread_id: str, limit: int = 30) -> list[sqlite3.Row]:
    return list(
        c.execute(
            "SELECT direction, body, ts FROM messages WHERE thread_id=? ORDER BY ts ASC LIMIT ?",
            (thread_id, limit),
        )
    )


def insert_draft(
    c,
    thread_id: str,
    in_reply_to_msg_id: str,
    body: str,
    status: str,
    reason: str = "",
    platform: str = "facebook",
) -> int:
    cur = c.execute(
        """
        INSERT INTO drafts(thread_id, platform, in_reply_to_msg_id, body, status, created_ts, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (thread_id, platform, in_reply_to_msg_id, body, status, now(), reason),
    )
    return cur.lastrowid


def mark_draft(c, draft_id: int, status: str):
    c.execute("UPDATE drafts SET status=?, decided_ts=? WHERE id=?", (status, now(), draft_id))


def cycle_start(c, platform: str = "facebook") -> int:
    cur = c.execute(
        "INSERT INTO cycles(platform, started_ts, status) VALUES (?, ?, 'running')",
        (platform, now()),
    )
    return cur.lastrowid


def cycle_end(
    c,
    cycle_id: int,
    *,
    status: str,
    threads_scanned: int = 0,
    unread_found: int = 0,
    replies_sent: int = 0,
    replies_queued: int = 0,
    error_msg: str | None = None,
):
    c.execute(
        """
        UPDATE cycles
        SET ended_ts=?, status=?, threads_scanned=?, unread_found=?,
            replies_sent=?, replies_queued=?, error_msg=?
        WHERE id=?
        """,
        (now(), status, threads_scanned, unread_found, replies_sent, replies_queued, error_msg, cycle_id),
    )


def list_cycles(c, limit: int = 200, platform: str | None = None) -> list[sqlite3.Row]:
    if platform:
        return list(
            c.execute(
                """
                SELECT id, platform, started_ts, ended_ts, status, threads_scanned,
                       unread_found, replies_sent, replies_queued, error_msg
                FROM cycles WHERE platform=?
                ORDER BY started_ts DESC LIMIT ?
                """,
                (platform, limit),
            )
        )
    return list(
        c.execute(
            """
            SELECT id, platform, started_ts, ended_ts, status, threads_scanned,
                   unread_found, replies_sent, replies_queued, error_msg
            FROM cycles
            ORDER BY started_ts DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def cycle_drafts(c, cycle_id: int) -> list[sqlite3.Row]:
    """Drafts created during the time window of a given cycle."""
    row = c.execute(
        "SELECT platform, started_ts, ended_ts FROM cycles WHERE id=?", (cycle_id,)
    ).fetchone()
    if not row:
        return []
    end = row["ended_ts"] or now()
    return list(
        c.execute(
            """
            SELECT d.id, d.thread_id, d.platform, d.body, d.status, d.reason, d.created_ts,
                   t.counterparty, t.listing_title
            FROM drafts d JOIN threads t ON t.thread_id = d.thread_id
            WHERE d.created_ts >= ? AND d.created_ts <= ? AND d.platform = ?
            ORDER BY d.created_ts ASC
            """,
            (row["started_ts"], end, row["platform"]),
        )
    )


def delete_cycle(c, cycle_id: int):
    c.execute("DELETE FROM cycles WHERE id=?", (cycle_id,))


def delete_all_cycles(c, only_failures: bool = False):
    if only_failures:
        c.execute("DELETE FROM cycles WHERE status IN ('failure', 'partial')")
    else:
        c.execute("DELETE FROM cycles")


def pending_drafts(c) -> list[sqlite3.Row]:
    return list(
        c.execute(
            """
            SELECT d.id, d.thread_id, d.platform, d.body, d.reason, d.created_ts,
                   t.counterparty, t.listing_title
            FROM drafts d JOIN threads t ON t.thread_id=d.thread_id
            WHERE d.status='pending'
            ORDER BY d.created_ts ASC
            """
        )
    )


def get_draft(c, draft_id: int) -> sqlite3.Row | None:
    return c.execute(
        "SELECT id, thread_id, platform, body, status FROM drafts WHERE id=?",
        (draft_id,),
    ).fetchone()
