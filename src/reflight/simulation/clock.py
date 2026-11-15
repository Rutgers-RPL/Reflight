"""One instruction-derived clock, with exact periods and stable sample ordering."""

from fractions import Fraction
import heapq
from typing import Callable


def _fraction(value: float | int | Fraction) -> Fraction:
    return value if isinstance(value, Fraction) else Fraction(str(value))


class VirtualClock:
    def __init__(self, instructions_per_second: float, end_s: float,
                 end_reason: str = "duration") -> None:
        self.instructions_per_second = _fraction(instructions_per_second)
        self.end = _fraction(end_s)
        if self.instructions_per_second <= 0 or self.end < 0:
            raise ValueError("clock requires positive instruction rate and nonnegative endpoint")
        self.end_reason = end_reason
        self.now = Fraction(0)
        self._events: list[tuple[Fraction, str, int]] = []
        self._sensors: dict[str, tuple[Fraction, Callable[[float], None]]] = {}

    @property
    def time_s(self) -> float:
        return float(self.now)

    @property
    def finished(self) -> bool:
        return self.now == self.end

    def schedule(self, endpoint: str, rate_hz: int, update: Callable[[float], None]) -> None:
        if self.now or endpoint in self._sensors or rate_hz <= 0:
            raise ValueError("schedule unique sensors at time zero with positive rates")
        self._sensors[endpoint] = (Fraction(1, rate_hz), update)
        heapq.heappush(self._events, (Fraction(0), endpoint, 0))

    def advance(self, time_s: float | Fraction) -> None:
        target = min(_fraction(time_s), self.end)
        if target < self.now:
            raise ValueError("virtual time must be monotonic")
        while self._events and self._events[0][0] <= target:
            due, endpoint, index = heapq.heappop(self._events)
            self.now = due
            period, update = self._sensors[endpoint]
            update(float(due))
            heapq.heappush(self._events, ((index + 1) * period, endpoint, index + 1))
        self.now = target

    def before_instruction(self, completed: int) -> None:
        self.advance(Fraction(completed) / self.instructions_per_second)
