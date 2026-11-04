from __future__ import annotations

import datetime as dt
import json

import polars as pl
import pytest

from civicpulse_ingestion.extract import build_where, list_runs, read_watermark, run_extraction
from civicpulse_ingestion.schema import SchemaError
from civicpulse_ingestion.socrata import SocrataClient, SocrataError, soql_quote
from conftest import FakeSocrata, make_record


def client_for(fake: FakeSocrata, cfg, slept: list[float]) -> SocrataClient:
    return SocrataClient(cfg.source, cfg.http, transport=fake.transport, sleep=slept.append)


def read_raw(paths) -> pl.DataFrame:
    return pl.read_parquet([p.as_posix() for p in sorted(paths.raw.rglob("*.parquet"))])


# ---------------------------------------------------------------- pagination
def test_keyset_pagination_fetches_every_record_exactly_once(cfg, paths, no_sleep):
    records = [make_record(i) for i in range(35)]  # 4 pages of 10 (last partial)
    fake = FakeSocrata(records)
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    raw = read_raw(paths)
    assert state.status == "succeeded"
    assert state.pages_written == 4 and state.rows_fetched == 35
    assert sorted(raw["unique_key"].to_list()) == sorted(r["unique_key"] for r in records)
    # The server :id order is not string order; pagination must follow the server, not sort locally.
    assert [r[":id"] for r in records] != sorted(r[":id"] for r in records)
    data_reqs = [r for r in fake.requests if "/resource/" in r.url.path]
    assert all(r.url.params["$order"] == ":id" for r in data_reqs)
    assert ":id >" not in data_reqs[0].url.params["$where"]
    assert f":id > {soql_quote(records[9][':id'])}" in data_reqs[1].url.params["$where"]


def test_exact_multiple_of_page_size_terminates_with_empty_page(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(i) for i in range(20)])
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert state.rows_fetched == 20 and state.pages_written == 2


def test_request_uses_explicit_select_and_server_side_filters(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(1)])
    run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    req = next(r for r in fake.requests if "/resource/" in r.url.path)
    select = req.url.params["$select"].split(",")
    assert select == cfg.source.system_fields + cfg.source.fields
    assert "incident_address" not in select  # privacy: never ingested
    where = req.url.params["$where"]
    assert "complaint_type in('Noise - Residential','HEAT/HOT WATER')" in where
    assert "created_date >= '2026-01-01T00:00:00'" in where
    assert "created_date < '2026-03-01T00:00:00'" in where


def test_soql_quote_escapes_single_quotes():
    assert soql_quote("O'Brien") == "'O''Brien'"


# ---------------------------------------------------------------- retries / rate limits
def test_retries_on_429_honouring_retry_after_then_succeeds(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(i) for i in range(5)])
    fake.failures = [("429", "7")]  # metadata call gets throttled first
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert state.status == "succeeded"
    assert no_sleep == [7.0]


def test_retries_on_5xx_and_timeouts_with_bounded_jittered_backoff(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(i) for i in range(5)])
    fake.failures = [503, "timeout", 502]
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert state.status == "succeeded"
    assert len(no_sleep) == 3
    for attempt, delay in enumerate(no_sleep):
        assert 0 <= delay <= min(cfg.http.backoff_max_seconds, cfg.http.backoff_base_seconds * 2**attempt)


def test_non_retryable_4xx_fails_immediately(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(1)])
    fake.failures = [400]
    with pytest.raises(SocrataError) as exc:
        run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert exc.value.status_code == 400 and exc.value.attempts == 1
    assert no_sleep == []


