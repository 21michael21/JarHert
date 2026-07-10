from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable


_PATTERNS = {
    "secret": re.compile(
        r"\b(?:sk-(?:proj|or)-[A-Za-z0-9_-]{16,}|[0-9]{8,10}:AA[A-Za-z0-9_-]{30,})\b"
    ),
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "url": re.compile(r"https?://[^\s<>()]+", re.IGNORECASE),
    "phone": re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){10,15}(?!\w)"),
    "telegram_handle": re.compile(r"(?<!\w)@[A-Za-z0-9_]{5,32}\b"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "credential": re.compile(
        r"\b(?:api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+",
        re.IGNORECASE,
    ),
    "local_path": re.compile(r"(?:~|/(?:Users|home|opt|private|var/folders))/[^\s,;]+", re.IGNORECASE),
    "personal_name": re.compile(
        r"(?i:\b(?:меня\s+зовут|мо[её]\s+имя|зовут\s+меня))\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]{1,40}",
    ),
}

_PLACEHOLDERS = {
    "secret": "[SECRET]",
    "email": "[EMAIL]",
    "url": "[URL]",
    "phone": "[PHONE]",
    "telegram_handle": "[TELEGRAM_HANDLE]",
    "ip_address": "[IP_ADDRESS]",
    "credential": "[CREDENTIAL]",
    "local_path": "[LOCAL_PATH]",
    "personal_name": "[PERSON_NAME]",
}


def redact_training_text(text: str) -> tuple[str, set[str]]:
    redacted = str(text or "")
    findings: set[str] = set()
    for name, pattern in _PATTERNS.items():
        if pattern.search(redacted):
            findings.add(name)
            redacted = pattern.sub(_PLACEHOLDERS[name], redacted)
    return redacted.strip(), findings


def build_consented_record(
    *,
    system_prompt: str,
    user_text: str,
    assistant_text: str,
    source_turn_id: int,
) -> dict[str, Any]:
    clean_user, _ = redact_training_text(user_text)
    clean_assistant, _ = redact_training_text(assistant_text)
    if not clean_user or not clean_assistant:
        raise ValueError("Consented training pair must contain user and assistant text")
    return {
        "messages": [
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": clean_user},
            {"role": "assistant", "content": clean_assistant},
        ],
        "metadata": {"source": "consented_conversation_turn", "turn_id": source_turn_id},
    }


def redact_dataset_rows(rows: Iterable[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    sanitized: list[dict[str, Any]] = []
    findings: Counter[str] = Counter()
    for row in rows:
        messages = row.get("messages")
        if not isinstance(messages, list):
            sanitized.append({"messages": messages})
            continue
        clean_messages: list[object] = []
        for message in messages:
            if not isinstance(message, dict):
                clean_messages.append(message)
                continue
            clean_message = dict(message)
            clean_message["content"], found = redact_training_text(str(message.get("content") or ""))
            findings.update(found)
            clean_messages.append(clean_message)
        sanitized.append({"messages": clean_messages})
    return sanitized, dict(findings)


def audit_dataset_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    role_counts: Counter[str] = Counter()
    privacy_findings: Counter[str] = Counter()
    row_count = 0
    dialogue_rows = 0
    invalid_rows = 0
    for row in rows:
        row_count += 1
        messages = row.get("messages")
        if not isinstance(messages, list):
            invalid_rows += 1
            continue
        roles: set[str] = set()
        for message in messages:
            if not isinstance(message, dict):
                invalid_rows += 1
                continue
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "")
            roles.add(role)
            role_counts[role] += 1
            _, findings = redact_training_text(content)
            privacy_findings.update(findings)
        if {"user", "assistant"} <= roles:
            dialogue_rows += 1
    return {
        "rows": row_count,
        "dialogue_rows": dialogue_rows,
        "invalid_rows": invalid_rows,
        "role_counts": dict(role_counts),
        "privacy_findings": dict(privacy_findings),
        "human_review_required": True,
    }
