from __future__ import annotations

from pathlib import Path

from sqlalchemy import event, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def _sqlite_path_from_url(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    raw_path = database_url[len(prefix) :]
    if raw_path in {":memory:", ""}:
        return None
    return Path(raw_path)


def make_session_factory(database_url: str) -> sessionmaker[Session]:
    sqlite_path = _sqlite_path_from_url(database_url)
    if sqlite_path is not None and sqlite_path.parent != Path("."):
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    if database_url.startswith("sqlite"):
        engine = create_engine(
            database_url,
            future=True,
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        event.listen(engine, "connect", _configure_sqlite_connection)
    else:
        engine = create_engine(database_url, future=True, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)


def init_db(session_factory: sessionmaker[Session]) -> None:
    engine = session_factory.kw["bind"]
    from backend.migrations import current_revision, head_revision, run_migrations

    database_url = str(engine.url)
    if current_revision(database_url) != head_revision():
        run_migrations(database_url)


def _configure_sqlite_connection(connection, _connection_record) -> None:
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()
