"""Extraction runner: keyset pagination, checkpoints, watermarks, lineage, failure logging.

Raw pages are written as Parquet with every source value kept as a string, plus lineage
columns. Nothing is cleaned here — cleaning happens in :mod:`civicpulse_ingestion.transform`.

Run state lives in ``data/state/runs/<run_id>.json`` and is updated *after* each page file is
written. If a run dies between those two steps, resuming re-fetches the same page into the same
file name, so recovery is idempotent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import polars as pl

from civicpulse_ingestion.schema import SchemaError, compare_metadata, validate_page
from civicpulse_ingestion.socrata import SocrataClient, SocrataError, soql_quote
from civicpulse_shared.config import IngestionConfig
from civicpulse_shared.logging import log
from civicpulse_shared.paths import DataPaths, atomic_write_json, read_json

logger = logging.getLogger(__name__)

Mode = Literal["backfill", "incremental"]

# Socrata system field -> raw column name (colons are awkward in SQL).
SYSTEM_COLUMN_MAP = {":id": "_sys_id", ":updated_at": "_sys_updated_at"}


@dataclass
class RunState:
    run_id: str
    mode: str
    where: str
    status: str = "running"  # running | paused | succeeded | failed
    started_at: str = field(default_factory=lambda: _utcnow().isoformat())
    finished_at: str | None = None
    pages_written: int = 0
    rows_fetched: int = 0
    last_id: str | None = None
    max_updated_at: str | None = None
    total_attempts: int = 0
    schema_warnings: list[str] = field(default_factory=list)
    page_warnings: list[str] = field(default_factory=list)
    source_rows_updated_at: str | None = None
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def build_where(
    cfg: IngestionConfig,
    *,
    start: dt.date,
    end: dt.date | None,
    updated_after: dt.datetime | None = None,
) -> str:
    types = ",".join(soql_quote(t) for t in cfg.complaint_types)
    parts = [f"complaint_type in({types})", f"created_date >= '{start.isoformat()}T00:00:00'"]
    if end is not None:
        parts.append(f"created_date < '{end.isoformat()}T00:00:00'")
    if updated_after is not None:
        ts = updated_after.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S")
        parts.append(f":updated_at > '{ts}'")
    return " AND ".join(parts)


def _state_path(paths: DataPaths, run_id: str) -> Path:
    return paths.runs / f"{run_id}.json"


def save_state(paths: DataPaths, state: RunState) -> None:
    atomic_write_json(_state_path(paths, state.run_id), asdict(state))


def load_state(paths: DataPaths, run_id: str) -> RunState:
    return RunState(**read_json(_state_path(paths, run_id)))


def list_runs(paths: DataPaths) -> list[RunState]:
    if not paths.runs.exists():
        return []
    runs = [RunState(**read_json(p)) for p in paths.runs.glob("*.json")]
    return sorted(runs, key=lambda r: r.started_at)


def find_resumable(paths: DataPaths, mode: str, where: str) -> RunState | None:
    for r in reversed(list_runs(paths)):
        if r.mode == mode and r.where == where and r.status in ("running", "failed", "paused"):
            return r
    return None


def read_watermark(paths: DataPaths) -> dt.datetime | None:
    if not paths.watermark_file.exists():
        return None
    return dt.datetime.fromisoformat(read_json(paths.watermark_file)["max_updated_at"])


def write_watermark(paths: DataPaths, value: dt.datetime, run_id: str) -> None:
    current = read_watermark(paths)
    if current is not None and current >= value:
        return  # watermarks only move forward
    atomic_write_json(
        paths.watermark_file,
        {"max_updated_at": value.isoformat(), "set_by_run": run_id, "set_at": _utcnow().isoformat()},
    )


def _log_failed_batch(paths: DataPaths, state: RunState, page_no: int, error: SocrataError) -> None:
    paths.failed_batches_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utcnow().isoformat(),
        "run_id": state.run_id,
        "page_no": page_no,
        "after_id": state.last_id,
        "where": state.where,
        "status_code": error.status_code,
        "attempts": error.attempts,
        "error": str(error)[:500],
    }
    with open(paths.failed_batches_file, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def _page_frame(
    records: list[dict[str, Any]], cfg: IngestionConfig, state: RunState, page_no: int, fetched_at: str
) -> pl.DataFrame:
    cols = cfg.source.system_fields + cfg.source.fields
    data: dict[str, list[str | None]] = {}
    for c in cols:
        name = SYSTEM_COLUMN_MAP.get(c, c)
        data[name] = [None if r.get(c) is None else str(r.get(c)) for r in records]
    df = pl.DataFrame(data, schema={SYSTEM_COLUMN_MAP.get(c, c): pl.Utf8 for c in cols})
    return df.with_columns(
        pl.lit(state.run_id).alias("_ingest_run_id"),
        pl.lit(page_no, dtype=pl.Int32).alias("_page_no"),
        pl.lit(fetched_at).alias("_fetched_at"),
        pl.lit(cfg.source.dataset_id).alias("_source_dataset"),
    )


def run_extraction(
    cfg: IngestionConfig,
    paths: DataPaths,
    client: SocrataClient,
    *,
    mode: Mode,
    start: dt.date | None = None,
    end: dt.date | None = None,
    resume: bool = True,
    max_pages: int | None = None,
    check_schema: bool = True,
) -> RunState:
    """Run (or resume) one extraction. Returns the final run state; raises on failure."""
    paths.ensure()
    start = start or cfg.backfill.start
    updated_after: dt.datetime | None = None
    if mode == "backfill":
        end = end or cfg.backfill.end
    else:
        wm = read_watermark(paths)
        if wm is None:
            raise RuntimeError("no watermark yet: run a backfill before incremental extraction")
        updated_after = wm - dt.timedelta(hours=cfg.incremental.lookback_hours)
        end = None  # incremental picks up new requests too
    where = build_where(cfg, start=start, end=end, updated_after=updated_after)

    state = find_resumable(paths, mode, where) if resume else None
    if state is not None:
        log(
            logger,
            logging.INFO,
            "resuming run",
            run_id=state.run_id,
            last_id=state.last_id,
            pages_written=state.pages_written,
        )
        state.status, state.error = "running", None
    else:
        run_id = _utcnow().strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        state = RunState(
            run_id=run_id,
            mode=mode,
            where=where,
            params={
                "start": start.isoformat(),
                "end": end.isoformat() if end else None,
                "updated_after": updated_after.isoformat() if updated_after else None,
                "page_size": cfg.http.page_size,
                "categories": [c.key for c in cfg.categories],
            },
        )

    if check_schema:
        meta = client.fetch_metadata()
        report = compare_metadata(meta, cfg.source.fields)
        state.schema_warnings = report.warnings()
        rows_updated = meta.get("rowsUpdatedAt")
        if rows_updated:
            state.source_rows_updated_at = dt.datetime.fromtimestamp(rows_updated, dt.UTC).isoformat()
        atomic_write_json(
            paths.source_metadata_file,
            {
                "checked_at": _utcnow().isoformat(),
                "rows_updated_at": state.source_rows_updated_at,
                "name": meta.get("name"),
                "attribution": meta.get("attribution"),
                "schema_report": report.to_dict(),
            },
        )
        if report.missing_selected:
            state.status, state.error = "failed", f"selected fields missing at source: {report.missing_selected}"
            save_state(paths, state)
            raise SchemaError(state.error)
    save_state(paths, state)

    run_dir = paths.raw / f"run={state.run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    page_cap = min(max_pages or cfg.http.max_pages_per_run, cfg.http.max_pages_per_run)
    selected = cfg.source.system_fields + cfg.source.fields
    pages_this_call = 0

    while pages_this_call < page_cap:
        page_no = state.pages_written + 1
        try:
            page = client.fetch_page(where, after_id=state.last_id, limit=cfg.http.page_size)
        except SocrataError as exc:
            _log_failed_batch(paths, state, page_no, exc)
            state.status, state.error = "failed", str(exc)[:500]
            state.finished_at = _utcnow().isoformat()
            save_state(paths, state)
            log(logger, logging.ERROR, "extraction failed; resumable", run_id=state.run_id, page_no=page_no)
            raise
        state.total_attempts += page.attempts
        if not page.records:
            break
        problems = validate_page(page.records, selected)
        state.page_warnings += [f"page {page_no}: {p}" for p in problems]
        fetched_at = _utcnow().isoformat()
        frame = _page_frame(page.records, cfg, state, page_no, fetched_at)
        frame.write_parquet(run_dir / f"page-{page_no:05d}.parquet", compression="zstd")

        last_id = page.records[-1].get(":id")
        if not last_id:
            state.status, state.error = "failed", "page missing :id; cannot paginate safely"
            save_state(paths, state)
            raise SocrataError(state.error)
        updated = [r[":updated_at"] for r in page.records if r.get(":updated_at")]
        if updated:
            page_max = max(updated)
            if state.max_updated_at is None or page_max > state.max_updated_at:
                state.max_updated_at = page_max
        state.last_id = str(last_id)
        state.pages_written = page_no
        state.rows_fetched += len(page.records)
        save_state(paths, state)
        pages_this_call += 1
        log(
            logger,
            logging.INFO,
            "page written",
            run_id=state.run_id,
            page_no=page_no,
            rows=len(page.records),
            elapsed_s=round(page.elapsed_s, 2),
            attempts=page.attempts,
        )
        if len(page.records) < cfg.http.page_size:
            break
    else:
        state.finished_at = _utcnow().isoformat()
        if max_pages is not None and max_pages < cfg.http.max_pages_per_run:
            # Caller asked for a bounded slice: pause (resumable) and do NOT advance the watermark.
            state.status = "paused"
            save_state(paths, state)
            log(
                logger,
                logging.INFO,
                "extraction paused at caller page cap",
                run_id=state.run_id,
                pages=state.pages_written,
            )
            return state
        state.status, state.error = "failed", f"safety page cap {page_cap} reached"
        save_state(paths, state)
        raise RuntimeError(state.error)

    state.status = "succeeded"
    state.finished_at = _utcnow().isoformat()
    save_state(paths, state)
    if state.max_updated_at:
        write_watermark(paths, dt.datetime.fromisoformat(state.max_updated_at.replace("Z", "+00:00")), state.run_id)
    log(
        logger,
        logging.INFO,
        "extraction succeeded",
        run_id=state.run_id,
        rows=state.rows_fetched,
        pages=state.pages_written,
    )
    return state
