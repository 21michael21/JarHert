from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from assistant.provider_policy import BudgetLedgerEntry, BudgetLedgerSummary
from backend.models import ProviderBudgetDailyRecord, ProviderBudgetEntryRecord


class SqlProviderBudgetLedger:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def reserve(
        self,
        *,
        provider_name: str,
        estimated_cost_micro_usd: int,
        daily_budget_micro_usd: int,
        now: datetime | None = None,
    ) -> bool:
        day = (now or datetime.now(timezone.utc)).date().isoformat()
        cost = max(0, estimated_cost_micro_usd)
        budget = max(0, daily_budget_micro_usd)
        self._ensure_day(day)
        with self.session_factory() as db:
            reserved = db.execute(
                update(ProviderBudgetDailyRecord)
                .where(
                    ProviderBudgetDailyRecord.day == day,
                    ProviderBudgetDailyRecord.estimated_cost_micro_usd + cost <= budget,
                )
                .values(
                    estimated_cost_micro_usd=ProviderBudgetDailyRecord.estimated_cost_micro_usd + cost,
                    request_count=ProviderBudgetDailyRecord.request_count + 1,
                )
            )
            if reserved.rowcount != 1:
                db.rollback()
                return False
            db.add(
                ProviderBudgetEntryRecord(
                    day=day,
                    provider_name=provider_name,
                    estimated_cost_micro_usd=cost,
                )
            )
            db.commit()
            return True

    def summary(self, *, now: datetime | None = None) -> BudgetLedgerSummary:
        day = (now or datetime.now(timezone.utc)).date()
        with self.session_factory() as db:
            record = db.scalar(select(ProviderBudgetDailyRecord).where(ProviderBudgetDailyRecord.day == day.isoformat()))
            if record is None:
                return BudgetLedgerSummary(day=day)
            return BudgetLedgerSummary(
                day=day,
                estimated_cost_micro_usd=record.estimated_cost_micro_usd,
                request_count=record.request_count,
            )

    def entries(self, *, now: datetime | None = None) -> list[BudgetLedgerEntry]:
        day = (now or datetime.now(timezone.utc)).date()
        with self.session_factory() as db:
            records = db.scalars(
                select(ProviderBudgetEntryRecord)
                .where(ProviderBudgetEntryRecord.day == day.isoformat())
                .order_by(ProviderBudgetEntryRecord.id.asc())
            ).all()
            return [
                BudgetLedgerEntry(
                    day=day,
                    provider_name=record.provider_name,
                    estimated_cost_micro_usd=record.estimated_cost_micro_usd,
                )
                for record in records
            ]

    def _ensure_day(self, day: str) -> None:
        for _ in range(2):
            with self.session_factory() as db:
                if db.scalar(select(ProviderBudgetDailyRecord.id).where(ProviderBudgetDailyRecord.day == day)) is not None:
                    return
                db.add(ProviderBudgetDailyRecord(day=day))
                try:
                    db.commit()
                    return
                except IntegrityError:
                    db.rollback()
        raise RuntimeError(f"Could not initialize provider budget ledger for {day}")
