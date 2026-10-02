from __future__ import annotations
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS watched_contacts (
  telegram_user_id INTEGER PRIMARY KEY,
  display_name TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS known_groups (
  group_id INTEGER PRIMARY KEY,
  title TEXT NOT NULL DEFAULT '',
  discovered_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS call_monitors (
  group_id INTEGER NOT NULL,
  call_id INTEGER NOT NULL,
  call_access_hash TEXT NOT NULL DEFAULT '',
  lease_owner TEXT,
  lease_until INTEGER NOT NULL DEFAULT 0,
  started_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  PRIMARY KEY (group_id, call_id)
);
CREATE TABLE IF NOT EXISTS call_presence (
  group_id INTEGER NOT NULL,
  call_id INTEGER NOT NULL,
  telegram_user_id INTEGER NOT NULL,
  joined_at INTEGER NOT NULL,
  PRIMARY KEY (group_id, call_id, telegram_user_id)
);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  group_id INTEGER NOT NULL,
  call_id INTEGER NOT NULL,
  telegram_user_id INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  delivered_at INTEGER
);
"""

class Store:
    """SQLite persistence. Lease acquisition is one atomic UPSERT guarded by time."""
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        try:
            yield con
        finally:
            con.close()

    def initialize(self) -> None:
        with self.connection() as con:
            con.executescript(SCHEMA)

    def watched(self) -> list[int]:
        with self.connection() as con:
            return [r[0] for r in con.execute("SELECT telegram_user_id FROM watched_contacts")]

    def add_watched(self, user_id: int, name: str = "") -> None:
        with self.connection() as con:
            con.execute("INSERT OR IGNORE INTO watched_contacts VALUES (?, ?, ?)", (user_id, name, int(time.time())))

    def remove_watched(self, user_id: int) -> bool:
        with self.connection() as con:
            return con.execute("DELETE FROM watched_contacts WHERE telegram_user_id=?", (user_id,)).rowcount > 0

    def save_group(self, group_id: int, title: str) -> None:
        with self.connection() as con:
            con.execute("INSERT INTO known_groups VALUES (?, ?, ?) ON CONFLICT(group_id) DO UPDATE SET title=excluded.title", (group_id, title, int(time.time())))

    def groups(self) -> list[sqlite3.Row]:
        with self.connection() as con: return con.execute("SELECT * FROM known_groups").fetchall()

    def acquire_monitor(self, group_id: int, call_id: int, access_hash: int | str, owner: str | None = None, lease_seconds: int = 10, now: int | None = None) -> str | None:
        """Return owner token only if this process owns a non-expired per-call lease."""
        now = int(time.time()) if now is None else now
        owner = owner or uuid.uuid4().hex
        with self.connection() as con:
            cur = con.execute("""
              INSERT INTO call_monitors(group_id,call_id,call_access_hash,lease_owner,lease_until,started_at,updated_at)
              VALUES (?, ?, ?, ?, ?, ?, ?)
              ON CONFLICT(group_id,call_id) DO UPDATE SET
                lease_owner=excluded.lease_owner, lease_until=excluded.lease_until,
                call_access_hash=excluded.call_access_hash, updated_at=excluded.updated_at
              WHERE call_monitors.lease_until < ? OR call_monitors.lease_owner = excluded.lease_owner
            """, (group_id, call_id, str(access_hash), owner, now + lease_seconds, now, now, now))
            return owner if cur.rowcount == 1 else None

    def renew_monitor(self, group_id: int, call_id: int, owner: str, lease_seconds: int, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        with self.connection() as con:
            return con.execute("UPDATE call_monitors SET lease_until=?,updated_at=? WHERE group_id=? AND call_id=? AND lease_owner=? AND lease_until>=?", (now+lease_seconds, now, group_id, call_id, owner, now)).rowcount == 1

    def record_presence(self, group_id: int, call_id: int, user_id: int, present: bool, body: str, now: int | None = None) -> tuple[str, int] | None:
        now = int(time.time()) if now is None else now
        with self.connection() as con:
            row = con.execute("SELECT joined_at FROM call_presence WHERE group_id=? AND call_id=? AND telegram_user_id=?", (group_id,call_id,user_id)).fetchone()
            if present and not row:
                con.execute("INSERT INTO call_presence VALUES (?,?,?,?)", (group_id,call_id,user_id,now))
                con.execute("INSERT INTO alerts(kind,group_id,call_id,telegram_user_id,body,created_at) VALUES ('joined',?,?,?,?,?)", (group_id,call_id,user_id,body,now))
                return ("joined", 0)
            if not present and row:
                con.execute("DELETE FROM call_presence WHERE group_id=? AND call_id=? AND telegram_user_id=?", (group_id,call_id,user_id))
                seconds = now-row[0]
                con.execute("INSERT INTO alerts(kind,group_id,call_id,telegram_user_id,body,created_at) VALUES ('left',?,?,?,?,?)", (group_id,call_id,user_id,f'{body} after {seconds}s',now))
                return ("left", seconds)
        return None

    def pending_alerts(self) -> list[sqlite3.Row]:
        with self.connection() as con: return con.execute("SELECT * FROM alerts WHERE delivered_at IS NULL ORDER BY id").fetchall()
    def mark_delivered(self, alert_id: int) -> None:
        with self.connection() as con: con.execute("UPDATE alerts SET delivered_at=? WHERE id=?", (int(time.time()), alert_id))
