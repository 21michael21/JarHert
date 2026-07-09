from __future__ import annotations

from assistant.hermes_client import FakeHermesClient
from assistant.ideas import InMemoryIdeaStore
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext


def user() -> UserContext:
    return UserContext(user_id=1, tg_user_id=1001)


def test_pipeline_records_parse_llm_and_total_timings() -> None:
    pipeline = AssistantPipeline(FakeHermesClient(), DailyLimitStore())

    reply = pipeline.handle_text(user(), "/ask привет")

    assert reply.perf_ms["intent_parse_ms"] >= 0
    assert reply.perf_ms["llm_ms"] >= 0
    assert reply.perf_ms["total_response_ms"] >= 0


def test_pipeline_records_route_and_tool_timings_for_natural_action() -> None:
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        plain_text_ai_enabled=True,
        ideas=InMemoryIdeaStore(),
    )

    reply = pipeline.handle_text(user(), "запиши идею про быструю доставку")

    assert reply.perf_ms["route_ms"] >= 0
    assert reply.perf_ms["tool_ms"] >= 0
