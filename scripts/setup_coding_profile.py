from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from collections.abc import Callable


Execute = Callable[[list[str]], subprocess.CompletedProcess[str]]


def ensure_coding_profile(
    *,
    source_binary: str,
    coding_binary: str,
    coding_profile: str,
    binary_exists: Callable[[str], bool] = lambda binary: shutil.which(binary) is not None,
    execute: Execute | None = None,
) -> bool:
    """Create the isolated profile once, then enforce its Docker-only backend."""
    execute = execute or _execute
    created = False
    if not binary_exists(coding_binary):
        _require_success(
            execute(
                [
                    source_binary,
                    "profile",
                    "create",
                    coding_profile,
                    "--clone-from",
                    "jarhert",
                    "--description",
                    "Isolated local Docker profile for JarHert coding jobs.",
                ]
            ),
            "Не удалось создать локальный coding-профиль.",
        )
        created = True

    for key, value in (
        ("terminal.backend", "docker"),
        ("terminal.docker_image", "nikolaik/python-nodejs:python3.11-nodejs20"),
        ("terminal.container_persistent", "false"),
        ("terminal.docker_mount_cwd_to_workspace", "false"),
    ):
        _require_success(
            execute([coding_binary, "config", "set", key, value]),
            f"Не удалось настроить {key} для coding-профиля.",
        )

    status = execute([coding_binary, "status"])
    _require_success(status, "Не удалось проверить coding-профиль.")
    if not re.search(r"Backend:\s*docker\b", f"{status.stdout or ''}\n{status.stderr or ''}", re.IGNORECASE):
        raise RuntimeError("Coding-профиль не использует Docker backend.")
    return created


def _execute(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, text=True, capture_output=True, check=False)


def _require_success(result: subprocess.CompletedProcess[str], message: str) -> None:
    if result.returncode != 0:
        raise RuntimeError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or validate the local Docker-only Hermes coding profile.")
    parser.add_argument("--source-binary", default="jarhert")
    parser.add_argument("--coding-binary", default="jarhert-coding")
    parser.add_argument("--coding-profile", default="jarhert-coding")
    args = parser.parse_args()
    if shutil.which(args.source_binary) is None:
        raise SystemExit(f"Hermes source profile wrapper is unavailable: {args.source_binary}")

    created = ensure_coding_profile(
        source_binary=args.source_binary,
        coding_binary=args.coding_binary,
        coding_profile=args.coding_profile,
    )
    print(f"coding_profile_ready=true created={str(created).lower()} profile={args.coding_profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
