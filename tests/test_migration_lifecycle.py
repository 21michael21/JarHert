from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect

from assistant.action_schema import ActionType
from backend.db import Base
from backend.migrations import (
    SchemaNotCurrentError,
    UnknownDatabaseSchemaError,
    alembic_config,
    current_revision,
    head_revision,
    require_current_schema,
)
from backend.stores import SqlActionQueueStore, UserStore
from backend.db import init_db, make_session_factory
from scripts.run_migrations import run_migrations


def test_clean_database_is_created_only_by_alembic(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'clean.sqlite3'}"

    run_migrations(database_url)

    assert current_revision(database_url) == head_revision()
    inspector = inspect(create_engine(database_url))
    tables = set(inspector.get_table_names())
    assert {"alembic_version", "users", "agent_actions", "inbound_updates"} <= tables
    for table_name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        assert set(table.columns.keys()) <= actual


def test_init_db_uses_alembic_not_orm_create_all(tmp_path, monkeypatch) -> None:
    database_url = f"sqlite:///{tmp_path / 'init.sqlite3'}"
    factory = make_session_factory(database_url)
    monkeypatch.setattr(Base.metadata, "create_all", lambda *_args, **_kwargs: pytest.fail("create_all must not run"))

    init_db(factory)

    assert current_revision(database_url) == head_revision()


def test_versioned_database_upgrades_from_previous_revision(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'old.sqlite3'}"
    config = alembic_config(database_url)
    command.upgrade(config, "0007_item_leases")

    assert current_revision(database_url) == "0007_item_leases"
    run_migrations(database_url)

    assert current_revision(database_url) == head_revision()
    columns = {column["name"] for column in inspect(create_engine(database_url)).get_columns("agent_actions")}
    assert {"result_text", "depends_on_action_id", "compensation_for_action_id", "compensation_status"} <= columns


def test_one_revision_rollback_and_reupgrade_are_reproducible(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'rollback.sqlite3'}"
    config = alembic_config(database_url)
    run_migrations(database_url)

    command.downgrade(config, "-1")
    assert current_revision(database_url) == "0008_update_idempotency"
    downgraded_columns = {column["name"] for column in inspect(create_engine(database_url)).get_columns("agent_actions")}
    assert not {"depends_on_action_id", "compensation_for_action_id", "compensation_status"} & downgraded_columns
    command.upgrade(config, "head")

    assert current_revision(database_url) == head_revision()
    upgraded_columns = {column["name"] for column in inspect(create_engine(database_url)).get_columns("agent_actions")}
    assert {"depends_on_action_id", "compensation_for_action_id", "compensation_status"} <= upgraded_columns


def test_stale_versioned_database_is_rejected_at_service_start(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'stale.sqlite3'}"
    command.upgrade(alembic_config(database_url), "0008_update_idempotency")

    with pytest.raises(SchemaNotCurrentError, match="scripts/migrate.sh"):
        require_current_schema(database_url)


def test_unversioned_nonempty_database_is_rejected_without_automatic_stamp(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'unknown.sqlite3'}"
    with create_engine(database_url).begin() as connection:
        connection.exec_driver_sql("CREATE TABLE legacy_data (id INTEGER PRIMARY KEY)")

    with pytest.raises(UnknownDatabaseSchemaError, match="alembic_version"):
        run_migrations(database_url)


def test_migrated_sqlite_database_keeps_concurrent_worker_claim_atomic(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'workers.sqlite3'}"
    run_migrations(database_url)
    factory = make_session_factory(database_url)
    user = UserStore(factory).get_or_create(9801)
    action = SqlActionQueueStore(factory).enqueue(
        user_id=user.id,
        action_type=ActionType.TASK_CREATE,
        payload={"title": "concurrent migration check"},
    )
    barrier = Barrier(2)

    def claim(worker_id: str):
        barrier.wait()
        return SqlActionQueueStore(factory).claim_next(worker_id=worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim, ["worker-a", "worker-b"]))

    winners = [item for item in claimed if item is not None]
    assert [item.id for item in winners] == [action.id]