# ---------------------------------------------------------------- failure & recovery
def test_failed_batch_is_logged_and_run_resumes_from_checkpoint(cfg, paths, no_sleep):
    import httpx

    records = [make_record(i) for i in range(30)]
    fake = FakeSocrata(records)
    calls = {"n": 0}

    def flaky(request):
        # metadata OK, page 1 OK, then page 2 fails more times than max_retries allows.
        if "/resource/" in request.url.path:
            calls["n"] += 1
            if 2 <= calls["n"] <= 2 + cfg.http.max_retries:
                return httpx.Response(503, text="down")
        return fake.handler(request)

    client = SocrataClient(cfg.source, cfg.http, transport=httpx.MockTransport(flaky), sleep=no_sleep.append)
    with pytest.raises(SocrataError):
        run_extraction(cfg, paths, client, mode="backfill")
    [run] = list_runs(paths)
    assert run.status == "failed" and run.pages_written == 1 and run.last_id == records[9][":id"]
    failed = [json.loads(line) for line in paths.failed_batches_file.read_text().splitlines()]
    assert failed[0]["page_no"] == 2 and failed[0]["after_id"] == records[9][":id"]
    assert read_watermark(paths) is None  # a failed run never advances the watermark

    # Resume: same run id, continues after page 1, no duplicates.
    state = run_extraction(cfg, paths, client, mode="backfill")
    assert state.run_id == run.run_id and state.status == "succeeded"
    raw = read_raw(paths)
    assert raw.height == 30 and raw["unique_key"].n_unique() == 30
    assert read_watermark(paths) is not None


def test_caller_page_cap_pauses_without_advancing_watermark(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(i) for i in range(30)])
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill", max_pages=1)
    assert state.status == "paused" and state.rows_fetched == 10
    assert read_watermark(paths) is None
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert state.status == "succeeded" and state.rows_fetched == 30


# ---------------------------------------------------------------- schema
def test_schema_drift_is_recorded_as_warning(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(1)])
    fake.metadata["columns"].append({"fieldName": "new_col", "dataTypeName": "text"})
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert state.status == "succeeded"
    assert "column added at source: new_col" in state.schema_warnings


def test_missing_selected_field_aborts_before_fetching(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(1)])
    fake.metadata["columns"] = [c for c in fake.metadata["columns"] if c["fieldName"] != "borough"]
    with pytest.raises(SchemaError):
        run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert not any("/resource/" in r.url.path for r in fake.requests)


def test_page_with_missing_required_fields_is_flagged(cfg, paths, no_sleep):
    bad = make_record(2)
    del bad["unique_key"]
    fake = FakeSocrata([make_record(1), bad])
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    assert any("missing required field unique_key" in w for w in state.page_warnings)


# ---------------------------------------------------------------- incremental
def test_incremental_requires_watermark(cfg, paths, no_sleep):
    with pytest.raises(RuntimeError, match="watermark"):
        run_extraction(cfg, paths, client_for(FakeSocrata([]), cfg, no_sleep), mode="incremental")


def test_incremental_uses_lookback_and_picks_up_late_updates(cfg, paths, no_sleep):
    records = [make_record(i) for i in range(12)]
    fake = FakeSocrata(records)
    run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    wm = read_watermark(paths)
    assert wm == dt.datetime(2026, 2, 1, tzinfo=dt.UTC)

    # A late update to an existing request plus a brand-new request after the watermark.
    records[3] = {**records[3], ":updated_at": "2026-02-03T10:00:00.000Z", "status": "Open"}
    records[3].pop("closed_date")
    records.append(make_record(99, **{":updated_at": "2026-02-03T11:00:00.000Z"}))
    fake.requests.clear()
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="incremental")
    where = next(r for r in fake.requests if "/resource/" in r.url.path).url.params["$where"]
    # lookback 72h behind watermark 2026-02-01T00:00 -> 2026-01-29T00:00
    assert ":updated_at > '2026-01-29T00:00:00'" in where
    assert "created_date <" not in where  # incremental has no upper created bound
    # The lookback deliberately re-fetches rows updated near the watermark (all 12 here share
    # the watermark timestamp) plus the new one; the warehouse dedup resolves the overlap.
    assert state.rows_fetched == 13
    inc = pl.read_parquet(list((paths.raw / f"run={state.run_id}").glob("*.parquet")))
    late = inc.filter(pl.col("unique_key") == records[3]["unique_key"]).row(0, named=True)
    assert late["status"] == "Open" and late["closed_date"] is None
    assert records[-1]["unique_key"] in inc["unique_key"].to_list()
    assert read_watermark(paths) == dt.datetime(2026, 2, 3, 11, tzinfo=dt.UTC)

    from civicpulse_ingestion.transform import build_warehouse

    build_warehouse(cfg, paths, snapshot_at=dt.datetime(2026, 2, 10, tzinfo=dt.UTC))
    fact = pl.read_parquet(paths.table("fact_service_requests"))
    assert fact.height == 13
    assert fact.filter(pl.col("unique_key") == records[3]["unique_key"])["status_group"].item() == "open"


