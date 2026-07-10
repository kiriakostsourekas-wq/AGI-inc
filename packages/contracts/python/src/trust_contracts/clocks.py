"""Separate clocks for fictional scenario time and security deadlines."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


def require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock values must be timezone-aware")
    return value


class ScenarioClock(Protocol):
    """Clock used for fictional travel dates and seeded application state."""

    def now(self) -> datetime: ...


class SecurityClock(Protocol):
    """Clock used for grants, sessions, retention, and audit timestamps."""

    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FixedScenarioClock:
    value: datetime

    def __post_init__(self) -> None:
        require_aware(self.value)

    def now(self) -> datetime:
        return self.value


@dataclass(frozen=True, slots=True)
class SystemSecurityClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(slots=True)
class FrozenSecurityClock:
    """Mutable test clock that never consults scenario time."""

    value: datetime

    def __post_init__(self) -> None:
        require_aware(self.value)

    def now(self) -> datetime:
        return self.value

    def advance(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("security clock cannot move backwards")
        self.value += delta
