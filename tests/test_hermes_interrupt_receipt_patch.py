from __future__ import annotations

from deploy.vps.patch_hermes_interrupt_receipt import (
    _KNOWN_BRANCHES,
    _OLD_BRANCH,
    _PARTIAL_PREFIX,
    _RECEIPT_MARKER,
    patch_source,
)


def test_patch_recovers_a_completed_plan_before_a_technical_partial_reply() -> None:
    source = _PARTIAL_PREFIX + _OLD_BRANCH

    patched = patch_source(source)

    assert _RECEIPT_MARKER in patched
    assert patched.index("_receipt_messages = []") < patched.index("if _partial:")
    assert '"status": "succeeded"' in patched
    assert '"actions":' in patched
    assert 'getattr(agent, "_session_messages", [])' in patched
    assert patch_source(patched) == patched


def test_patch_upgrades_every_known_deployed_branch_shape() -> None:
    for branch in _KNOWN_BRANCHES:
        patched = patch_source(_PARTIAL_PREFIX + branch)
        assert _RECEIPT_MARKER in patched
        assert "Готово: подтверждённый план выполнен." in patched


def test_patch_rejects_an_unknown_upstream_shape() -> None:
    try:
        patch_source("def unrelated():\n    pass\n")
    except RuntimeError as error:
        assert "target" in str(error).lower()
    else:  # pragma: no cover - assertion guard
        raise AssertionError("The patch must fail closed for an unknown Hermes version.")
