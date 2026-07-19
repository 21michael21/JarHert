"""Shared input validation helpers for JarHert Personal OS stores.

Only the byte-identical copies live here. Stores with deliberately
different error contracts keep their local variants.
"""

from __future__ import annotations


def required(value: str, label: str, *, limit: int) -> str:
    clean = " ".join(str(value or "").split())
    if not clean:
        raise ValueError(f"{label} не должен быть пустым.")
    if len(clean) > limit:
        raise ValueError(f"{label} превышает лимит {limit} символов.")
    return clean


def optional(value: str | None, *, limit: int) -> str | None:
    if value is None or not str(value).strip():
        return None
    return required(value, "Value", limit=limit)


def allowed(value: str, allowed_values: frozenset[str], label: str) -> str:
    clean = required(value, label, limit=40).casefold()
    if clean not in allowed_values:
        raise ValueError(f"{label} отсутствует в allowlist.")
    return clean


def bounded(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"
