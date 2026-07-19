from __future__ import annotations

import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def main() -> int:
    profile_home = Path(os.getenv("HERMES_HOME", Path(__file__).resolve().parents[1])).expanduser()
    environment = profile_home / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if python.is_file() and _has_native_dependencies(python):
        print("Native profile dependencies are installed.")
        return 0
    requirements = Path(__file__).resolve().parents[1] / "requirements-native.txt"
    uv = shutil.which("uv")
    if not uv:
        for candidate in (
            Path.home() / ".hermes" / "bin" / "uv",
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ):
            if candidate.is_file():
                uv = str(candidate)
                break
    if not python.is_file():
        if uv:
            created = subprocess.run(
                [uv, "venv", "--python", sys.executable, str(environment)],
                check=False,
            )
            if created.returncode != 0:
                return created.returncode
        else:
            venv.EnvBuilder(with_pip=True).create(environment)
    if uv:
        argv = [uv, "pip", "install", "--python", str(python), "-r", str(requirements)]
    else:
        argv = [str(python), "-m", "pip", "install", "-r", str(requirements)]
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        print("Failed to install native profile dependencies.")
        return result.returncode
    print("Native profile dependencies installed.")
    return 0


def _has_native_dependencies(python: Path) -> bool:
    result = subprocess.run(
        [str(python), "-c", "import mcp, telethon, fastapi, uvicorn, pypdf"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


if __name__ == "__main__":
    raise SystemExit(main())
