from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hermes.native_tools.coding_jobs import NativeCodingJobStore
from hermes.scripts.dispatch_channel_digest import parse_channels, queue_channel_digests


class _Export:
    def __init__(self, path: Path) -> None:
        self.path = path


class _Analysis:
    def __init__(self, text: str) -> None:
        self.text = text


def _run(tmp_path: Path, channels: list[str], *, now: datetime) -> tuple[list[dict[str, object]], NativeCodingJobStore]:
    store = NativeCodingJobStore(tmp_path / "personal.sqlite3")
    exports: list[str] = []

    def export_runner(*, peer: str, output_format: str, limit: int) -> _Export:
        exports.append(f"{peer}:{limit}")
        return _Export(tmp_path / f"{peer.lstrip('@')}.txt")

    def analysis_reader(path: Path) -> _Analysis:
        return _Analysis(f"текст канала {path.name}")

    results = queue_channel_digests(
        channels=channels,
        limit=200,
        owner_id=42,
        store=store,
        export_runner=export_runner,
        analysis_reader=analysis_reader,
        now=now,
    )
    assert exports == [f"{channel}:200" for channel in channels]
    return results, store


def test_channel_digest_queues_one_research_job_per_channel(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)

    results, store = _run(tmp_path, ["@news", "@tech"], now=now)

    assert [item["status"] for item in results] == ["queued", "queued"]
    job = store.get_for_user(int(results[0]["job_id"]), tg_user_id=42)
    assert job.mode == "research"
    assert job.deliver_result is True
    assert "дайджест" in job.prompt
    assert job.source_label == "digest:@news:2026-07-20"


def test_channel_digest_is_idempotent_per_day(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, 8, 30, tzinfo=timezone.utc)
    first, store = _run(tmp_path, ["@news"], now=now)
    second, _ = _run(tmp_path / "second", ["@news"], now=now) if False else (None, None)

    # Same store, second run reuses the idempotency key.
    store2 = NativeCodingJobStore(tmp_path / "personal.sqlite3")
    rerun = queue_channel_digests(
        channels=["@news"],
        limit=200,
        owner_id=42,
        store=store2,
        export_runner=lambda **kwargs: _Export(tmp_path / "news.txt"),
        analysis_reader=lambda path: _Analysis("текст"),
        now=now,
    )

    assert rerun[0]["job_id"] == first[0]["job_id"]


def test_channel_digest_records_errors_without_stopping(tmp_path: Path) -> None:
    store = NativeCodingJobStore(tmp_path / "personal.sqlite3")

    results = queue_channel_digests(
        channels=["@broken", "@news"],
        limit=200,
        owner_id=42,
        store=store,
        export_runner=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("no peer")) if kwargs["peer"] == "@broken" else _Export(tmp_path / "ok.txt"),
        analysis_reader=lambda path: _Analysis("текст"),
        now=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )

    assert results[0] == {"channel": "@broken", "error": "RuntimeError"}
    assert results[1]["status"] == "queued"


def test_parse_channels_bounds_input() -> None:
    assert parse_channels(" @a, @b ,, @c ") == ["@a", "@b", "@c"]
    assert len(parse_channels(",".join(f"@c{i}" for i in range(20)))) == 10
    assert parse_channels("") == []
