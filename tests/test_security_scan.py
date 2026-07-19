from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.security_scan import scan_repository


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _commit(root: Path, message: str) -> None:
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=scan@example.com", "-c", "user.name=Scan", "commit", "-q", "-m", message)


def test_security_scan_flags_sensitive_files_removed_from_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / ".env").write_text("TELEGRAM_API_ID=123456\n", encoding="utf-8")
    _commit(tmp_path, "leak")
    (tmp_path / ".env").unlink()
    _commit(tmp_path, "remove leak")

    findings = scan_repository(tmp_path)

    assert any(
        finding.startswith("history-sensitive-file:") and finding.endswith(":.env")
        for finding in findings
    ), findings


def test_security_scan_ignores_clean_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    (tmp_path / "notes.txt").write_text("nothing secret\n", encoding="utf-8")
    _commit(tmp_path, "notes")

    assert scan_repository(tmp_path) == []
