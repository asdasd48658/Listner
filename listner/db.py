"""Shared PostgreSQL persistence, with an explicit SQLite development fallback."""
from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SQLITE_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS watched_contacts (telegram_user_id INTEGER PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS known_groups (group_id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', discovered_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS call_monitors (group_id INTEGER NOT NULL, call_id INTEGER NOT NULL, call_access_hash TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until INTEGER NOT NULL DEFAULT 0, started_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, PRIMARY KEY (group_id, call_id));
CREATE TABLE IF NOT EXISTS call_presence (group_id INTEGER NOT NULL, call_id INTEGER NOT NULL, telegram_user_id INTEGER NOT NULL, joined_at INTEGER NOT NULL, PRIMARY KEY (group_id, call_id, telegram_user_id));
CREATE TABLE IF NOT EXISTS user_statuses (telegram_user_id INTEGER PRIMARY KEY, is_online INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, group_id INTEGER NOT NULL, call_id INTEGER NOT NULL, telegram_user_id INTEGER NOT NULL, body TEXT NOT NULL, created_at INTEGER NOT NULL, delivered_at INTEGER);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS watched_contacts (telegram_user_id BIGINT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', created_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS known_groups (group_id BIGINT PRIMARY KEY, title TEXT NOT NULL DEFAULT '', discovered_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS call_monitors (group_id BIGINT NOT NULL, call_id BIGINT NOT NULL, call_access_hash TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until BIGINT NOT NULL DEFAULT 0, started_at BIGINT NOT NULL, updated_at BIGINT NOT NULL, PRIMARY KEY (group_id, call_id));
CREATE TABLE IF NOT EXISTS call_presence (group_id BIGINT NOT NULL, call_id BIGINT NOT NULL, telegram_user_id BIGINT NOT NULL, joined_at BIGINT NOT NULL, PRIMARY KEY (group_id, call_id, telegram_user_id));
CREATE TABLE IF NOT EXISTS user_statuses (telegram_user_id BIGINT PRIMARY KEY, is_online BOOLEAN NOT NULL, updated_at BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, kind TEXT NOT NULL, group_id BIGINT NOT NULL, call_id BIGINT NOT NULL, telegram_user_id BIGINT NOT NULL, body TEXT NOT NULL, created_at BIGINT NOT NULL, delivered_at BIGINT);
"""


class Store:
    """Database-backed state. PostgreSQL is used for ``DATABASE_URL`` deployments.

    Passing a filesystem path is an explicit SQLite-only local development fallback.
    Each operation uses a short-lived connection, which is safe for serverless API
    requests and avoids retaining stale connections in a long-running worker.
    """

    def __init__(self, database: str):
        if not database:
            raise RuntimeError("DATABASE_URL is required (or set DATABASE_PATH for local SQLite development)")
        self.database = database
        self.is_postgres = database.startswith(("postgres://", "postgresql://"))
        if not self.is_postgres:
            self.path = database.removeprefix("sqlite:///")
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.is_postgres:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(self.database, row_factory=dict_row) as con:
                yield con
        else:
            con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            con.row_factory = sqlite3.Row
            try:
                yield con
            finally:
                con.close()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", "%s") if self.is_postgres else statement

    def _execute(self, con: Any, statement: str, params: tuple = ()) -> Any:
        return con.execute(self._sql(statement), params)

    def initialize(self) -> None:
        with self.connection() as con:
            if self.is_postgres:
                for statement in POSTGRES_SCHEMA.split(";\n"):
                    if statement.strip():
                        con.execute(statement)
            else:
                con.executescript(SQLITE_SCHEMA)

    def watched(self) -> list[int]:
        with self.connection() as con:
            return [r["telegram_user_id"] if self.is_postgres else r[0] for r in self._execute(con, "SELECT telegram_user_id FROM watched_contacts")]

    def add_watched(self, user_id: int, name: str = "") -> None:
        with self.connection() as con:
            self._execute(con, "INSERT INTO watched_contacts(telegram_user_id,display_name,created_at) VALUES (?,?,?) ON CONFLICT(telegram_user_id) DO NOTHING", (user_id, name, int(time.time())))

    def remove_watched(self, user_id: int) -> bool:
        with self.connection() as con:
            return self._execute(con, "DELETE FROM watched_contacts WHERE telegram_user_id=?", (user_id,)).rowcount > 0

    def record_online_status(self, user_id: int, is_online: bool, now: int | None = None) -> str | None:
        now = int(time.time()) if now is None else now
        online = bool(is_online) if self.is_postgres else int(is_online)
        with self.connection() as con:
            row = self._execute(con, "SELECT is_online FROM user_statuses WHERE telegram_user_id=?", (user_id,)).fetchone()
            if row is None:
                self._execute(con, "INSERT INTO user_statuses VALUES (?,?,?)", (user_id, online, now))
                if not is_online:
                    return None
            elif row["is_online"] == online:
                return None
            else:
                self._execute(con, "UPDATE user_statuses SET is_online=?, updated_at=? WHERE telegram_user_id=?", (online, now, user_id))
            kind = "online" if is_online else "offline"
            self._execute(con, "INSERT INTO alerts(kind,group_id,call_id,telegram_user_id,body,created_at) VALUES (?,?,?,?,?,?)", (kind, 0, 0, user_id, f"{user_id} is now {kind}", now))
            return kind

    def save_group(self, group_id: int, title: str) -> None:
        with self.connection() as con:
            self._execute(con, "INSERT INTO known_groups VALUES (?,?,?) ON CONFLICT(group_id) DO UPDATE SET title=excluded.title, discovered_at=excluded.discovered_at", (group_id, title, int(time.time())))

    def groups(self) -> list[Any]:
        with self.connection() as con:
            return self._execute(con, "SELECT * FROM known_groups").fetchall()

    def acquire_monitor(self, group_id: int, call_id: int, access_hash: int | str, owner: str | None = None, lease_seconds: int = 10, now: int | None = None) -> str | None:
        """Atomically acquire or renew one active call lease."""
        now = int(time.time()) if now is None else now
        owner = owner or uuid.uuid4().hex
        with self.connection() as con:
            cur = self._execute(con, """
                INSERT INTO call_monitors(group_id,call_id,call_access_hash,lease_owner,lease_until,started_at,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(group_id,call_id) DO UPDATE SET
                  lease_owner=excluded.lease_owner, lease_until=excluded.lease_until,
                  call_access_hash=excluded.call_access_hash, updated_at=excluded.updated_at
                WHERE call_monitors.lease_until < ? OR call_monitors.lease_owner = excluded.lease_owner
            """, (group_id, call_id, str(access_hash), owner, now + lease_seconds, now, now, now))
            return owner if cur.rowcount == 1 else None

    def renew_monitor(self, group_id: int, call_id: int, owner: str, lease_seconds: int, now: int | None = None) -> bool:
        now = int(time.time()) if now is None else now
        with self.connection() as con:
            return self._execute(con, "UPDATE call_monitors SET lease_until=?,updated_at=? WHERE group_id=? AND call_id=? AND lease_owner=? AND lease_until>=?", (now + lease_seconds, now, group_id, call_id, owner, now)).rowcount == 1

    def record_presence(self, group_id: int, call_id: int, user_id: int, present: bool, body: str, now: int | None = None) -> tuple[str, int] | None:
        now = int(time.time()) if now is None else now
        with self.connection() as con:
            if present:
                inserted = self._execute(con, "INSERT INTO call_presence VALUES (?,?,?,?) ON CONFLICT(group_id,call_id,telegram_user_id) DO NOTHING", (group_id, call_id, user_id, now)).rowcount
                if inserted:
                    self._execute(con, "INSERT INTO alerts(kind,group_id,call_id,telegram_user_id,body,created_at) VALUES ('joined',?,?,?,?,?)", (group_id, call_id, user_id, body, now))
                    return ("joined", 0)
            else:
                row = self._execute(con, "DELETE FROM call_presence WHERE group_id=? AND call_id=? AND telegram_user_id=? RETURNING joined_at", (group_id, call_id, user_id)).fetchone()
                if row:
                    joined_at = row["joined_at"] if self.is_postgres else row[0]
                    seconds = now - joined_at
                    self._execute(con, "INSERT INTO alerts(kind,group_id,call_id,telegram_user_id,body,created_at) VALUES ('left',?,?,?,?,?)", (group_id, call_id, user_id, f"{body} after {seconds}s", now))
                    return ("left", seconds)
        return None

    def pending_alerts(self) -> list[Any]:
        with self.connection() as con:
            return self._execute(con, "SELECT * FROM alerts WHERE delivered_at IS NULL ORDER BY id").fetchall()

    def mark_delivered(self, alert_id: int) -> None:
        with self.connection() as con:
            self._execute(con, "UPDATE alerts SET delivered_at=? WHERE id=? AND delivered_at IS NULL", (int(time.time()), alert_id))
