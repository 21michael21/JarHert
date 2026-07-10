from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import json

from assistant.style_quality import assess_communication_style


@dataclass(frozen=True)
class StyleCase:
    id: str
    prompt: str
    max_chars: int = 500
    required_any: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class StyleResponseScore:
    ok: bool
    score: int
    issues: tuple[str, ...]


def load_style_cases(path: Path) -> list[StyleCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_cases, list):
        raise ValueError("Style A/B fixture must contain a JSON list")
    cases: list[StyleCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise ValueError("Style A/B case must be an object")
        cases.append(
            StyleCase(
                id=str(raw_case["id"]),
                prompt=str(raw_case["prompt"]),
                max_chars=int(raw_case.get("max_chars", 500)),
                required_any=tuple(str(value) for value in raw_case.get("required_any", [])),
                forbidden_patterns=tuple(str(value) for value in raw_case.get("forbidden_patterns", [])),
            )
        )
    return cases


def score_style_response(case: StyleCase, text: str) -> StyleResponseScore:
    clean = (text or "").strip()
    issues: list[str] = []
    score = 100
    if not clean:
        return StyleResponseScore(False, 0, ("empty",))
    if len(clean) > case.max_chars:
        score -= 25
        issues.append("too_long")
    if case.required_any and not any(token.lower() in clean.lower() for token in case.required_any):
        score -= 25
        issues.append("missing_required_signal")
    if any(re.search(pattern, clean, re.IGNORECASE) for pattern in case.forbidden_patterns):
        score -= 30
        issues.append("forbidden_pattern")

    style_assessment = assess_communication_style(clean)
    if not style_assessment.ok:
        score -= max(20, 70 - style_assessment.score)
        issues.append("style_slop")
    return StyleResponseScore(
        ok=score >= 70 and not issues,
        score=max(0, score),
        issues=tuple(issues),
    )


def choose_winner(*, base_score: int, candidate_score: int, minimum_gain: int = 3) -> str:
    if candidate_score >= base_score + minimum_gain:
        return "candidate"
    if base_score >= candidate_score + minimum_gain:
        return "base"
    return "no_change"


def is_promotion_eligible(
    *,
    average_score: int,
    passed: int,
    total: int,
    minimum_average_score: int = 85,
    minimum_pass_rate: float = 0.85,
) -> bool:
    if total <= 0:
        return False
    return average_score >= minimum_average_score and passed / total >= minimum_pass_rate
