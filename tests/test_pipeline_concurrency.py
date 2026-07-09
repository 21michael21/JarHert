from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

from assistant.hermes_client import FakeHermesClient
from assistant.ideas import InMemoryIdeaStore
from assistant.limits import DailyLimitStore
from assistant.pipeline import AssistantPipeline
from assistant.types import UserContext


class BlockingIdeaStore(InMemoryIdeaStore):
    def __init__(self, barrier: Barrier) -> None:
        super().__init__()
        self._barrier = barrier

    def add(self, user_id: int, text: str):
        self._barrier.wait(timeout=2)
        return super().add(user_id, text)


class RecordingConversationStore:
    def __init__(self) -> None:
        self._items: list[dict] = []
        self._lock = Lock()

    def add(
        self,
        *,
        user_id: int,
        user_text: str,
        assistant_text: str,
        extracted_actions: list[dict] | None = None,
    ) -> None:
        with self._lock:
            self._items.append(
                {
                    "user_id": user_id,
                    "user_text": user_text,
                    "assistant_text": assistant_text,
                    "extracted_actions": list(extracted_actions or []),
                }
            )

    def latest_user_text(self, _user_id: int) -> None:
        return None

    def by_text(self) -> dict[str, dict]:
        with self._lock:
            return {item["user_text"]: item for item in self._items}


def test_parallel_requests_keep_trace_perf_and_actions_request_scoped() -> None:
    conversations = RecordingConversationStore()
    pipeline = AssistantPipeline(
        FakeHermesClient(),
        DailyLimitStore(),
        ideas=BlockingIdeaStore(Barrier(2)),
        conversation_turns=conversations,
    )
    first_text = "запиши идею альфа и напомни через 1 час проверить альфу"
    second_text = "запиши идею бета и напомни через 2 часа проверить бету"

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(
            pipeline.handle_text,
            UserContext(user_id=1, tg_user_id=1001),
            first_text,
        )
        second_future = executor.submit(
            pipeline.handle_text,
            UserContext(user_id=2, tg_user_id=1002),
            second_text,
        )
        first_reply = first_future.result(timeout=3)
        second_reply = second_future.result(timeout=3)

    turns = conversations.by_text()

    assert first_reply.trace_id
    assert second_reply.trace_id
    assert first_reply.trace_id != second_reply.trace_id
    assert turns[first_text]["extracted_actions"][0]["payload"]["text"] == "альфа"
    assert turns[second_text]["extracted_actions"][0]["payload"]["text"] == "бета"
    assert "total_response_ms" in first_reply.perf_ms
    assert "total_response_ms" in second_reply.perf_ms
