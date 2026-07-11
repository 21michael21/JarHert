from __future__ import annotations

import re
from dataclasses import dataclass


GENERIC_PREAMBLE_PATTERNS = (
    r"^\s*конечно[!,.\s]",
    r"^\s*с удовольствием\b",
    r"\bдавайте\s+(?:разбер[её]мся|погрузимся|рассмотрим)\b",
    r"\bрад(?:а)?\s+помочь\b",
)
GENERIC_META_PATTERNS = (
    r"\bв современном мире\b",
    r"\bважно отметить,? что\b",
    r"\bстоит отметить,? что\b",
    r"\bв заключение\b",
    r"\bкомплексный подход\b",
    r"\bраскрыть потенциал\b",
)
AI_IDENTITY_PATTERNS = (
    r"\bя как (?:ии|ai|искусственный интеллект)\b",
    r"\bкак языковая модель\b",
)
ROBOTIC_CHAT_PATTERNS = (
    r"^\s*принял[,.]?\s*(?:обрабатываю|выполняю)\b",
    r"\bитог пришлю отдельным сообщением\b",
    r"\bкак могу помочь\??\s*$",
    r"\bуточняющий вопрос:\s*",
)


@dataclass(frozen=True)
class StyleAssessment:
    score: int
    issues: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.score >= 70


def assess_communication_style(text: str) -> StyleAssessment:
    clean = (text or "").strip()
    if not clean:
        return StyleAssessment(0, ("empty",))
    lowered = clean.lower()
    score = 100
    issues: list[str] = []

    preamble_hits = sum(bool(re.search(pattern, lowered)) for pattern in GENERIC_PREAMBLE_PATTERNS)
    if preamble_hits:
        score -= min(60, 25 * preamble_hits)
        issues.append("generic_preamble")

    meta_hits = sum(bool(re.search(pattern, lowered)) for pattern in GENERIC_META_PATTERNS)
    if meta_hits:
        score -= min(45, 15 * meta_hits)
        issues.append("generic_meta")

    if any(re.search(pattern, lowered) for pattern in AI_IDENTITY_PATTERNS):
        score -= 60
        issues.append("ai_identity")

    robotic_hits = sum(bool(re.search(pattern, lowered)) for pattern in ROBOTIC_CHAT_PATTERNS)
    if robotic_hits:
        score -= min(70, 35 * robotic_hits)
        issues.append("robotic_chat")

    if clean.count("!") >= 3:
        score -= 35
        issues.append("excessive_exclamation")

    normalized_sentences = [
        " ".join(part.lower().split())
        for part in re.split(r"[.!?]+", clean)
        if part.strip()
    ]
    if len(normalized_sentences) != len(set(normalized_sentences)):
        score -= 35
        issues.append("repeated_sentence")

    return StyleAssessment(max(0, score), tuple(issues))
