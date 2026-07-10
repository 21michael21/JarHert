from __future__ import annotations

from assistant.training_data import audit_dataset_rows, build_consented_record, redact_dataset_rows, redact_training_text


def test_training_redaction_removes_sensitive_values_without_losing_request() -> None:
    text = "Напиши мне на user@example.com и используй sk-proj-" + "A" * 32

    redacted, findings = redact_training_text(text)

    assert "user@example.com" not in redacted
    assert "sk-proj-" not in redacted
    assert "[EMAIL]" in redacted
    assert "[SECRET]" in redacted
    assert findings == {"email", "secret"}


def test_training_redaction_removes_network_credentials_and_local_paths() -> None:
    text = (
        "Сервер 89.124.84.4, token=private-value, "
        "путь /Users/mihailkulibaba/Documents/private-project"
    )

    redacted, findings = redact_training_text(text)

    assert "89.124.84.4" not in redacted
    assert "private-value" not in redacted
    assert "/Users/mihailkulibaba" not in redacted
    assert "[IP_ADDRESS]" in redacted
    assert "[CREDENTIAL]" in redacted
    assert "[LOCAL_PATH]" in redacted
    assert findings == {"ip_address", "credential", "local_path"}


def test_consented_record_contains_real_user_assistant_pair() -> None:
    record = build_consented_record(
        system_prompt="Отвечай прямо.",
        user_text="Помоги составить план",
        assistant_text="Сначала выбери один результат на сегодня.",
        source_turn_id=42,
    )

    assert [message["role"] for message in record["messages"]] == ["system", "user", "assistant"]
    assert record["metadata"] == {"source": "consented_conversation_turn", "turn_id": 42}


def test_dataset_audit_reports_role_and_privacy_counts_without_raw_text() -> None:
    rows = [
        {
            "messages": [
                {"role": "system", "content": "Правила"},
                {"role": "assistant", "content": "Пиши на user@example.com"},
            ]
        },
        {
            "messages": [
                {"role": "system", "content": "Правила"},
                {"role": "user", "content": "Вопрос"},
                {"role": "assistant", "content": "Ответ"},
            ]
        },
    ]

    report = audit_dataset_rows(rows)

    assert report["rows"] == 2
    assert report["dialogue_rows"] == 1
    assert report["role_counts"] == {"system": 2, "assistant": 2, "user": 1}
    assert report["privacy_findings"] == {"email": 1}
    assert report["human_review_required"] is True
    assert "user@example.com" not in str(report)


def test_dataset_redaction_creates_clean_copy_without_mutating_source_rows() -> None:
    source = [
        {
            "messages": [
                {"role": "user", "content": "Проверь /Users/mihailkulibaba/private и token=private-value"},
                {"role": "assistant", "content": "Сделаю."},
            ]
        }
    ]

    sanitized, findings = redact_dataset_rows(source)

    assert source[0]["messages"][0]["content"].endswith("private-value")
    assert sanitized[0]["messages"][0]["content"] == "Проверь [LOCAL_PATH] и [CREDENTIAL]"
    assert findings == {"credential": 1, "local_path": 1}
    assert audit_dataset_rows(sanitized)["privacy_findings"] == {}
