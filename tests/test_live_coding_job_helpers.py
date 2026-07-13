from scripts.live_coding_job import coding_request, has_coding_evidence, require_live_approval


def test_coding_request_is_bounded_to_a_public_repository_and_no_side_effects() -> None:
    prompt = coding_request("canary-123")

    assert "https://github.com/octocat/Hello-World" in prompt
    assert "canary-123" in prompt
    assert "Не делай commit, push, merge или deploy" in prompt


def test_live_coding_runner_requires_explicit_external_action_flag() -> None:
    try:
        require_live_approval(False)
    except PermissionError as error:
        assert "--allow-live" in str(error)
    else:  # pragma: no cover - assertion path for an accidental safety regression.
        raise AssertionError("live coding runner must require explicit approval")


def test_live_coding_runner_requires_real_diff_evidence() -> None:
    assert has_coding_evidence("CODEX_CANARY_x: git diff --stat", "x") is True
    assert has_coding_evidence("CODEX_CANARY_x created", "x") is False
