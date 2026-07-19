from __future__ import annotations

import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(
    r"(?:[0-9]{8,10}:AA[A-Za-z0-9_-]{25,}|"
    r"sk-(?:or-v1|proj)-[A-Za-z0-9_-]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,})"
)
SENSITIVE_NAMES = {".env", "token.json", "client_secret.json", "credentials.json"}
_SYNTHETIC_TEST_SECRET = "sk-" "proj-abcdefghijklmnopqrstuvwxyz123456"
_SYNTHETIC_TEST_PATH = "tests/test_hermes_skill_distillation.py"


def find_secret_locations(files: dict[str, str]) -> list[str]:
    findings: list[str] = []
    for path, content in files.items():
        for line_number, line in enumerate(content.splitlines(), start=1):
            if SECRET_PATTERN.search(line) and not _is_known_synthetic_test_value(path, line):
                findings.append(f"{path}:{line_number}")
    return findings


def scan_repository(root: Path = PROJECT_ROOT) -> list[str]:
    tracked = _git(root, "ls-files").splitlines()
    findings = [f"tracked-sensitive-file:{path}" for path in tracked if Path(path).name in SENSITIVE_NAMES]
    files: dict[str, str] = {}
    for relative in tracked:
        path = root / relative
        try:
            files[relative] = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
    findings.extend(find_secret_locations(files))

    grep_pattern = (
        r"([0-9]{8,10}:AA[A-Za-z0-9_-]{25,}|sk-(or-v1|proj)-[A-Za-z0-9_-]{20,}|"
        r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|AIza[0-9A-Za-z_-]{35}|"
        r"gh[pousr]_[A-Za-z0-9]{30,})"
    )
    for revision in _git(root, "rev-list", "--all").splitlines():
        for name in _git(root, "ls-tree", "-r", "--name-only", revision).splitlines():
            if Path(name).name in SENSITIVE_NAMES:
                findings.append(f"history-sensitive-file:{revision[:12]}:{name}")
        result = subprocess.run(
            ["git", "grep", "-n", "-I", "-E", grep_pattern, revision, "--"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode not in {0, 1}:
            raise RuntimeError(f"git grep failed for {revision[:12]}")
        for line in result.stdout.splitlines():
            parts = line.split(":", 3)
            if len(parts) >= 4 and not _is_known_synthetic_test_value(parts[1], parts[3]):
                findings.append(f"history:{parts[0][:12]}:{parts[1]}:{parts[2]}")
    return sorted(set(findings))


def _is_known_synthetic_test_value(path: str, line: str) -> bool:
    """Keep a single redaction fixture from making historical secret scans unusable."""
    return path == _SYNTHETIC_TEST_PATH and _SYNTHETIC_TEST_SECRET in line


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout


def main() -> int:
    findings = scan_repository()
    if findings:
        print(f"security_scan=failed findings={len(findings)}")
        for finding in findings:
            print(f"- {finding}")
        return 1
    print("security_scan=passed tracked_secrets=0 history_secrets=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
