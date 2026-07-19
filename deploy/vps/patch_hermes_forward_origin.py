#!/usr/bin/env python3
"""Prefix forwarded Telegram messages with their source chat for JarHert.

The Hermes gateway builds a MessageEvent with the raw message text only, so
the agent cannot tell which chat a forwarded message came from. This patch
teaches the adapter to prepend a compact ``[Переслано из: ...]`` prefix so a
follow-up request like "спарси этот чат" has a concrete peer to work with.
Fail-closed: unknown adapter revisions abort without changes.
"""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER = "# JarHert forward-origin prefix v1."

ANCHOR = """        return MessageEvent(
            text=message.text or "",
"""

PATCHED = """        forward_label = _jarhert_forward_origin_label(message)
        event_text = message.text or ""
        if forward_label:
            event_text = f"[Переслано из: {forward_label}]\\n{event_text}"
        return MessageEvent(
            text=event_text,
"""

HELPER = '''

def _jarhert_forward_origin_label(message: "Message") -> str:
    """Resolve a compact source label for a forwarded message, or empty."""
    origin = getattr(message, "forward_origin", None)
    for chat in (
        getattr(origin, "sender_chat", None),
        getattr(origin, "chat", None),
        getattr(message, "forward_from_chat", None),
    ):
        if chat is None:
            continue
        title = str(getattr(chat, "title", None) or "").strip()
        username = str(getattr(chat, "username", None) or "").strip()
        if title or username:
            return f"{title} (@{username})".strip() if username else title
    user = getattr(origin, "sender_user", None) or getattr(message, "forward_from", None)
    if user is not None:
        name = " ".join(
            part
            for part in (
                str(getattr(user, "first_name", None) or "").strip(),
                str(getattr(user, "last_name", None) or "").strip(),
            )
            if part
        )
        username = str(getattr(user, "username", None) or "").strip()
        label = name or username
        if label:
            return f"{label} (@{username})" if username and username != label else label
    hidden = str(getattr(origin, "sender_user_name", None) or "").strip()
    if hidden:
        return f"{hidden} (скрытый автор)"
    return ""
'''


def patch(path: Path) -> str:
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        return "forward_origin_patch=already_applied"
    if source.count(ANCHOR) != 1:
        raise RuntimeError("Unsupported Hermes adapter revision: MessageEvent anchor not found.")
    if "def _jarhert_forward_origin_label" in source:
        raise RuntimeError("Forward-origin helper already exists without the patch marker.")
    source = source.replace(ANCHOR, PATCHED)
    source = source.rstrip("\n") + "\n" + HELPER + "\n"
    path.write_text(f"{MARKER}\n{source}", encoding="utf-8")
    return "forward_origin_patch=applied"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(patch(args.target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
