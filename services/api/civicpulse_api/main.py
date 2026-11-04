"""FastAPI application exposing read-only analytics over the NYC 311 warehouse."""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from civicpulse_analytics import AnalyticsService, DataUnavailableError, Filters
from civicpulse_analytics.models import (
    Breakdown,
    ClosureDistribution,
    FilterOptions,
    HourlyPattern,
    Kpis,
    QualityReport,
    RequestDetail,
    RequestPage,
    Series,
)
from civicpulse_api.ratelimit import TokenBucketLimiter
from civicpulse_shared.config import get_settings
from civicpulse_shared.logging import configure_logging, log

logger = logging.getLogger("civicpulse_api")
API_VERSION = "0.1.0"
MAX_URL_LENGTH = 4096


@lru_cache(maxsize=1)
def get_service() -> AnalyticsService:
    return AnalyticsService(get_settings().data_dir)


def get_filters(
    start: Annotated[dt.date | None, Query(description="Inclusive start date (created_date, NYC local)")] = None,
    end: Annotated[dt.date | None, Query(description="Inclusive end date")] = None,
    borough: Annotated[list[str] | None, Query(max_length=40)] = None,
    category: Annotated[list[str] | None, Query(max_length=64)] = None,
    agency: Annotated[list[str] | None, Query(max_length=12)] = None,
    status: Annotated[list[str] | None, Query(max_length=10)] = None,
) -> Filters:
    try:
        return Filters(
            start=start,
            end=end,
            boroughs=borough or [],
            categories=category or [],
            agencies=agency or [],
            statuses=status or [],
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=[{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors(include_url=False, include_input=False)],
        ) from exc


FiltersDep = Annotated[Filters, Depends(get_filters)]
ServiceDep = Annotated[AnalyticsService, Depends(get_service)]


def client_key(request: Request, trusted_hops: int) -> str:
    """Rate-limit identity. Uses X-Forwarded-For only when trusted proxies append to it:
    the entry `trusted_hops` from the right is the address the outermost trusted proxy saw."""
    peer = request.client.host if request.client else "unknown"
    if trusted_hops <= 0:
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if len(chain) < trusted_hops:
        return peer
    return chain[-trusted_hops]


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="CivicPulse AI — Analytics API",
        version=API_VERSION,
        description="Read-only analytics over NYC 311 service requests (NYC Open Data erm2-nwe9). "
        "Data is a versioned snapshot; see /api/v1/quality for freshness.",
    )
    limiter = TokenBucketLimiter(settings.api_rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"detail": "read-only API"}, status_code=405)
        if len(str(request.url)) > MAX_URL_LENGTH:
            return JSONResponse({"detail": "request URL too long"}, status_code=414)
        client = client_key(request, settings.api_trusted_proxy_hops)
        if request.url.path.startswith("/api/"):
            allowed, retry = limiter.allow(client)
            if not allowed:
                return JSONResponse(
                    {"detail": "rate limit exceeded"},
                    status_code=429,
                    headers={"Retry-After": str(max(1, int(retry + 0.5)))},
                )
        response = await call_next(request)
        elapsed = (time.perf_counter() - started) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        log(
            logger,
            logging.INFO,
            "request",
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            elapsed_ms=round(elapsed, 1),
        )
        return response

    @app.exception_handler(DataUnavailableError)
    async def _unavailable(_: Request, exc: DataUnavailableError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "data_unavailable"}, status_code=503)

    @app.exception_handler(ValueError)
    async def _bad_value(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "code": "invalid_argument"}, status_code=422)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error")
        return JSONResponse({"detail": "internal error", "code": "internal"}, status_code=500)

    # ---- health -----------------------------------------------------------------
    @app.get("/health", tags=["health"])
    def health() -> dict[str, Any]:
        """Liveness: the process is up."""
        return {"status": "ok", "version": API_VERSION}

    @app.get("/health/ready", tags=["health"])
    def ready(svc: ServiceDep) -> JSONResponse:
        """Readiness: warehouse tables exist and are queryable."""
        try:
            info = svc.health()
        except Exception as exc:  # report, never mask as healthy
            return JSONResponse({"ready": False, "error": type(exc).__name__}, status_code=503)
        return JSONResponse(info, status_code=200 if info["ready"] else 503)

    # ---- metadata -----------------------------------------------------------------
    @app.get("/api/v1/meta/filters", response_model=FilterOptions, tags=["meta"])
    def filter_options(svc: ServiceDep) -> FilterOptions:
        return svc.filter_options()

    # ---- metrics ------------------------------------------------------------------
    @app.get("/api/v1/metrics/kpis", response_model=Kpis, tags=["metrics"])
    def kpis(f: FiltersDep, svc: ServiceDep, threshold_days: Annotated[int, Query(ge=1, le=90)] = 7) -> Kpis:
        return svc.kpis(f, threshold_days)

    @app.get("/api/v1/metrics/volume", response_model=Series, tags=["metrics"])
    def volume(
        f: FiltersDep,
        svc: ServiceDep,
        grain: Annotated[str, Query(pattern="^(day|week|month)$")] = "day",
        split_by: Annotated[str, Query(pattern="^(category_key|borough|status_group|none)$")] = "category_key",
    ) -> Series:
        return svc.volume_series(f, grain, split_by)

    @app.get("/api/v1/metrics/breakdown/{dimension}", response_model=Breakdown, tags=["metrics"])
    def breakdown(dimension: str, f: FiltersDep, svc: ServiceDep) -> Breakdown:
        return svc.breakdown(f, dimension)

    @app.get("/api/v1/metrics/closure-distribution", response_model=ClosureDistribution, tags=["metrics"])
    def closure(f: FiltersDep, svc: ServiceDep) -> ClosureDistribution:
        return svc.closure_distribution(f)

    @app.get("/api/v1/metrics/hourly", response_model=HourlyPattern, tags=["metrics"])
    def hourly(f: FiltersDep, svc: ServiceDep) -> HourlyPattern:
        return svc.hourly_pattern(f)

    # ---- request explorer ---------------------------------------------------------
    @app.get("/api/v1/requests", response_model=RequestPage, tags=["requests"])
    def requests(
        f: FiltersDep,
        svc: ServiceDep,
        q: Annotated[str | None, Query(max_length=100)] = None,
        sort: Annotated[str, Query(max_length=32)] = "-created_at",
        page: Annotated[int, Query(ge=1, le=100_000)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> RequestPage:
        return svc.list_requests(f, search=q, sort=sort, page=page, page_size=page_size)

    @app.get("/api/v1/requests/export.csv", tags=["requests"])
    def export(f: FiltersDep, svc: ServiceDep, q: Annotated[str | None, Query(max_length=100)] = None) -> Response:
        cols, rows, total = svc.export_requests(f, search=q)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        w.writerows(rows)
        headers = {
            "Content-Disposition": 'attachment; filename="civicpulse_requests.csv"',
            "X-Total-Matching": str(total),
            "X-Rows-Exported": str(len(rows)),
            "X-Export-Truncated": str(total > len(rows)).lower(),
        }
        return Response(buf.getvalue(), media_type="text/csv", headers=headers)

    @app.get("/api/v1/requests/{unique_key}", response_model=RequestDetail, tags=["requests"])
    def request_detail(unique_key: str, svc: ServiceDep) -> RequestDetail:
        detail = svc.get_request(unique_key)
        if detail is None:
            raise HTTPException(404, "request not found in loaded snapshot")
        return detail

    # ---- quality ------------------------------------------------------------------
    @app.get("/api/v1/quality", response_model=QualityReport, tags=["quality"])
    def quality(svc: ServiceDep) -> QualityReport:
        return svc.quality_report()

    return app


app = create_app()
