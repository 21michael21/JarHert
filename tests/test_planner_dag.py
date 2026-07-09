from __future__ import annotations

from assistant.action_queue import ActionStatus, InMemoryActionQueueStore
from assistant.action_schema import ActionType
from assistant.agent_jobs import InMemoryAgentJobStore
from assistant.planner_dag import PlanNode, PlannerDag


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
    assert planner.resume(user_id=1, job_id=job.id).status == "queued"
    failed = queue.claim_next()
    queue.mark_failed(failed.id, "calendar failed")
    candidates = planner.compensation_candidates(job_id=job.id, failed_action_id=failed.id)

    assert candidates[0].compensation_status == "available"
    assert candidates[0].result_meta == {"trello_card_id": "abc"}

    cancelled = planner.cancel(user_id=1, job_id=job.id)
    assert cancelled.status == "cancelled"
