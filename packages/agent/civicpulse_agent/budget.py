"""Usage controls: per-client rate limit, global daily request limit and global daily cost ceiling.

In-process state, adequate for the single-instance deployment; a multi-instance deployment would need
a shared store. The cost ceiling is enforced before each provider call using the accumulated spend
plus a worst-case estimate for the next call.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass


class BudgetExceeded(Exception):  # noqa: N818 - a limit condition, not a programming error
    def __init__(self, reason: str, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


@dataclass(frozen=True)
class Prices:
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_mtok + output_tokens * self.output_per_mtok) / 1_000_000


class UsageGovernor:
    def __init__(
        self,
        per_client_per_minute: int,
        daily_requests: int,
        daily_budget_usd: float,
        prices: Prices,
        max_clients: int = 10_000,
    ) -> None:
        self.per_minute = per_client_per_minute
        self.daily_requests = daily_requests
        self.daily_budget = daily_budget_usd
        self.prices = prices
        self.max_clients = max_clients
        self._windows: OrderedDict[str, deque[float]] = OrderedDict()
        self._day = dt.datetime.now(dt.UTC).date()
        self._requests_today = 0
        self._spent_today = 0.0
        self._lock = threading.Lock()

    def _roll(self) -> None:
        today = dt.datetime.now(dt.UTC).date()
        if today != self._day:
            self._day, self._requests_today, self._spent_today = today, 0, 0.0

    def admit(self, client: str, now: float | None = None) -> None:
        """Count one investigation for ``client``; raises BudgetExceeded when a limit is reached."""
        now = time.monotonic() if now is None else now
        with self._lock:
            self._roll()
            win = self._windows.setdefault(client, deque())
            while win and now - win[0] >= 60:
                win.popleft()
            if len(win) >= self.per_minute:
                raise BudgetExceeded("rate_limited", retry_after=max(1, int(60 - (now - win[0]) + 0.5)))
            if self._requests_today >= self.daily_requests:
                raise BudgetExceeded("daily_request_limit")
            if self.daily_budget > 0 and self._spent_today >= self.daily_budget:
                raise BudgetExceeded("daily_budget_exhausted")
            win.append(now)
            self._windows.move_to_end(client)
            while len(self._windows) > self.max_clients:
                self._windows.popitem(last=False)
            self._requests_today += 1

    def can_spend(self, input_tokens: int, max_output_tokens: int) -> bool:
        with self._lock:
            self._roll()
            if self.daily_budget <= 0:
                return True
            return self._spent_today + self.prices.cost(input_tokens, max_output_tokens) <= self.daily_budget

    def record(self, input_tokens: int, output_tokens: int) -> float:
        cost = self.prices.cost(input_tokens, output_tokens)
        with self._lock:
            self._roll()
            self._spent_today += cost
        return cost

    def snapshot(self) -> dict[str, float | int | str]:
        with self._lock:
            self._roll()
            return {
                "day_utc": self._day.isoformat(),
                "requests_today": self._requests_today,
                "daily_request_limit": self.daily_requests,
                "spent_today_usd": round(self._spent_today, 6),
                "daily_budget_usd": self.daily_budget,
            }
