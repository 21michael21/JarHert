from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_profile_sync_prunes_rollback_snapshots_after_success() -> None:
    script = (ROOT / "deploy" / "vps" / "sync_hermes_profile.sh").read_text(encoding="utf-8")

    assert 'profile-sync-*' in script
    assert "-mtime +30" in script
    # Pruning happens only after the gateway came back healthy.
    assert script.index("is-active --quiet hermes-gateway-jarhert.service") < script.index("-mtime +30")
