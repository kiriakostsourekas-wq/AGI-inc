"""Server-enforced public live-run concurrency and hourly quotas."""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock

from trust_contracts import SecurityClock

from .errors import QuotaExceededError


@dataclass(slots=True)
class PublicQuotaGuard:
    clock: SecurityClock
    maximum_concurrent: int
    maximum_per_ip_per_hour: int
    _active: int = 0
    _starts: dict[str, deque[datetime]] = field(default_factory=lambda: defaultdict(deque))
    _lock: RLock = field(default_factory=RLock)

    def reserve(self, client_ip: str) -> None:
        now = self.clock.now()
        cutoff = now - timedelta(hours=1)
        with self._lock:
            starts = self._starts[client_ip]
            while starts and starts[0] <= cutoff:
                starts.popleft()
            if self._active >= self.maximum_concurrent:
                raise QuotaExceededError(
                    "live-run capacity is full; use a recorded replay or retry later"
                )
            if len(starts) >= self.maximum_per_ip_per_hour:
                raise QuotaExceededError("hourly live-run quota exceeded for this client")
            starts.append(now)
            self._active += 1

    def release(self) -> None:
        with self._lock:
            self._active = max(0, self._active - 1)

    @property
    def active(self) -> int:
        with self._lock:
            return self._active
