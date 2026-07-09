from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator


@dataclass
class PerfRecorder:
    _clock: Callable[[], float] = time.perf_counter
    _seconds: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))

    @contextmanager
    def track(self, name: str) -> Iterator[None]:
        started = self._clock()
        try:
            yield
        finally:
            self._seconds[name] += max(0.0, self._clock() - started)

    def snapshot_ms(self) -> dict[str, int]:
        return {f"{name}_ms": round(value * 1000) for name, value in sorted(self._seconds.items())}


class NullPerfRecorder:
    @contextmanager
    def track(self, _name: str) -> Iterator[None]:
        yield

    def snapshot_ms(self) -> dict[str, int]:
        return {}
