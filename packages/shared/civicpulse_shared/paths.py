"""Canonical on-disk layout. Raw data is always kept separate from cleaned data."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DataPaths:
    root: Path

    @property
    def raw(self) -> Path:
        return self.root / "raw" / "nyc311"

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def runs(self) -> Path:
        return self.state / "runs"

    @property
    def watermark_file(self) -> Path:
        return self.state / "watermark.json"

    @property
    def failed_batches_file(self) -> Path:
        return self.state / "failed_batches.jsonl"

    @property
    def source_metadata_file(self) -> Path:
        return self.state / "source_metadata.json"

    def table(self, name: str) -> Path:
        return self.processed / f"{name}.parquet"

    def ensure(self) -> None:
        for p in (self.raw, self.processed, self.runs):
            p.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Write JSON atomically (temp file + rename) so a crash never leaves a torn state file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True, default=str)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
