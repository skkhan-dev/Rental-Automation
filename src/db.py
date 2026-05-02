from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    listing_title TEXT,
    counterparty TEXT,
    first_seen_ts INTEGER NOT NULL,
    last_seen_ts INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id),
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    body TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, ts);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL REFERENCES threads(thread_id),
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
"""


@contextmanager
def conn(db_path: Path):
    db_path.parent.mkdir(exist_ok=True)
    c = sqlite3.connect(db_path, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout = 5000")
    c.executescript(SCHEMA)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def now() -> int:
    return int(time.time())


def upsert_thread(c, thread_id: str, listing_title: str | None, counterparty: str | None):
    ts = now()
    c.execute(
        """
        INSERT INTO threads(thread_id, listing_title, counterparty, first_seen_ts, last_seen_ts)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(thread_id) DO UPDATE SET
            last_seen_ts=excluded.last_seen_ts,
            listing_title=COALESCE(threads.listing_title, excluded.listing_title),
            counterparty=COALESCE(threads.counterparty, excluded.counterparty)
        """,
        (thread_id, listing_title, counterparty, ts, ts),
    )


def message_seen(c, msg_id: str) -> bool:
    return c.execute("SELECT 1 FROM messages WHERE msg_id=?", (msg_id,)).fetchone() is not None


def insert_message(c, msg_id: str, thread_id: str, direction: str, body: str, ts: int | None = None):
    c.execute(
        "INSERT OR IGNORE INTO messages(msg_id, thread_id, direction, body, ts) VALUES (?, ?, ?, ?, ?)",
        (msg_id, thread_id, direction, body, ts or now()),
    )


def thread_history(c, thread_id: str, limit: int = 30) -> list[sqlite3.Row]:
    return list(
        c.execute(
            "SELECT direction, body, ts FROM messages WHERE thread_id=? ORDER BY ts ASC LIMIT ?",
            (thread_id, limit),
        )
    )


def insert_draft(c, thread_id: str, in_reply_to_msg_id: str, body: str, status: str, reason: str = "") -> int:
    cur = c.execute(
        """
        INSERT INTO drafts(thread_id, in_reply_to_msg_id, body, status, created_ts, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (thread_id, in_reply_to_msg_id, body, status, now(), reason),
    )
    return cur.lastrowid


def mark_draft(c, draft_id: int, status: str):
    c.execute("UPDATE drafts SET status=?, decided_ts=? WHERE id=?", (status, now(), draft_id))


def cycle_start(c) -> int:
    cur = c.execute(
        "INSERT INTO cycles(started_ts, status) VALUES (?, 'running')",
        (now(),),
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


def list_cycles(c, limit: int = 200) -> list[sqlite3.Row]:
    return list(
        c.execute(
            """
            SELECT id, started_ts, ended_ts, status, threads_scanned,
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
    row = c.execute("SELECT started_ts, ended_ts FROM cycles WHERE id=?", (cycle_id,)).fetchone()
    if not row:
        return []
    end = row["ended_ts"] or now()
    return list(
        c.execute(
            """
            SELECT d.id, d.thread_id, d.body, d.status, d.reason, d.created_ts,
                   t.counterparty, t.listing_title
            FROM drafts d JOIN threads t ON t.thread_id = d.thread_id
            WHERE d.created_ts >= ? AND d.created_ts <= ?
            ORDER BY d.created_ts ASC
            """,
            (row["started_ts"], end),
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
            SELECT d.id, d.thread_id, d.body, d.reason, d.created_ts,
                   t.counterparty, t.listing_title
            FROM drafts d JOIN threads t ON t.thread_id=d.thread_id
            WHERE d.status='pending'
            ORDER BY d.created_ts ASC
            """
        )
    )
