"""Source schema validation and drift detection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from civicpulse_shared.config import REPO_ROOT

SNAPSHOT_PATH = REPO_ROOT / "configs" / "nyc311_schema_snapshot.json"

# Fields that must be present on every record (a record without them is rejected later).
REQUIRED_RECORD_FIELDS = ("unique_key", "created_date", "complaint_type", ":id")


class SchemaError(RuntimeError):
    """The source no longer provides fields the pipeline depends on."""


@dataclass
class SchemaReport:
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    type_changed: dict[str, tuple[str, str]] = field(default_factory=dict)
    missing_selected: list[str] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.added or self.removed or self.type_changed)

    def warnings(self) -> list[str]:
        out = [f"column added at source: {c}" for c in self.added]
        out += [f"column removed at source: {c}" for c in self.removed]
        out += [f"type changed for {c}: {a} -> {b}" for c, (a, b) in self.type_changed.items()]
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "type_changed": {k: list(v) for k, v in self.type_changed.items()},
            "missing_selected": self.missing_selected,
        }


def load_snapshot(path: Path = SNAPSHOT_PATH) -> dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        cols: dict[str, str] = json.load(fh)["columns"]
    return cols


def compare_metadata(
    metadata: dict[str, Any], selected_fields: list[str], snapshot: dict[str, str] | None = None
) -> SchemaReport:
    """Compare live dataset metadata with the committed snapshot and the selected fields."""
    snapshot = snapshot if snapshot is not None else load_snapshot()
    live = {
        c["fieldName"]: c.get("dataTypeName", "")
        for c in metadata.get("columns", [])
        if not str(c.get("fieldName", "")).startswith(":@")
    }
    rep = SchemaReport()
    rep.added = sorted(set(live) - set(snapshot))
    rep.removed = sorted(set(snapshot) - set(live))
    rep.type_changed = {k: (snapshot[k], live[k]) for k in sorted(set(live) & set(snapshot)) if snapshot[k] != live[k]}
    rep.missing_selected = sorted(f for f in selected_fields if f not in live)
    return rep


def validate_page(records: list[dict[str, Any]], selected: list[str]) -> list[str]:
    """Validate the shape of one page.

    Socrata omits keys whose value is null, so a missing optional key is normal. We flag:
    * keys we did not request (should be impossible with ``$select``), and
    * required keys missing from records (counted; those records are rejected downstream).
    """
    allowed = set(selected)
    problems: list[str] = []
    unexpected: set[str] = set()
    missing_required = dict.fromkeys(REQUIRED_RECORD_FIELDS, 0)
    for rec in records:
        if not isinstance(rec, dict):
            problems.append("non-object record in page")
            continue
        unexpected.update(k for k in rec if k not in allowed)
        for k in REQUIRED_RECORD_FIELDS:
            if rec.get(k) in (None, ""):
                missing_required[k] += 1
    if unexpected:
        problems.append(f"unexpected fields: {sorted(unexpected)}")
    problems += [f"{n} records missing required field {k}" for k, n in missing_required.items() if n]
    return problems
