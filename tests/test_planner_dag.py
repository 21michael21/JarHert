from __future__ import annotations

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.action_schema import ActionType
from assistant.agent_jobs import InMemoryAgentJobStore
from assistant.planner_dag import PlanNode, PlannerDag
from backend.db import init_db, make_session_factory
from backend.queue_store import SqlActionQueueStore, SqlAgentJobStore
from backend.user_store import UserStore


def test_planner_dag_creates_dependencies_and_checkpoints() -> None:
    jobs = InMemoryAgentJobStore()
    queue = InMemoryActionQueueStore()
    planner = PlannerDag(jobs=jobs, actions=queue)

    job = planner.create_plan(
        user_id=1,
        goal="длинный план",
        nodes=[
            PlanNode("a", ActionType.IDEA_SAVE, {"text": "сначала"}),
            PlanNode("b", ActionType.TASK_CREATE, {"title": "потом"}, depends_on=("a",)),
        ],
    )

    actions = sorted(queue.list_for_user(1), key=lambda item: item.id)
    assert job.steps == ["a: idea.save", "b: task.create"]
    assert actions[1].depends_on_action_id == actions[0].id

    claimed = queue.claim_next()
    queue.mark_succeeded(claimed.id, result_meta={"note_id": "n1"}, result_text="ok")
    checkpoints = planner.checkpoints(user_id=1, job_id=job.id)

    assert checkpoints == [{"action_id": claimed.id, "type": "idea.save", "result_meta": {"note_id": "n1"}, "result_text": "ok"}]


def test_planner_dag_join_waits_for_every_parent() -> None:
    jobs = InMemoryAgentJobStore()
    queue = InMemoryActionQueueStore()
    planner = PlannerDag(jobs=jobs, actions=queue)
    planner.create_plan(
        user_id=1,
        goal="две проверки перед итогом",
        nodes=[
            PlanNode("first", ActionType.IDEA_SAVE, {"text": "первый"}),
            PlanNode("second", ActionType.IDEA_SAVE, {"text": "второй"}),
            PlanNode("join", ActionType.TASK_CREATE, {"title": "итог"}, depends_on=("first", "second")),
        ],
    )
    first, second, join = sorted(queue.list_for_user(1), key=lambda item: item.id)

    assert join.depends_on_action_ids == (first.id, second.id)

    claimed_first = queue.claim_next()
    assert claimed_first.id == first.id
    queue.mark_succeeded(claimed_first.id)
    claimed_second = queue.claim_next()
    assert claimed_second.id == second.id
    queue.mark_succeeded(claimed_second.id)
    assert queue.claim_next().id == join.id


def test_sql_planner_dag_join_persists_every_parent(tmp_path) -> None:
    factory = make_session_factory(f"sqlite:///{tmp_path / 'planner.sqlite3'}")
    init_db(factory)
    user = UserStore(factory).get_or_create(5001)
    actions = SqlActionQueueStore(factory)
    planner = PlannerDag(jobs=SqlAgentJobStore(factory), actions=actions)
    planner.create_plan(
        user_id=user.id,
        goal="sql join",
        nodes=[
            PlanNode("first", ActionType.IDEA_SAVE, {"text": "первый"}),
            PlanNode("second", ActionType.IDEA_SAVE, {"text": "второй"}),
            PlanNode("join", ActionType.TASK_CREATE, {"title": "итог"}, depends_on=("first", "second")),
        ],
    )
    first, second, join = sorted(actions.list_for_user(user.id, limit=10), key=lambda item: item.id)

    assert join.depends_on_action_ids == (first.id, second.id)
    claimed_first = actions.claim_next(worker_id="worker")
    actions.mark_succeeded(claimed_first.id, worker_id="worker")
    claimed_second = actions.claim_next(worker_id="worker")
    actions.mark_succeeded(claimed_second.id, worker_id="worker")
    assert actions.claim_next(worker_id="worker").id == join.id


def test_planner_dag_pause_resume_cancel_and_compensation_candidates() -> None:
    jobs = InMemoryAgentJobStore()
    queue = InMemoryActionQueueStore()
    planner = PlannerDag(jobs=jobs, actions=queue)
    job = planner.create_plan(
        user_id=1,
        goal="план с rollback",
        nodes=[
            PlanNode("card", ActionType.TASK_CREATE, {"title": "создать"}),
            PlanNode("event", ActionType.CALENDAR_CREATE, {"title": "созвон", "start": "tomorrow 10:00", "end": "tomorrow 10:30"}, depends_on=("card",)),
        ],
    )
    first = queue.claim_next()
    queue.mark_succeeded(first.id, result_meta={"trello_card_id": "abc"})

    assert planner.pause(user_id=1, job_id=job.id).status == "paused"
    assert queue.claim_next() is None
    assert planner.resume(user_id=1, job_id=job.id).status == "queued"
    failed = queue.claim_next()
    queue.mark_failed(failed.id, "calendar failed")
    candidates = planner.compensation_candidates(job_id=job.id, failed_action_id=failed.id)

    assert candidates[0].compensation_status == "available"
    assert candidates[0].result_meta == {"trello_card_id": "abc"}

    cancelled = planner.cancel(user_id=1, job_id=job.id)
    assert cancelled.status == "cancelled"


def test_planner_dag_exposes_partial_results_while_paused() -> None:
    jobs = InMemoryAgentJobStore()
    queue = InMemoryActionQueueStore()
    planner = PlannerDag(jobs=jobs, actions=queue)
    job = planner.create_plan(
        user_id=1,
        goal="план с checkpoint",
        nodes=[
            PlanNode("note", ActionType.IDEA_SAVE, {"text": "сохранить"}),
            PlanNode("task", ActionType.TASK_CREATE, {"title": "создать"}, depends_on=("note",)),
        ],
    )
    first = queue.claim_next()
    queue.mark_succeeded(first.id, result_meta={"note_id": "n1"}, result_text="Заметка сохранена")

    planner.pause(user_id=1, job_id=job.id)

    assert planner.partial_results(user_id=1, job_id=job.id) == ["Заметка сохранена"]
    assert queue.claim_next() is None
