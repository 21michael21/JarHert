from backend.db import init_db, make_session_factory
from backend.stores import EventStore, SqlConversationStore, UserStore
from backend.training_feedback_store import SqlTrainingFeedbackStore
from assistant.hermes_client import FakeHermesClient
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from gateway_bot.service import GatewayService
from gateway_bot.telegram_callbacks import handle_callback_data


def _factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'feedback.sqlite3'}")
    init_db(factory)
    return factory


def test_feedback_buttons_store_only_explicitly_approved_reply(tmp_path) -> None:
    factory = _factory(tmp_path)
    feedback = SqlTrainingFeedbackStore(factory)
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            plain_text_ai_enabled=True,
            conversation_turns=SqlConversationStore(factory),
        ),
        users=UserStore(factory),
        events=EventStore(factory),
        training_feedback=feedback,
    )

    reply = service.handle_text(1005, "объясни MVP")

    assert reply.conversation_turn_id is not None
    assert [button.text for row in reply.buttons for button in row] == [
        "Нормально",
        "Сделай короче",
        "Я исправил сам",
    ]
    saved = handle_callback_data(service, 1005, f"ai:feedback_ok:{reply.conversation_turn_id}")

    assert "Сохранил" in saved.text
    assert len(feedback.list_approved(1)) == 1


def test_other_user_cannot_approve_known_conversation_turn(tmp_path) -> None:
    factory = _factory(tmp_path)
    feedback = SqlTrainingFeedbackStore(factory)
    service = GatewayService(
        pipeline=AssistantPipeline(
            FakeHermesClient(),
            DailyLimitStore(),
            plain_text_ai_enabled=True,
            conversation_turns=SqlConversationStore(factory),
        ),
        users=UserStore(factory),
        training_feedback=feedback,
    )
    reply = service.handle_text(1010, "объясни MVP")

    denied = handle_callback_data(service, 1011, f"ai:feedback_ok:{reply.conversation_turn_id}")

    assert denied.blocked_reason == "training_turn_not_found"
    assert feedback.list_approved(1) == []
    assert feedback.list_approved(2) == []


def test_corrected_reply_is_captured_instead_of_sent_to_ai(tmp_path) -> None:
    factory = _factory(tmp_path)
    hermes = FakeHermesClient()
    feedback = SqlTrainingFeedbackStore(factory)
    service = GatewayService(
        pipeline=AssistantPipeline(
            hermes,
            DailyLimitStore(),
            plain_text_ai_enabled=True,
            conversation_turns=SqlConversationStore(factory),
        ),
        users=UserStore(factory),
        training_feedback=feedback,
    )
    reply = service.handle_text(1006, "объясни MVP")

    pending = handle_callback_data(service, 1006, f"ai:feedback_edit:{reply.conversation_turn_id}")
    captured = service.handle_text(1006, "Сначала проверь спрос, потом делай MVP.")

    assert "Пришли следующей репликой" in pending.text
    assert "Сохранил согласованную пару" in captured.text
    assert len(hermes.requests) == 1
    assert feedback.list_approved(1)[0].assistant_text == "Сначала проверь спрос, потом делай MVP."


def test_shorten_button_creates_new_candidate_without_auto_approval(tmp_path) -> None:
    factory = _factory(tmp_path)
    hermes = FakeHermesClient()
    feedback = SqlTrainingFeedbackStore(factory)
    service = GatewayService(
        pipeline=AssistantPipeline(
            hermes,
            DailyLimitStore(),
            plain_text_ai_enabled=True,
            conversation_turns=SqlConversationStore(factory),
        ),
        users=UserStore(factory),
        training_feedback=feedback,
    )
    original = service.handle_text(1007, "объясни MVP")

    shorter = handle_callback_data(service, 1007, f"ai:feedback_shorter:{original.conversation_turn_id}")

    assert shorter.conversation_turn_id != original.conversation_turn_id
    assert "Нормально" in str(shorter.buttons)
    assert feedback.list_approved(1) == []
    assert len(hermes.requests) == 2
