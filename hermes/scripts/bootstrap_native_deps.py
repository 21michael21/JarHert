from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    if importlib.util.find_spec("telethon") is not None:
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
    if uv:
        argv = [uv, "pip", "install", "--python", sys.executable, "-r", str(requirements)]
    else:
        argv = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    result = subprocess.run(argv, check=False)
    if result.returncode != 0:
        print("Failed to install native profile dependencies.")
        return result.returncode
    print("Native profile dependencies installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
