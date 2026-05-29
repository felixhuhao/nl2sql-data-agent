from collections.abc import Generator
from contextlib import contextmanager

import duckdb
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.app.config import get_settings


def get_duckdb_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path = get_settings().resolved_duckdb_path()
    if not path.exists() and read_only:
        raise FileNotFoundError(f"DuckDB file not found: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def get_sqlite_engine():
    path = get_settings().resolved_sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}", future=True)


@contextmanager
def sqlite_session() -> Generator[Session]:
    engine = get_sqlite_engine()
    session = Session(engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
