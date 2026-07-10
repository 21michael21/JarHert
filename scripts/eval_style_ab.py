from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.style_ab import StyleCase, choose_winner, is_promotion_eligible, load_style_cases, score_style_response
from assistant.communication_style import constrain_response_length, load_communication_style
from assistant.response_policy import ResponsePolicy, classify_response_policy
from assistant.types import HermesRequest, UserContext
from assistant.provider_registry import build_provider_registry
from assistant.provider_transport import build_provider_client
from backend.config import Settings


BASELINE_PROMPT = (
    "Ты русскоязычный личный помощник. Отвечай по делу, без выдуманных фактов. "
    "Учитывай прямое требование пользователя к длине ответа."
)
DEFAULT_CASES = PROJECT_ROOT / "tests" / "style_ab_cases.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "style_ab"


@dataclass(frozen=True)
class VariantResult:
    score: int
    ok: bool
    issues: tuple[str, ...]
    latency_ms: int
    answer: str
    error: str = ""


def run_variant(
    client,
    case: StyleCase,
    system_prompt: str,
    *,
    response_policy: ResponsePolicy | None = None,
    max_output_chars: int | None = None,
) -> VariantResult:
    started = time.perf_counter()
    try:
        response = client.ask(
            HermesRequest(
                user=UserContext(user_id=0, tg_user_id=0),
                prompt=case.prompt,
                system_prompt=system_prompt,
                max_output_tokens=180,
            )
        )
        answer = response.text.strip()
        if max_output_chars is not None:
            answer = constrain_response_length(answer, max_chars=max_output_chars)
        if response_policy is not None:
            answer = response_policy.normalize(answer)
        assessment = score_style_response(case, answer)
        return VariantResult(
            score=assessment.score,
            ok=assessment.ok,
            issues=assessment.issues,
            latency_ms=response.latency_ms or round((time.perf_counter() - started) * 1000),
            answer=answer,
        )
    except Exception as exc:
        return VariantResult(
            score=0,
            ok=False,
            issues=("provider_error",),
            latency_ms=round((time.perf_counter() - started) * 1000),
            answer="",
            error=f"{type(exc).__name__}: {exc}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare JarHert base and candidate communication styles.")
    parser.add_argument("--candidate", type=Path, required=True, help="Local distilled style profile")
    parser.add_argument("--provider", default="openai_cheap")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--max-cases", type=int, default=0, help="Use a small subset only for a quick local check")
    args = parser.parse_args()

    candidate_guide = load_communication_style(enabled=True, path=str(args.candidate))
    cases = load_style_cases(args.cases)
    if args.max_cases:
        cases = cases[: args.max_cases]
    settings = Settings()
    try:
        provider = build_provider_registry(settings).get(args.provider)
    except KeyError as exc:
        raise SystemExit(f"Configured provider is unavailable: {args.provider}") from exc
    client = build_provider_client(provider, settings)

    rows = []
    base_scores: list[int] = []
    candidate_scores: list[int] = []
    base_ok_count = 0
    candidate_ok_count = 0
    for case in cases:
        base = run_variant(client, case, BASELINE_PROMPT)
        response_policy = classify_response_policy(case.prompt)
        candidate_budget = candidate_guide.budget(case.prompt, "concise", max_chars=2_500)
        candidate = run_variant(
            client,
            case,
            candidate_guide.render("concise", policy_instruction=response_policy.instructions),
            response_policy=response_policy,
            max_output_chars=candidate_budget.max_chars,
        )
        base_scores.append(base.score)
        candidate_scores.append(candidate.score)
        base_ok_count += int(base.ok)
        candidate_ok_count += int(candidate.ok)
        rows.append({"id": case.id, "prompt": case.prompt, "base": asdict(base), "candidate": asdict(candidate)})

    base_average = round(sum(base_scores) / len(base_scores)) if base_scores else 0
    candidate_average = round(sum(candidate_scores) / len(candidate_scores)) if candidate_scores else 0
    winner = choose_winner(base_score=base_average, candidate_score=candidate_average)
    if winner == "candidate" and candidate_ok_count < base_ok_count:
        winner = "no_change"
    promotion_eligible = winner == "candidate" and is_promotion_eligible(
        average_score=candidate_average,
        passed=candidate_ok_count,
        total=len(cases),
    )
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider.name,
        "model": provider.model,
        "case_count": len(cases),
        "base": {"average_score": base_average, "passed": base_ok_count},
        "candidate": {"path": str(args.candidate), "average_score": candidate_average, "passed": candidate_ok_count},
        "winner": winner,
        "promotion_eligible": promotion_eligible,
        "rows": rows,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"style_ab provider={provider.name} cases={len(cases)} base={base_average}/{base_ok_count} "
        f"candidate={candidate_average}/{candidate_ok_count} winner={winner} "
        f"promotion_eligible={promotion_eligible} report={report_path}"
    )
    return 0 if promotion_eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())
