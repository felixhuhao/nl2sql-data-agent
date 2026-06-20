from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
import threading
from pathlib import Path

from backend.app.auth.models import User


class SQLiteUserStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def ensure_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auth_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def create(self, user: User) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO auth_users (user_id, username, password_hash, role, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user.user_id, user.username, user.password_hash, user.role, user.created_at),
            )

    def count(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM auth_users").fetchone()
        return int(row["count"])

    def get_by_username(self, username: str) -> User | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, password_hash, role, created_at FROM auth_users WHERE username = ?",
                (username,),
            ).fetchone()
        return _row_to_user(row)

    def get_by_id(self, user_id: str) -> User | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT user_id, username, password_hash, role, created_at FROM auth_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
        return _row_to_user(row)

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


def _row_to_user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
        role=row["role"],
        created_at=row["created_at"],
    )
