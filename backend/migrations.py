from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UnknownDatabaseSchemaError(RuntimeError):
    pass


class SchemaNotCurrentError(RuntimeError):
    pass


def alembic_config(database_url: str) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["database_url"] = database_url
    return config


def head_revision() -> str:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


def current_revision(database_url: str) -> str | None:
    engine = create_engine(database_url, future=True)
    try:
        with engine.connect() as connection:
            return MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()


def run_migrations(database_url: str) -> None:
    _ensure_sqlite_parent(database_url)
    engine = create_engine(database_url, future=True)
    try:
        table_names = {
            name for name in inspect(engine).get_table_names() if not name.startswith("sqlite_")
        }
    finally:
        engine.dispose()
    if table_names and "alembic_version" not in table_names:
        raise UnknownDatabaseSchemaError(
            "Database contains tables but has no alembic_version. "
            "Refusing to stamp an unknown schema; restore a backup or establish its revision explicitly."
        )
    command.upgrade(alembic_config(database_url), "head")


def require_current_schema(database_url: str) -> None:
    current = current_revision(database_url)
    head = head_revision()
    if current != head:
        raise SchemaNotCurrentError(
            f"Database schema is at {current or 'none'}, expected {head}. Run scripts/migrate.sh before starting services."
        )


def is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def _ensure_sqlite_parent(database_url: str) -> None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url[len(prefix) :]
    if raw_path in {"", ":memory:"}:
        return
    Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
