from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import secrets
import sqlite3
import threading
import time
from pathlib import Path


class SQLiteLoginSessionStore:
    def __init__(self, path: Path, *, clock=time.time) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.RLock()

    def ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expire_at REAL NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expire_at ON auth_sessions(expire_at)")

    def create(self, user_id: str, *, ttl_seconds: int) -> str:
        session_id = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_sessions (session_id, user_id, created_at, expire_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, user_id, now, now + ttl_seconds),
            )
        return session_id

    def get(self, session_id: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT session_id, user_id, created_at, expire_at FROM auth_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            if float(row["expire_at"]) <= self._clock():
                conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))
                return None
            return {
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "created_at": row["created_at"],
                "expire_at": row["expire_at"],
            }

    def delete(self, session_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE session_id = ?", (session_id,))

    def delete_expired(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM auth_sessions WHERE expire_at <= ?", (self._clock(),))

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
