"""Deterministic resolution of relative dates against today's calendar AND the dataset's coverage.

"Last week" means the previous calendar week relative to today, not "the last week of data". When
that period is not (fully) covered by the snapshot, the resolution says so, so the answer can state
the distinction instead of silently substituting another period.
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import asdict, dataclass

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


@dataclass(frozen=True)
class ResolvedPeriod:
    phrase: str
    start: dt.date
    end: dt.date
    basis: str
    covered: str  # "full", "partial", "none"
    note: str

    def to_dict(self) -> dict[str, str]:
        d = asdict(self)
        d["start"], d["end"] = self.start.isoformat(), self.end.isoformat()
        return d


def _month_end(y: int, m: int) -> dt.date:
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def _coverage(start: dt.date, end: dt.date, first: dt.date, last: dt.date) -> tuple[str, str]:
    if end < first or start > last:
        return "none", f"No data: the snapshot covers {first}..{last} (complete days)."
    if start < first or end > last:
        eff = f"{max(start, first)}..{min(end, last)}"
        return "partial", f"Only {eff} is covered; the snapshot's last complete day is {last}."
    return "full", ""


def resolve_periods(question: str, today: dt.date, first: dt.date, last: dt.date) -> list[ResolvedPeriod]:
    q = question.lower()
    out: list[ResolvedPeriod] = []
    rel = f"calendar period relative to today ({today})"

    def add(phrase: str, start: dt.date, end: dt.date, basis: str = rel, extra: str = "") -> None:
        covered, note = _coverage(start, end, first, last)
        out.append(ResolvedPeriod(phrase, start, end, basis, covered, " ".join(x for x in (note, extra) if x)))

    week_start = today - dt.timedelta(days=today.weekday())
    if re.search(r"\b(last|previous|past) week\b", q):
        add(
            "last week",
            week_start - dt.timedelta(days=7),
            week_start - dt.timedelta(days=1),
            extra="Weeks run Monday-Sunday.",
        )
    if re.search(r"\bthis week\b", q):
        add("this week", week_start, week_start + dt.timedelta(days=6), extra="Weeks run Monday-Sunday.")
    if re.search(r"\b(last|previous|past) month\b", q):
        first_this = today.replace(day=1)
        prev_end = first_this - dt.timedelta(days=1)
        add("last month", prev_end.replace(day=1), prev_end)
    if re.search(r"\bthis month\b", q):
        add("this month", today.replace(day=1), _month_end(today.year, today.month))
    if re.search(r"\b(last|previous|past) year\b", q):
        add("last year", dt.date(today.year - 1, 1, 1), dt.date(today.year - 1, 12, 31))
    if re.search(r"\byesterday\b", q):
        yday = today - dt.timedelta(days=1)
        add("yesterday", yday, yday)
    for n, unit in re.findall(r"\b(?:last|past) (\d{1,3}) (day|week|month)s?\b", q):
        days = int(n) * {"day": 1, "week": 7, "month": 30}[unit]
        add(f"last {n} {unit}s", today - dt.timedelta(days=days), today - dt.timedelta(days=1))
    for name, yr in re.findall(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b", q):
        m, y = MONTHS[name], int(yr)
        add(f"{calendar.month_name[m]} {y}", dt.date(y, m, 1), _month_end(y, m), basis="explicit month")
    if not any(p.basis == "explicit month" for p in out):
        for name in re.findall(r"\bin (" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b(?!\s+\d)", q):
            m = MONTHS[name]
            y = last.year if _month_end(last.year, m) <= last else last.year - 1
            add(
                f"{calendar.month_name[m]} (most recent complete)",
                dt.date(y, m, 1),
                _month_end(y, m),
                basis="most recent fully covered month of that name in the data",
            )
    for yr in re.findall(r"\b(?:in|during|for) (\d{4})\b", q):
        y = int(yr)
        add(str(y), dt.date(y, 1, 1), dt.date(y, 12, 31), basis="explicit year")
    if re.search(r"heating[- ]season|heat season", q):
        y = last.year if dt.date(last.year, 10, 31) <= last else last.year - 1
        add(
            "heating-season onset",
            dt.date(y, 10, 1),
            dt.date(y, 10, 31),
            basis="October of the most recent fully covered year (NYC heat season starts 1 October)",
        )
    if re.search(r"\bnext week\b|\bcoming week\b|\bnext 7 days\b", q):
        nxt = week_start + dt.timedelta(days=7)
        out.append(
            ResolvedPeriod(
                "next week (forecast)",
                last + dt.timedelta(days=1),
                last + dt.timedelta(days=7),
                f"the 7 days after the dataset's last complete day ({last}); forecasts start there",
                "none",
                f"Relative to today ({today}) the next calendar week is {nxt}..{nxt + dt.timedelta(days=6)}, which "
                f"the precomputed forecast does not cover.",
            )
        )
    return out
