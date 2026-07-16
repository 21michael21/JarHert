#!/usr/bin/env python3
"""Localize Hermes Telegram approval receipts without changing callback semantics."""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "# JarHert Russian Telegram approval labels v1."


def patch(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return "telegram_approval_patch=already_applied"
    if source.count('"once": "✅ Approved once",') != 2:
        raise RuntimeError("Unsupported Hermes Telegram approval callback source.")
    if source.count('text=self.format_message(f"{label} by {user_display}"),') != 2:
        raise RuntimeError("Unsupported Hermes Telegram approval receipt source.")

    replacements = {
        '"once": "✅ Approved once",': '"once": "✅ Подтверждено",',
        '"session": "✅ Approved for session",': '"session": "✅ Подтверждено на эту сессию",',
        '"always": "✅ Approved permanently",': '"always": "✅ Подтверждено всегда",',
        '"deny": "❌ Denied",': '"deny": "❌ Отклонено",',
        '"always": "🔒 Always approve",': '"always": "🔒 Всегда разрешать",',
        '"cancel": "❌ Cancelled",': '"cancel": "❌ Отменено",',
        'text=self.format_message(f"{label} by {user_display}"),': 'text=self.format_message(label),',
    }
    for before, after in replacements.items():
        source = source.replace(before, after)
    path.write_text(f"{MARKER}\n{source}", encoding="utf-8")
    return "telegram_approval_patch=applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(patch(args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
