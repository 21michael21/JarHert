from __future__ import annotations

import subprocess

from scripts.setup_coding_profile import ensure_coding_profile


def test_setup_creates_and_configures_dedicated_docker_profile() -> None:
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[-1] == "status":
            return subprocess.CompletedProcess(argv, 0, stdout="Backend: docker", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    created = ensure_coding_profile(
        source_binary="jarhert",
        coding_binary="jarhert-coding",
        coding_profile="jarhert-coding",
        binary_exists=lambda _binary: False,
        execute=execute,
    )

    assert created is True
    assert calls[0] == [
        "jarhert",
        "profile",
        "create",
        "jarhert-coding",
        "--clone-from",
        "jarhert",
        "--description",
        "Isolated local Docker profile for JarHert coding jobs.",
    ]
    assert ["jarhert-coding", "config", "set", "terminal.backend", "docker"] in calls
    assert ["jarhert-coding", "config", "set", "terminal.container_persistent", "false"] in calls
    assert calls[-1] == ["jarhert-coding", "status"]


def test_setup_keeps_existing_profile_and_verifies_docker_backend() -> None:
    calls: list[list[str]] = []

    def execute(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="Backend: docker", stderr="")

    created = ensure_coding_profile(
        source_binary="jarhert",
        coding_binary="jarhert-coding",
        coding_profile="jarhert-coding",
        binary_exists=lambda _binary: True,
        execute=execute,
    )

    assert created is False
    assert not any("profile" in call for call in calls)
