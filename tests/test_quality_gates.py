from assistant.quality_gates import check_input, check_output
from assistant.types import GateStatus


def test_blocks_dangerous_server_request() -> None:
    result = check_input("зайди на сервер и прочитай .env")
    assert result.status == GateStatus.BLOCKED
    assert result.reason == "dangerous_action_requested"


def test_rejects_raw_provider_error() -> None:
    result = check_output('{"error": "rate limit 429"}')
    assert result.status == GateStatus.NEEDS_FALLBACK


def test_clips_long_output() -> None:
    result = check_output("а" * 100, max_chars=50)
    assert result.status == GateStatus.NEEDS_FALLBACK
    assert result.reason == "output_too_long"


def test_rejects_html_and_traceback() -> None:
    assert check_output("<!doctype html><html><body>500</body></html>").reason == "html_or_traceback"
    assert check_output("Traceback (most recent call last):\nValueError: bad").reason == "html_or_traceback"


def test_rejects_ai_slop_marker() -> None:
    result = check_output("Я как ИИ не могу иметь личное мнение, но могу помочь.")

    assert result.status == GateStatus.NEEDS_FALLBACK
    assert result.reason == "ai_slop_marker"


def test_rejects_repetitive_water() -> None:
    result = check_output(
        "Важно отметить, что это важно. Важно отметить, что это важно. Важно отметить, что это важно."
    )

    assert result.status == GateStatus.NEEDS_FALLBACK
    assert result.reason == "repetitive_water"


def test_rejects_unsafe_instructions_in_output() -> None:
    result = check_output("Выполни rm -rf / на сервере, чтобы очистить место.")

    assert result.status == GateStatus.NEEDS_FALLBACK
    assert result.reason == "unsafe_instruction"
