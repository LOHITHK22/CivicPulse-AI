"""Validated filter model and safe SQL predicate builder.

All user-supplied values are passed as bound parameters. Column names come only from code.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

BOROUGHS = ("BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND", "UNSPECIFIED")
STATUS_GROUPS = ("open", "closed", "unknown")
MAX_RANGE_DAYS = 366 * 3
_AGENCY_RE = re.compile(r"^[A-Z]{2,12}$")
_CATEGORY_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class FilterValidationError(ValueError):
    pass


class Filters(BaseModel):
    """Filters applied consistently by every dashboard query. Dates are inclusive (NYC local)."""

    start: dt.date | None = None
    end: dt.date | None = None
    boroughs: list[str] = Field(default_factory=list, max_length=len(BOROUGHS))
    categories: list[str] = Field(default_factory=list, max_length=20)
    agencies: list[str] = Field(default_factory=list, max_length=20)
    statuses: list[str] = Field(default_factory=list, max_length=len(STATUS_GROUPS))

    @field_validator("boroughs")
    @classmethod
    def _boroughs(cls, v: list[str]) -> list[str]:
        out = sorted({b.strip().upper() for b in v if b.strip()})
        bad = [b for b in out if b not in BOROUGHS]
        if bad:
            raise ValueError(f"unknown borough(s): {bad}; valid: {list(BOROUGHS)}")
        return out

    @field_validator("statuses")
    @classmethod
    def _statuses(cls, v: list[str]) -> list[str]:
        out = sorted({s.strip().lower() for s in v if s.strip()})
        bad = [s for s in out if s not in STATUS_GROUPS]
        if bad:
            raise ValueError(f"unknown status group(s): {bad}; valid: {list(STATUS_GROUPS)}")
        return out

    @field_validator("agencies")
    @classmethod
    def _agencies(cls, v: list[str]) -> list[str]:
        out = sorted({a.strip().upper() for a in v if a.strip()})
        bad = [a for a in out if not _AGENCY_RE.match(a)]
        if bad:
            raise ValueError(f"invalid agency code(s): {bad}")
        return out

    @field_validator("categories")
    @classmethod
    def _categories(cls, v: list[str]) -> list[str]:
        out = sorted({c.strip().lower() for c in v if c.strip()})
        bad = [c for c in out if not _CATEGORY_RE.match(c)]
        if bad:
            raise ValueError(f"invalid category key(s): {bad}")
        return out

    @model_validator(mode="after")
    def _range(self) -> Filters:
        if self.start and self.end:
            if self.end < self.start:
                raise ValueError("end must be on or after start")
            if (self.end - self.start).days > MAX_RANGE_DAYS:
                raise ValueError(f"date range may not exceed {MAX_RANGE_DAYS} days")
        return self

    def where(self, *, date_col: str = "created_date", skip_status: bool = False) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if self.start:
            clauses.append(f"{date_col} >= ?")
            params.append(self.start)
        if self.end:
            clauses.append(f"{date_col} <= ?")
            params.append(self.end)
        for col, values in (("borough", self.boroughs), ("category_key", self.categories), ("agency", self.agencies)):
            if values:
                clauses.append(f"{col} IN ({', '.join('?' for _ in values)})")
                params.extend(values)
        if self.statuses and not skip_status:
            clauses.append(f"status_group IN ({', '.join('?' for _ in self.statuses)})")
            params.extend(self.statuses)
        return (" AND ".join(clauses) if clauses else "TRUE"), params

    def cache_key(self) -> str:
        return self.model_dump_json()
