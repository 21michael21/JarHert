from __future__ import annotations

from assistant.communication_style import CommunicationStyleGuide, constrain_response_length
from assistant.provider_clients import HermesClient
from assistant.perf import PerfRecorder
from assistant.quality_gates import check_output, check_output_safety
from assistant.response_policy import classify_response_policy
from assistant.response_composer import ResponseComposer
from assistant.types import AssistantReply, GateStatus, HermesRequest, Intent, UserContext


def answer_with_ai(
    *,
    hermes: HermesClient,
    responses: ResponseComposer,
    user: UserContext,
    prompt: str,
    intent: Intent,
    style: str,
    communication_style: CommunicationStyleGuide,
    max_output_chars: int,
    perf: PerfRecorder,
    trace_id: str = "",
    events=None,
) -> AssistantReply:
    response_policy = classify_response_policy(prompt)
    system_prompt = communication_style.render(style, policy_instruction=response_policy.instructions)
    response_budget = communication_style.budget(prompt, style, max_chars=max_output_chars)
    try:
        with perf.track("llm"):
            hermes_response = hermes.ask(
                HermesRequest(
                    user=user,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    max_output_tokens=response_budget.max_output_tokens,
                    intent=intent,
                    context={
                        "style": style,
                        "style_profile": communication_style.version,
                        "response_budget_chars": str(response_budget.max_chars),
                    },
                    trace_id=trace_id,
                )
            )
    except Exception:
        return responses.provider_unavailable(intent=intent)

    output_gate = check_output_safety(hermes_response.text)
    if output_gate.status == GateStatus.NEEDS_FALLBACK:
        return responses.provider_fallback(
            reason=output_gate.reason,
            intent=intent,
            provider=hermes_response.provider,
            model=hermes_response.model,
            fallback_count=hermes_response.fallback_count,
        )

    constrained_text = constrain_response_length(
        output_gate.safe_text,
        max_chars=response_budget.max_chars,
    )
    constrained_text = response_policy.normalize(constrained_text)
    output_gate = check_output(constrained_text, max_chars=response_budget.max_chars)
    if output_gate.status == GateStatus.NEEDS_FALLBACK:
        return responses.provider_fallback(
            reason=output_gate.reason,
            intent=intent,
            provider=hermes_response.provider,
            model=hermes_response.model,
            fallback_count=hermes_response.fallback_count,
        )

    if events is not None and hermes_response.fallback_count:
        events.log(
            user.user_id,
            "provider_fallback",
            {
                "provider": hermes_response.provider,
                "model": hermes_response.model,
                "fallback_count": hermes_response.fallback_count,
                "fallback_reason": hermes_response.fallback_reason,
            },
            trace_id=trace_id,
        )

    return AssistantReply(
        text=output_gate.safe_text,
        intent=intent,
        provider=hermes_response.provider,
        model=hermes_response.model,
        fallback_count=hermes_response.fallback_count,
        trace_id=trace_id,
    )
