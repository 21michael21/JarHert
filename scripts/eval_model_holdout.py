from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistant.communication_style import constrain_response_length, load_communication_style
from assistant.model_holdout import (
    VariantSample,
    action_router_safety_contract_failures,
    evaluate_candidate_gate,
    load_holdout_cases,
    score_holdout_answer,
    summarize_variant,
)
from assistant.provider_clients import OpenAIResponsesClient
from assistant.response_policy import classify_response_policy
from assistant.types import HermesRequest, UserContext
from backend.config import Settings


BASE_SYSTEM_PROMPT = (
    "Ты русскоязычный личный помощник. Отвечай по делу, кратко и без выдуманных фактов. "
    "Соблюдай прямое требование пользователя к длине ответа."
)
DEFAULT_HOLDOUT = PROJECT_ROOT / "dev" / "model_holdout" / "holdout.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports" / "model_holdout"


def run_variant(
    *,
    name: str,
    model: str,
    client,
    cases,
    system_prompt: str,
    apply_runtime_style: bool,
    style_guide=None,
    progress_every: int = 0,
) -> tuple[object, list[dict]]:
    samples: list[VariantSample] = []
    rows: list[dict] = []
    for index, case in enumerate(cases, start=1):
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
            if apply_runtime_style and style_guide is not None:
                policy = classify_response_policy(case.prompt)
                budget = style_guide.budget(case.prompt, "concise", max_chars=case.max_chars)
                answer = policy.normalize(constrain_response_length(answer, max_chars=budget.max_chars))
            latency_ms = response.latency_ms or round((time.perf_counter() - started) * 1000)
            score = score_holdout_answer(case, answer)
            sample = VariantSample(case.id, latency_ms, score, answer)
        except Exception as exc:
            latency_ms = round((time.perf_counter() - started) * 1000)
            score = score_holdout_answer(case, "")
            sample = VariantSample(case.id, latency_ms, score, "", f"{type(exc).__name__}: {exc}")
        samples.append(sample)
        rows.append(
            {
                "id": case.id,
                "category": case.category,
                "latency_ms": sample.latency_ms,
                "quality_score": sample.score.quality_score,
                "short": sample.score.short,
                "unnecessary_question": sample.score.unnecessary_question,
                "factual_violation": sample.score.factual_violation,
                "issues": list(sample.score.issues),
                "error": sample.error,
            }
        )
        if progress_every and (index % progress_every == 0 or index == len(cases)):
            print(f"model_holdout variant={name} progress={index}/{len(cases)}", flush=True)
    return summarize_variant(name, model, samples), rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the closed 100-case model comparison gate.")
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--base-model", default="gpt-5-nano")
    parser.add_argument("--fine-tuned-model", default=os.getenv("FINE_TUNED_MODEL", ""))
    parser.add_argument("--max-cases", type=int, default=0, help="Run a short smoke sample without opening the gate.")
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--gate", action="store_true", help="Exit non-zero unless a candidate wins every threshold.")
    args = parser.parse_args()

    if not args.holdout.is_file():
        raise SystemExit(f"Closed holdout is missing: {args.holdout}. Run scripts/generate_model_holdout.py first.")
    settings = Settings()
    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is required for a real model holdout comparison")
    cases = load_holdout_cases(args.holdout)
    if args.max_cases:
        cases = cases[: args.max_cases]
    if args.gate and len(cases) != 100:
        raise SystemExit("--gate requires all 100 holdout cases")
    base_client = OpenAIResponsesClient(
        api_key=settings.openai_api_key,
        model=args.base_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.ai_provider_deadline_seconds,
        max_output_tokens=settings.openai_max_output_tokens,
    )
    base, base_rows = run_variant(
        name="base",
        model=args.base_model,
        client=base_client,
        cases=cases,
        system_prompt=BASE_SYSTEM_PROMPT,
        apply_runtime_style=False,
        progress_every=args.progress_every,
    )
    style_guide = load_communication_style(enabled=True, path=settings.ai_style_prompt_path)
    styled, styled_rows = run_variant(
        name="style_profile",
        model=args.base_model,
        client=base_client,
        cases=cases,
        system_prompt=style_guide.render("concise"),
        apply_runtime_style=True,
        style_guide=style_guide,
        progress_every=args.progress_every,
    )
    contracts = action_router_safety_contract_failures()
    variants = [(styled, styled_rows)]
    if args.fine_tuned_model.strip():
        fine_client = OpenAIResponsesClient(
            api_key=settings.openai_api_key,
            model=args.fine_tuned_model.strip(),
            base_url=settings.openai_base_url,
            timeout_seconds=settings.ai_provider_deadline_seconds,
            max_output_tokens=settings.openai_max_output_tokens,
        )
        fine, fine_rows = run_variant(
            name="fine_tuned",
            model=args.fine_tuned_model.strip(),
            client=fine_client,
            cases=cases,
            system_prompt=BASE_SYSTEM_PROMPT,
            apply_runtime_style=False,
            progress_every=args.progress_every,
        )
        variants.append((fine, fine_rows))

    candidate_reports = []
    for summary, rows in variants:
        decision = evaluate_candidate_gate(base, summary, action_router_safety_failures=len(contracts))
        candidate_reports.append({"summary": asdict(summary), "gate": asdict(decision), "rows": rows})
    winners = [item for item in candidate_reports if item["gate"]["passed"]]
    winner = max(winners, key=lambda item: (item["summary"]["quality_score"], -item["summary"]["p50_latency_ms"])) if winners else None
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "holdout": str(args.holdout),
        "case_count": len(cases),
        "base": {"summary": asdict(base), "rows": base_rows},
        "candidates": candidate_reports,
        "action_router_safety_failures": contracts,
        "fine_tuned": "configured" if args.fine_tuned_model.strip() else "skipped_not_configured",
        "winner": winner["summary"]["name"] if winner else "no_winner",
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.report_dir / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"model_holdout cases={len(cases)} base={base.quality_score}/{base.p50_latency_ms}ms "
        f"winner={payload['winner']} report={report_path}"
    )
    return 0 if not args.gate or winner is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
