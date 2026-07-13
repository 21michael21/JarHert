from __future__ import annotations

import subprocess

import pytest

from hermes.native_tools.sandbox_worker import SandboxTask, SandboxedHermesWorker


def test_coding_task_runs_same_hermes_profile_with_docker_backend() -> None:
    calls: list[tuple[list[str], dict[str, str], int]] = []

    def execute(argv, *, env, timeout, **_kwargs):
        calls.append((argv, env, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout="done\n", stderr="")

    worker = SandboxedHermesWorker(
        profile_binary="jarhert",
        execute=execute,
        docker_available=lambda: True,
        allowed_research_hosts={"docs.python.org"},
    )

    result = worker.run(
        SandboxTask(
            mode="coding",
            prompt="Добавь тест и исправь баг",
            repository_url="https://github.com/example/project.git",
        )
    )

    assert result.output == "done"
    argv, env, timeout = calls[0]
    assert argv[:2] == ["jarhert", "chat"]
    assert "--yolo" in argv
    assert argv[argv.index("--toolsets") + 1] == "coding"
    assert "sandboxed-coding" in argv
    assert env["TERMINAL_ENV"] == "docker"
    assert env["TERMINAL_DOCKER_FORWARD_ENV"] == "[]"
    assert env["TERMINAL_DOCKER_VOLUMES"] == "[]"
    assert env["TERMINAL_DOCKER_ENV"] == "{}"
    assert env["TERMINAL_DOCKER_EXTRA_ARGS"] == "[]"
    assert env["TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE"] == "false"
    assert env["TERMINAL_CONTAINER_PERSISTENT"] == "false"
    assert env["TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES"] == "false"
    assert timeout == 900


def test_non_github_or_non_https_repository_is_rejected() -> None:
    worker = SandboxedHermesWorker(docker_available=lambda: True)

    with pytest.raises(ValueError, match="GitHub HTTPS"):
        worker.run(SandboxTask(mode="coding", prompt="test", repository_url="file:///etc"))


def test_research_source_must_be_in_explicit_allowlist() -> None:
    worker = SandboxedHermesWorker(
        docker_available=lambda: True,
        allowed_research_hosts={"docs.python.org"},
    )

    with pytest.raises(ValueError, match="allowlist"):
        worker.run(
            SandboxTask(
                mode="research",
                prompt="Сравни документацию",
                source_urls=("https://private.example/secrets",),
            )
        )


def test_worker_refuses_to_fall_back_to_host_when_docker_is_missing() -> None:
    worker = SandboxedHermesWorker(docker_available=lambda: False)

    with pytest.raises(RuntimeError, match="Docker sandbox недоступен"):
        worker.run(
            SandboxTask(
                mode="coding",
                prompt="test",
                repository_url="https://github.com/example/project.git",
            )
        )


def test_terminal_approval_timeout_is_not_reported_as_a_completed_coding_task() -> None:
    def execute(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="\u23f1 Timeout \u2014 denying command\n\u0420\u0430\u0437\u0440\u0435\u0448\u0438\u0442\u0435 \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c clone?",
            stderr="",
        )

    worker = SandboxedHermesWorker(execute=execute, docker_available=lambda: True)

    with pytest.raises(RuntimeError, match="terminal"):
        worker.run(
            SandboxTask(
                mode="coding",
                prompt="Исправь тест",
                repository_url="https://github.com/example/project",
            )
        )


def test_workspace_access_failure_is_not_reported_as_a_completed_coding_task() -> None:
    def execute(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="The workspace is read-only, so I cannot clone or create files.",
            stderr="",
        )

    worker = SandboxedHermesWorker(execute=execute, docker_available=lambda: True)

    with pytest.raises(RuntimeError, match="workspace"):
        worker.run(
            SandboxTask(
                mode="coding",
                prompt="Исправь тест",
                repository_url="https://github.com/example/project",
            )
        )


def test_sandbox_preflight_checks_cli_without_starting_an_agent_turn() -> None:
    calls = []

    def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="usage", stderr="")

    worker = SandboxedHermesWorker(profile_binary="jarhert", execute=execute, docker_available=lambda: True)
    worker.preflight()

    assert calls[0][0] == ["jarhert", "--help"]


def test_research_mode_uses_only_declared_sources() -> None:
    captured: list[list[str]] = []

    def execute(argv, **_kwargs):
        captured.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="report", stderr="")

    worker = SandboxedHermesWorker(
        execute=execute,
        docker_available=lambda: True,
        allowed_research_hosts={"docs.python.org"},
    )
    worker.run(
        SandboxTask(
            mode="research",
            prompt="Найди ограничения API",
            source_urls=("https://docs.python.org/3/library/sqlite3.html",),
        )
    )

    prompt = captured[0][captured[0].index("-q") + 1]
    assert "docs.python.org/3/library/sqlite3.html" in prompt
    assert "не используй другие источники" in prompt.lower()


def test_coding_prompt_requires_terminal_evidence_from_a_writable_workspace() -> None:
    prompt = _prompt_for(
        SandboxTask(
            mode="coding",
            prompt="Добавь тест",
            repository_url="https://github.com/example/project",
        )
    )

    normalized = prompt.lower()
    assert "/workspace и /workspace/task доступны для записи" in normalized
    assert "первым инструментом используй terminal" in normalized
    assert "не выдавай план или пример diff за выполненную работу" in normalized


def _prompt_for(task: SandboxTask) -> str:
    captured: list[list[str]] = []

    def execute(argv, **_kwargs):
        captured.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="done", stderr="")

    worker = SandboxedHermesWorker(execute=execute, docker_available=lambda: True)
    worker.run(task)
    return captured[0][captured[0].index("-q") + 1]
