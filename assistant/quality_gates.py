from __future__ import annotations

import re

from assistant.style_quality import assess_communication_style
from assistant.types import GateResult, GateStatus


DANGEROUS_PATTERNS = [
    r"\b(rm\s+-rf|sudo|chmod|chown|docker\s|kubectl\s|ssh\s|scp\s)\b",
    r"\b(read|cat|show|print)\b.*\b(\.env|secret|token|private\s*key)\b",
    r"\b(удали|сотри|прочитай|покажи|выведи)\b.*\b(\.env|секрет|токен|ключ|сервер)\b",
    r"\b(зайди|подключись)\b.*\b(сервер|ssh|vds|root)\b",
]

SECRET_MARKERS = [
    ".env",
    "secret",
    "token",
    "private key",
    "секрет",
    "токен",
    "приватный ключ",
]

SECRET_ACCESS_VERBS = [
    "read",
    "cat",
    "show",
    "print",
    "прочитай",
    "покажи",
    "выведи",
    "скинь",
]

RAW_ERROR_PATTERNS = [
    r"\bapi[_ -]?key\b",
    r"\b429\b.*\brate\b",
    r"\b400\b|\b401\b|\b403\b|\b429\b|\b500\b|\b502\b|\b503\b",
    r"^http [45]\d\d\b",
    r"\{\"error\"",
    r"\b(?:rate_limit|ratelimit|badrequest|httperror|apierror)\b",
    r"\b(?:openai|openrouter|gemini|groq)\.[a-z_]*error\b",
]

HTML_TRACEBACK_PATTERNS = [
    r"traceback \(most recent call last\)",
    r"\b(?:valueerror|runtimeerror|typeerror|keyerror|exception):",
    r"<!doctype\s+html",
    r"<html[\s>]",
    r"<body[\s>]",
    r"</html>",
]

AI_SLOP_PATTERNS = [
    r"\bas an ai\b",
    r"\bas a language model\b",
    r"\bя\s+как\s+(?:ии|ai|искусственный интеллект)\b",
    r"\bкак\s+(?:ии|ai|искусственный интеллект)\s*,?\s*я\b",
    r"\bя\s+(?:являюсь|не являюсь)\s+(?:ии|ai|искусственным интеллектом)\b",
    r"\bмне нужно ответить\b",
    r"\bсначала подумаю\b",
    r"\bвопрос (?:именно )?о том\b",
    r"\bвозможно, имеется в виду\b",
]


def check_input(text: str, *, max_chars: int = 4000) -> GateResult:
    safe_text = (text or "").strip()
    if not safe_text:
        return GateResult(GateStatus.BLOCKED, "empty_input")
    if len(safe_text) > max_chars:
        return GateResult(GateStatus.BLOCKED, "input_too_long")

    lowered = safe_text.lower()
    if any(marker in lowered for marker in SECRET_MARKERS) and any(
        verb in lowered for verb in SECRET_ACCESS_VERBS
    ):
        return GateResult(GateStatus.BLOCKED, "dangerous_action_requested")

    for pattern in DANGEROUS_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return GateResult(GateStatus.BLOCKED, "dangerous_action_requested")

    return GateResult(GateStatus.OK, safe_text=safe_text)


def check_output(text: str, *, max_chars: int = 2500) -> GateResult:
    safety_gate = check_output_safety(text)
    if not safety_gate.ok:
        return safety_gate
    safe_text = safety_gate.safe_text
    if len(safe_text) > max_chars:
        return GateResult(GateStatus.NEEDS_FALLBACK, "output_too_long")

    if _has_repetitive_water(safe_text):
        return GateResult(GateStatus.NEEDS_FALLBACK, "repetitive_water")

    if not assess_communication_style(safe_text).ok:
        return GateResult(GateStatus.NEEDS_FALLBACK, "style_slop")

    return GateResult(GateStatus.OK, safe_text=safe_text)


def check_output_safety(text: str) -> GateResult:
    safe_text = (text or "").strip()
    if not safe_text:
        return GateResult(GateStatus.NEEDS_FALLBACK, "empty_output")

    lowered = safe_text.lower()
    if _contains_unsafe_instruction(lowered):
        return GateResult(GateStatus.NEEDS_FALLBACK, "unsafe_instruction")

    for pattern in HTML_TRACEBACK_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return GateResult(GateStatus.NEEDS_FALLBACK, "html_or_traceback")

    for pattern in RAW_ERROR_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return GateResult(GateStatus.NEEDS_FALLBACK, "raw_provider_error")

    for pattern in AI_SLOP_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return GateResult(GateStatus.NEEDS_FALLBACK, "ai_slop_marker")

    return GateResult(GateStatus.OK, safe_text=safe_text)


def _contains_unsafe_instruction(lowered_text: str) -> bool:
    unsafe_markers = (
        r"\brm\s+-rf\b",
        r"\bsudo\s+",
        r"\bchmod\s+777\b",
        r"\bcurl\b.*\|\s*(?:sh|bash)",
        r"\bssh\s+root@",
        r"\b(?:прочитай|покажи|выведи)\b.*\b(?:\.env|токен|секрет|ключ)\b",
    )
    return any(re.search(pattern, lowered_text, flags=re.IGNORECASE) for pattern in unsafe_markers)


def _has_repetitive_water(text: str) -> bool:
    sentences = [
        _normalize_sentence(part)
        for part in re.split(r"[.!?。！？]+", text)
        if _normalize_sentence(part)
    ]
    counts: dict[str, int] = {}
    for sentence in sentences:
        counts[sentence] = counts.get(sentence, 0) + 1
        if counts[sentence] >= 3:
            return True

    lowered = text.lower()
    water_phrases = (
        "важно отметить",
        "стоит отметить",
        "в современном мире",
        "в конечном итоге",
        "на сегодняшний день",
    )
    return any(lowered.count(phrase) >= 3 for phrase in water_phrases)


def _normalize_sentence(value: str) -> str:
    return " ".join(value.lower().strip(" \n\t,;:—-").split())
