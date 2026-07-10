from assistant.context_store import ConversationTurn
from assistant.training_feedback import TrainingFeedbackStatus
from assistant.training_feedback_export import build_approved_feedback_records
from backend.db import init_db, make_session_factory
from backend.stores import SqlConversationStore, UserStore
from backend.training_feedback_store import SqlTrainingFeedbackStore


def _factory(tmp_path):
    factory = make_session_factory(f"sqlite:///{tmp_path / 'feedback.sqlite3'}")
    init_db(factory)
    return factory


def _turn(factory, *, user_id: int) -> ConversationTurn:
    return SqlConversationStore(factory).add(
        user_id=user_id,
        user_text="Меня зовут Антон, напиши на anton@example.com",
        assistant_text="Используй token=private-value и https://example.com",
    )


def test_explicit_normal_feedback_stores_only_redacted_approved_pair(tmp_path) -> None:
    factory = _factory(tmp_path)
    user = UserStore(factory).get_or_create(1001)
    store = SqlTrainingFeedbackStore(factory)

    example = store.approve_turn(user.id, _turn(factory, user_id=user.id))

    assert example.status is TrainingFeedbackStatus.APPROVED
    assert example.user_text == "[PERSON_NAME], напиши на [EMAIL]"
    assert example.assistant_text == "Используй [CREDENTIAL] и [URL]"
    assert "Антон" not in str(example)
    assert "private-value" not in str(example)
    assert store.list_approved(user.id) == [example]


def test_corrected_reply_is_stored_only_after_explicit_edit_flow(tmp_path) -> None:
    factory = _factory(tmp_path)
    user = UserStore(factory).get_or_create(1002)
    store = SqlTrainingFeedbackStore(factory)
    source = _turn(factory, user_id=user.id)

    pending = store.begin_edit(user.id, source)
    approved = store.consume_pending_edit(user.id, "Готовый короткий ответ. Пиши на +7 999 123-45-67")

    assert pending.status is TrainingFeedbackStatus.PENDING_EDIT
    assert approved is not None
    assert approved.status is TrainingFeedbackStatus.APPROVED
    assert approved.assistant_text == "Готовый короткий ответ. Пиши на [PHONE]"
    assert store.consume_pending_edit(user.id, "второй текст") is None


def test_new_edit_replaces_old_pending_slot_for_same_user(tmp_path) -> None:
    factory = _factory(tmp_path)
    user = UserStore(factory).get_or_create(1009)
    turns = SqlConversationStore(factory)
    store = SqlTrainingFeedbackStore(factory)
    first = turns.add(user_id=user.id, user_text="первый вопрос", assistant_text="первый ответ")
    second = turns.add(user_id=user.id, user_text="второй вопрос", assistant_text="второй ответ")

    store.begin_edit(user.id, first)
    store.begin_edit(user.id, second)
    approved = store.consume_pending_edit(user.id, "исправленный второй ответ")

    assert approved is not None
    assert approved.conversation_turn_id == second.id
    assert approved.assistant_text == "исправленный второй ответ"


def test_conversation_turn_store_returns_owned_turn_only(tmp_path) -> None:
    factory = _factory(tmp_path)
    users = UserStore(factory)
    first = users.get_or_create(1003)
    second = users.get_or_create(1004)
    turns = SqlConversationStore(factory)
    saved = turns.add(user_id=first.id, user_text="вопрос", assistant_text="ответ")

    assert turns.get_for_user(first.id, saved.id) == saved
    assert turns.get_for_user(second.id, saved.id) is None


def test_export_builds_records_from_approved_feedback_only(tmp_path) -> None:
    factory = _factory(tmp_path)
    user = UserStore(factory).get_or_create(1008)
    store = SqlTrainingFeedbackStore(factory)
    approved = store.approve_turn(user.id, _turn(factory, user_id=user.id))
    store.begin_edit(
        user.id,
        SqlConversationStore(factory).add(user_id=user.id, user_text="другой вопрос", assistant_text="другой ответ"),
    )

    records = build_approved_feedback_records(system_prompt="Пиши ясно.", examples=[approved, *store.list_approved(user.id)])

    assert len(records) == 1
    assert all(record["metadata"]["source"] == "explicit_telegram_feedback" for record in records)
    assert "anton@example.com" not in str(records)
