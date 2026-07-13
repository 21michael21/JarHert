from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse


Execute = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SandboxTask:
    mode: str
    prompt: str
    repository_url: str | None = None
    source_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SandboxResult:
    output: str
    mode: str


class SandboxedHermesWorker:
    """Launch the same Hermes profile with its hardened Docker terminal backend."""

    def __init__(
        self,
        *,
        profile_binary: str = "jarhert",
        execute: Execute = subprocess.run,
        docker_available: Callable[[], bool] | None = None,
        allowed_research_hosts: set[str] | None = None,
    ) -> None:
        self.profile_binary = profile_binary
        self.execute = execute
        self.docker_available = docker_available or _docker_available
        self.allowed_research_hosts = {
            host.strip().lower()
            for host in (allowed_research_hosts or _research_hosts_from_env())
            if host.strip()
        }

    def run(self, task: SandboxTask) -> SandboxResult:
        if not self.docker_available():
            raise RuntimeError("Docker sandbox недоступен; запуск на host запрещён.")
        prompt = _build_prompt(task, self.allowed_research_hosts)
        toolsets = "coding" if task.mode == "coding" else "web,skills"
        argv = [
            self.profile_binary,
            "-z",
            prompt,
            "--toolsets",
            toolsets,
            "--skills",
            "sandboxed-coding",
            "--source",
            "sandbox-worker",
            "--max-turns",
            "40",
        ]
        if task.mode == "coding":
            # Commands are pre-approved only inside this forced, ephemeral Docker backend.
            # The worker clears all configurable mounts and forwarded environment variables.
            argv.append("--yolo")
        environment = os.environ.copy()
        environment.update(
            {
                "TERMINAL_ENV": "docker",
                "TERMINAL_DOCKER_FORWARD_ENV": "[]",
                "TERMINAL_DOCKER_VOLUMES": "[]",
                "TERMINAL_DOCKER_ENV": "{}",
                "TERMINAL_DOCKER_EXTRA_ARGS": "[]",
                "TERMINAL_DOCKER_MOUNT_CWD_TO_WORKSPACE": "false",
                "TERMINAL_DOCKER_RUN_AS_HOST_USER": "false",
                "TERMINAL_CONTAINER_PERSISTENT": "false",
                "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES": "false",
                "HERMES_SANDBOX_TASK": "1",
            }
        )
        result = self.execute(
            argv,
            env=environment,
            timeout=900,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Sandbox worker завершился с кодом {result.returncode}. Проверь Hermes logs."
            )
        output = (result.stdout or "").strip()[:20_000]
        if _requires_terminal_approval(output):
            raise RuntimeError("Sandbox worker остановился без результата: terminal-подтверждение или workspace unavailable.")
        return SandboxResult(output=output, mode=task.mode)

    def preflight(self) -> None:
        """Check Docker and the local profile CLI without starting an agent turn."""
        if not self.docker_available():
            raise RuntimeError("Docker sandbox недоступен; запуск на host запрещён.")
        try:
            result = self.execute(
                [self.profile_binary, "--help"],
                timeout=15,
                text=True,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError("Hermes profile CLI недоступен для coding runner.") from error
        if result.returncode != 0:
            raise RuntimeError("Hermes profile CLI недоступен для coding runner.")


def _build_prompt(task: SandboxTask, allowed_hosts: set[str]) -> str:
    mode = task.mode.strip().lower()
    user_prompt = " ".join(task.prompt.split())
    if mode not in {"coding", "research"}:
        raise ValueError("Sandbox mode должен быть coding или research.")
    if not user_prompt or len(user_prompt) > 5000:
        raise ValueError("Sandbox prompt должен содержать от 1 до 5000 символов.")

    if mode == "coding":
        repository = _validate_github_repository(task.repository_url)
        return (
            "Работай только внутри Docker workspace. Клонируй репозиторий "
            f"{repository} в /workspace/task. Задача: {user_prompt}\n"
            "/workspace и /workspace/task доступны для записи внутри одноразового Docker контейнера. "
            "Первым инструментом используй terminal: проверь pwd и создай /workspace/task. "
            "Не выдавай план или пример diff за выполненную работу: сначала получи фактический результат "
            "terminal, затем верни настоящий diff и вывод проверки. "
            "Сначала изучи код, затем сделай отдельную ветку, минимальный diff и тесты. "
            "Не читай host-файлы, не ищи credentials, не push, не merge и не deploy. "
            "Верни итог, проверки и diff summary."
        )

    sources = tuple(_validate_research_url(url, allowed_hosts) for url in task.source_urls)
    if not sources:
        raise ValueError("Research task требует хотя бы один разрешённый source URL.")
    if len(sources) > 10:
        raise ValueError("Research task поддерживает не более 10 source URLs.")
    source_list = "\n".join(f"- {url}" for url in sources)
    return (
        f"Исследовательская задача: {user_prompt}\n"
        f"Разрешённые источники:\n{source_list}\n"
        "Не используй другие источники. Не вводи credentials и не выполняй внешние действия. "
        "Отдели факты от выводов, приложи ссылки и верни короткий отчёт."
    )


def _validate_github_repository(value: str | None) -> str:
    parsed = urlparse(str(value or "").strip())
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "github.com"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(parts) != 2
        or any(part in {".", ".."} for part in parts)
    ):
        raise ValueError("Coding repository должен быть GitHub HTTPS URL вида owner/repo.")
    return f"https://github.com/{parts[0]}/{parts[1]}"


def _validate_research_url(value: str, allowed_hosts: set[str]) -> str:
    parsed = urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Research source должен быть HTTPS URL без credentials.")
    if host not in allowed_hosts:
        raise ValueError(f"Research host '{host}' отсутствует в allowlist.")
    return value.strip()


def _research_hosts_from_env() -> set[str]:
    raw = os.getenv("HERMES_RESEARCH_ALLOWED_HOSTS", "github.com,api.github.com")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _requires_terminal_approval(output: str) -> bool:
    normalized = output.lower()
    return (
        "timeout — denying command" in normalized
        or "timeout - denying command" in normalized
        or "разрешите выполнить" in normalized
        or "approve this command" in normalized
        or "workspace is read-only" in normalized
        or "workspace read-only" in normalized
        or "рабочее пространство" in normalized and "только для чтения" in normalized
        or "cannot write to the docker workspace" in normalized
        or "не могу клонировать репозиторий или создавать" in normalized
        or "не могу клонировать и создавать" in normalized
    )
