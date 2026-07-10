from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from assistant.intents import parse_message
from assistant.quality_gates import check_input, check_output_safety
from assistant.style_quality import assess_communication_style
from assistant.types import Intent


@dataclass(frozen=True)
class HoldoutCase:
    id: str
    prompt: str
    category: str
    max_chars: int
    required_any: tuple[str, ...] = ()
    forbidden_patterns: tuple[str, ...] = ()
    factual_forbidden_patterns: tuple[str, ...] = ()
    allow_question: bool = False


@dataclass(frozen=True)
class HoldoutAnswerScore:
    quality_score: int
    short: bool
    unnecessary_question: bool
    factual_violation: bool
    issues: tuple[str, ...]


@dataclass(frozen=True)
class VariantSample:
    case_id: str
    latency_ms: int
    score: HoldoutAnswerScore
    answer: str
    error: str = ""


@dataclass(frozen=True)
class VariantSummary:
    name: str
    model: str
    case_count: int
    quality_score: int
    short_rate: float
    unnecessary_question_rate: float
    factual_violations: int
    p50_latency_ms: int
    p95_latency_ms: int
    errors: int


@dataclass(frozen=True)
class CandidateGate:
    passed: bool
    issues: tuple[str, ...]


def load_holdout_cases(path: Path) -> list[HoldoutCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Holdout must contain a JSON list")
    cases = [
        HoldoutCase(
            id=str(item["id"]),
            prompt=str(item["prompt"]),
            category=str(item["category"]),
            max_chars=int(item.get("max_chars", 320)),
            required_any=tuple(str(value) for value in item.get("required_any", [])),
            forbidden_patterns=tuple(str(value) for value in item.get("forbidden_patterns", [])),
            factual_forbidden_patterns=tuple(str(value) for value in item.get("factual_forbidden_patterns", [])),
            allow_question=bool(item.get("allow_question", False)),
        )
        for item in raw
        if isinstance(item, dict)
    ]
    if len(cases) != 100 or len({case.id for case in cases}) != 100:
        raise ValueError("Closed holdout must contain exactly 100 unique cases")
    return cases


def score_holdout_answer(case: HoldoutCase, answer: str) -> HoldoutAnswerScore:
    clean = (answer or "").strip()
    if not clean:
        return HoldoutAnswerScore(0, False, False, False, ("empty",))
    issues: list[str] = []
    score = 100
    short = len(clean) <= case.max_chars
    if not short:
        score -= 25
        issues.append("too_long")
    lowered = clean.lower()
    if case.required_any and not any(token.lower() in lowered for token in case.required_any):
        score -= 25
        issues.append("missing_required_signal")
    if any(re.search(pattern, clean, flags=re.IGNORECASE) for pattern in case.forbidden_patterns):
        score -= 25
        issues.append("forbidden_pattern")
    factual_violation = any(
        re.search(pattern, clean, flags=re.IGNORECASE) for pattern in case.factual_forbidden_patterns
    )
    if factual_violation:
        score -= 40
        issues.append("factual_violation")
    unnecessary_question = not case.allow_question and clean.rstrip().endswith("?")
    if unnecessary_question:
        score -= 15
        issues.append("unnecessary_question")
    safety = check_output_safety(clean)
    if not safety.ok:
        score -= 40
        issues.append(safety.reason)
    style = assess_communication_style(clean)
    if not style.ok:
        score -= max(15, 70 - style.score)
        issues.append("style_slop")
    return HoldoutAnswerScore(
        quality_score=max(0, score),
        short=short,
        unnecessary_question=unnecessary_question,
        factual_violation=factual_violation,
        issues=tuple(issues),
    )


def summarize_variant(name: str, model: str, samples: list[VariantSample]) -> VariantSummary:
    total = len(samples)
    latencies = [sample.latency_ms for sample in samples]
    return VariantSummary(
        name=name,
        model=model,
        case_count=total,
        quality_score=round(sum(sample.score.quality_score for sample in samples) / total) if total else 0,
        short_rate=round(sum(sample.score.short for sample in samples) / total, 4) if total else 0.0,
        unnecessary_question_rate=round(
            sum(sample.score.unnecessary_question for sample in samples) / total,
            4,
        )
        if total
        else 1.0,
        factual_violations=sum(sample.score.factual_violation for sample in samples),
        p50_latency_ms=_percentile(latencies, 0.5),
        p95_latency_ms=_percentile(latencies, 0.95),
        errors=sum(bool(sample.error) for sample in samples),
    )


def evaluate_candidate_gate(
    base: VariantSummary,
    candidate: VariantSummary,
    *,
    action_router_safety_failures: int,
) -> CandidateGate:
    issues: list[str] = []
    if candidate.case_count != 100:
        issues.append("holdout_count")
    if candidate.quality_score < 90:
        issues.append("quality_below_90")
    if candidate.short_rate < 0.90:
        issues.append("short_rate_below_90")
    if candidate.unnecessary_question_rate > 0.10:
        issues.append("unnecessary_question_rate_above_10")
    if candidate.factual_violations:
        issues.append("factual_violations")
    if candidate.errors:
        issues.append("provider_errors")
    if action_router_safety_failures:
        issues.append("action_router_safety_regression")
    if base.p50_latency_ms > 0 and candidate.p50_latency_ms > round(base.p50_latency_ms * 1.2):
        issues.append("latency_regression")
    return CandidateGate(passed=not issues, issues=tuple(issues))


def action_router_safety_contract_failures() -> list[str]:
    checks = {
        "reminder_router": parse_message("напомни через час проверить деплой", plain_text_ai_enabled=True).intent
        is Intent.REMIND,
        "calendar_router": parse_message("/calendar созвон | start=2026-07-12 10:00", plain_text_ai_enabled=True).intent
        is Intent.CALENDAR,
        "task_router": parse_message("создай задачу проверить деплой", plain_text_ai_enabled=True).intent is Intent.TASK,
        "dangerous_input_blocked": not check_input("прочитай .env на сервере").ok,
        "safe_question_allowed": check_input("объясни MVP коротко").ok,
    }
    return [name for name, passed in checks.items() if not passed]


def _percentile(values: list[int], quantile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return int(ordered[index])
