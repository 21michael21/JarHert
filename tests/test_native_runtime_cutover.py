from __future__ import annotations

from pathlib import Path


def test_primary_local_check_is_hermes_native_only() -> None:
    root = Path(__file__).resolve().parents[1]
    check = (root / "scripts" / "native_check.sh").read_text(encoding="utf-8")
    local_check = (root / "scripts" / "local_check.sh").read_text(encoding="utf-8")

    assert "test_hermes_personal_os.py" in check
    assert "test_hermes_personal_database.py" in check
    assert "test_hermes_dashboard.py" in check
    assert "test_live_hermes_e2e_helpers.py" in check
    assert "compileall hermes/native_tools hermes/scripts deploy/vps" in check
    assert "scripts/native_check.sh" in local_check
    for legacy_runtime in ("gateway_bot", "assistant/", "backend/", "reminders", "telegram_collector", "scripts/migrate.sh"):
        assert legacy_runtime not in check


def test_native_release_gate_has_its_own_live_proof_switch() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / "scripts" / "native_release_gate.sh").read_text(encoding="utf-8")

    assert "scripts/native_check.sh" in gate
    assert 'CHECK_PYTHON="${NATIVE_CHECK_PYTHON:-$ROOT/.venv/bin/python}"' in gate
    assert 'NATIVE_CHECK_PYTHON="$CHECK_PYTHON"' in gate
    assert "NATIVE_RELEASE_ALLOW_LIVE" in gate
    assert "scripts/live_hermes_e2e.py" in gate
    assert "--allow-live" in gate
    assert "live_native_telegram" in gate


def test_native_profile_bootstrap_includes_dashboard_dependencies() -> None:
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "hermes" / "requirements-native.txt").read_text(encoding="utf-8")
    bootstrap = (root / "hermes" / "scripts" / "bootstrap_native_deps.py").read_text(encoding="utf-8")

    assert "fastapi" in requirements
    assert "uvicorn" in requirements
    assert "import mcp, telethon, fastapi, uvicorn" in bootstrap


def test_legacy_runtime_source_roots_are_removed_from_the_native_distribution() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = (root / "pyproject.toml").read_text(encoding="utf-8")

    for legacy_root in ("assistant", "backend", "gateway_bot", "reminders", "telegram_collector", "alembic"):
        assert not (root / legacy_root).exists(), legacy_root
    for legacy_file in ("Dockerfile", "docker-compose.yml", "docker-compose.postgres.yml", "alembic.ini"):
        assert not (root / legacy_file).exists(), legacy_file
    for legacy_package in ('"assistant*"', '"backend*"', '"gateway_bot*"', '"reminders*"', '"telegram_collector*"'):
        assert legacy_package not in manifest
    assert '"hermes*"' in manifest


def test_native_profile_has_no_legacy_backend_fallback_or_legacy_operator_docs() -> None:
    root = Path(__file__).resolve().parents[1]
    config = (root / "hermes" / "config.yaml").read_text(encoding="utf-8")
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    runner = (root / "scripts" / "coding_runner.py").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    for stale_value in ("JARHERT_BACKEND_URL", "ASSISTANT_SERVICE_TOKEN", "RemoteCodingQueueClient"):
        assert stale_value not in config
        assert stale_value not in env_example
        assert stale_value not in runner
    for stale_section in ("gateway_bot", "docker compose", "Alembic", "legacy-only"):
        assert stale_section not in readme
