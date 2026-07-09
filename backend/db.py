from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy import create_engine
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
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, future=True, connect_args=connect_args)
    return sessionmaker(engine, expire_on_commit=False)


def init_db(session_factory: sessionmaker[Session]) -> None:
    import backend.models  # noqa: F401

    engine = session_factory.kw["bind"]
    Base.metadata.create_all(engine)
    _upgrade_existing_schema(engine)


def _upgrade_existing_schema(engine) -> None:
    inspector = inspect(engine)
    if "reminders" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("reminders")}
        if "attempts" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE reminders ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"))