def test_incremental_lookback_excludes_rows_older_than_window(cfg, paths, no_sleep):
    old = [make_record(i, **{":updated_at": "2026-01-02T00:00:00.000Z"}) for i in range(5)]
    recent = [make_record(10 + i, **{":updated_at": "2026-02-01T00:00:00.000Z"}) for i in range(3)]
    fake = FakeSocrata(old + recent)
    run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="backfill")
    state = run_extraction(cfg, paths, client_for(fake, cfg, no_sleep), mode="incremental")
    assert state.rows_fetched == 3  # only rows updated within the 72h lookback


def test_build_where_quotes_category_values(cfg):
    cfg.categories[0].complaint_types = ["Bad'Type"]
    w = build_where(cfg, start=dt.date(2026, 1, 1), end=None)
    assert "'Bad''Type'" in w


# ---------------------------------------------------------------- chunked backfills + manifest (M2)
def test_chunked_backfill_skips_succeeded_window_without_refetching(cfg, paths, no_sleep):
    fake = FakeSocrata([make_record(i) for i in range(15)])
    client = client_for(fake, cfg, no_sleep)
    first = run_extraction(cfg, paths, client, mode="backfill", start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 1))
    n_requests = len(fake.requests)
    again = run_extraction(
        cfg, paths, client, mode="backfill", start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 1), skip_if_succeeded=True
    )
    assert again.run_id == first.run_id and len(fake.requests) == n_requests  # nothing fetched
    other = run_extraction(
        cfg, paths, client, mode="backfill", start=dt.date(2026, 2, 1), end=dt.date(2026, 3, 1), skip_if_succeeded=True
    )
    assert other.run_id != first.run_id  # a different window is a different run
    assert len(list_runs(paths)) == 2


def test_manifest_checksums_counts_and_coverage(cfg, paths, no_sleep):
    from civicpulse_ingestion.manifest import build_manifest

    fake = FakeSocrata([make_record(i) for i in range(15)])
    client = client_for(fake, cfg, no_sleep)
    run_extraction(cfg, paths, client, mode="backfill", start=dt.date(2026, 1, 1), end=dt.date(2026, 2, 1))
    run_extraction(cfg, paths, client, mode="backfill", start=dt.date(2026, 2, 15), end=dt.date(2026, 3, 1))
    m = build_manifest(paths, client)
    assert all(r["rows_fetched_match_disk"] and r["source_count_matches"] for r in m["runs"])
    assert all(len(r["checksum_sha256"]) == 64 for r in m["runs"])
    cov = m["backfill_coverage"]
    assert cov["start"] == "2026-01-01" and cov["end_exclusive"] == "2026-03-01"
    assert cov["gaps_between_windows"] == [{"from": "2026-02-01", "to": "2026-02-15"}]  # reported, not filled
    assert m["totals"]["raw_rows"] == 30
    # Any change to a raw page changes the run checksum.
    page = next(paths.raw.rglob("page-00001.parquet"))
    before = [r["checksum_sha256"] for r in build_manifest(paths)["runs"]]
    pl.read_parquet(page).head(3).write_parquet(page)
    after = [r["checksum_sha256"] for r in build_manifest(paths)["runs"]]
    assert sum(a != b for a, b in zip(before, after, strict=True)) == 1
