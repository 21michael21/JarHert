from __future__ import annotations

from datetime import datetime, timezone

from hermes.native_tools.personal_productivity import PersonalProductivityStore


def test_stale_sending_reminder_is_claimed_again(tmp_path) -> None:
    store = PersonalProductivityStore(tmp_path / "personal-os.sqlite3")
    reminder = store.create_reminder(
        text="Позвонить врачу",
        remind_at="2030-01-01T12:00:00+00:00",
        idempotency_key="stale-sending",
    )
    first = store.claim_due_reminders(now=datetime(2030, 1, 1, 12, 1, tzinfo=timezone.utc))
    assert [item.id for item in first] == [reminder.id]
    # Диспетчер умер после claim: свежий (в тестовой шкале) sending не сбрасывается.
    with store._connect() as connection:
        connection.execute(
            "UPDATE personal_reminders SET updated_at = '2030-01-01 12:01:00' WHERE id = ?",
            (reminder.id,),
        )
        connection.commit()
    assert store.claim_due_reminders(now=datetime(2030, 1, 1, 12, 2, tzinfo=timezone.utc)) == []
    # ...а через десять минут зависшее sending восстанавливается и уходит снова.
    with store._connect() as connection:
        connection.execute(
            "UPDATE personal_reminders SET updated_at = '2030-01-01 11:50:00' WHERE id = ?",
            (reminder.id,),
        )
        connection.commit()

    recovered = store.claim_due_reminders(now=datetime(2030, 1, 1, 12, 3, tzinfo=timezone.utc))

    assert [item.id for item in recovered] == [reminder.id]
