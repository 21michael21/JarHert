from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class DailyLimitStore:
    per_user_limit: int = 20
    global_limit: int = 200
    _user_counts: dict[tuple[int, date], int] = field(default_factory=dict)
    _global_counts: dict[date, int] = field(default_factory=dict)

    def is_unlimited(self) -> bool:
        return self.per_user_limit <= 0 and self.global_limit <= 0

    def can_consume(self, user_id: int, *, today: date | None = None) -> bool:
        if self.is_unlimited():
            return True
        day = today or date.today()
        user_count = self._user_counts.get((user_id, day), 0)
        global_count = self._global_counts.get(day, 0)
        user_ok = self.per_user_limit <= 0 or user_count < self.per_user_limit
        global_ok = self.global_limit <= 0 or global_count < self.global_limit
        return user_ok and global_ok

    def consume(self, user_id: int, *, today: date | None = None) -> bool:
        day = today or date.today()
        if not self.can_consume(user_id, today=day):
            return False
        self._user_counts[(user_id, day)] = self._user_counts.get((user_id, day), 0) + 1
        self._global_counts[day] = self._global_counts.get(day, 0) + 1
        return True

    def remaining_for_user(self, user_id: int, *, today: date | None = None) -> int:
        if self.per_user_limit <= 0:
            return 2_147_483_647
        day = today or date.today()
        used = self._user_counts.get((user_id, day), 0)
        return max(0, self.per_user_limit - used)
