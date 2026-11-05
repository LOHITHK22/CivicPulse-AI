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
from fastapi import Path as FastAPIPath
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
from civicpulse_analytics.predictions import PredictionService
from civicpulse_api.ai import INVESTIGATIONS_PATH, build_router, get_agent_settings, origin_allowed
from civicpulse_api.ratelimit import TokenBucketLimiter
from civicpulse_shared.config import get_settings
from civicpulse_shared.logging import configure_logging, log

logger = logging.getLogger("civicpulse_api")
API_VERSION = "0.3.0"
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


@lru_cache(maxsize=1)
def get_predictions() -> PredictionService:
    return PredictionService(get_settings().data_dir)


FiltersDep = Annotated[Filters, Depends(get_filters)]
PredDep = Annotated[PredictionService, Depends(get_predictions)]
CategoryQ = Annotated[str, Query(min_length=1, max_length=64, pattern=r"^[a-z_]+$")]
BoroughQ = Annotated[str, Query(min_length=1, max_length=20, pattern=r"^[A-Z ]+$")]
OptCategoryQ = Annotated[str | None, Query(max_length=64, pattern=r"^[a-z_]+$")]
OptBoroughQ = Annotated[str | None, Query(max_length=20, pattern=r"^[A-Z ]+$")]
ModelName = Annotated[str, FastAPIPath(pattern=r"^(forecast|delay_risk|anomaly)$")]
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
        description="Read-only analytics and precomputed model outputs over NYC 311 service requests "
        "(NYC Open Data erm2-nwe9). Data is a versioned snapshot; see /api/v1/quality for freshness. "
        "Models are trained offline; no endpoint trains or executes a model.",
    )
    limiter = TokenBucketLimiter(settings.api_rate_limit_per_minute)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # The only write-shaped route is the AI investigation POST (it reads data, never writes it).
            if request.method != "POST" or request.url.path != INVESTIGATIONS_PATH:
                return JSONResponse({"detail": "read-only API"}, status_code=405)
            if not origin_allowed(request.headers.get("origin")):
                return JSONResponse({"detail": "origin not allowed"}, status_code=403)
            if not request.headers.get("content-type", "").lower().startswith("application/json"):
                return JSONResponse({"detail": "content type must be application/json"}, status_code=415)
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse({"detail": "content-length required"}, status_code=411)
            if int(length) > get_agent_settings().ai_max_body_bytes:
                return JSONResponse({"detail": "request body too large"}, status_code=413)
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

    # ---- models: metadata, evaluation, explanations (read-only, precomputed) -------------
    @app.get("/api/v1/models", tags=["models"])
    def models(pred: PredDep) -> dict[str, Any]:
        """Registry of the current model run: versions, data build, periods, selected models."""
        return pred.registry()

    @app.get("/api/v1/models/{name}/evaluation", tags=["models"])
    def model_evaluation(name: ModelName, pred: PredDep) -> dict[str, Any]:
        """Full offline evaluation (backtests / held-out test metrics) for one model family."""
        return pred.evaluation(name)

    @app.get("/api/v1/models/{name}/explanations", tags=["models"])
    def model_explanations(name: ModelName, pred: PredDep) -> dict[str, Any]:
        """Global feature importance (permutation, gain, coefficients). Associations, not causes."""
        return pred.explanations(name)

    # ---- predictions --------------------------------------------------------------
    @app.get("/api/v1/predictions/forecast", tags=["predictions"])
    def forecast(
        pred: PredDep,
        category: CategoryQ,
        borough: BoroughQ,
        history_days: Annotated[int, Query(ge=7, le=365)] = 56,
    ) -> dict[str, Any]:
        """Seven-day forecast with prediction intervals for one category x borough, plus recent history."""
        return pred.forecast(category, borough, history_days)

    @app.get("/api/v1/predictions/forecast/backtest", tags=["predictions"])
    def forecast_backtest(
        pred: PredDep,
        category: CategoryQ,
        borough: BoroughQ,
        horizon: Annotated[int, Query(ge=1, le=7)] = 1,
        model: Annotated[str | None, Query(pattern=r"^(naive|seasonal_naive|rolling_mean|lightgbm)$")] = None,
    ) -> dict[str, Any]:
        """Out-of-sample backtest predictions vs actuals (validation + test origins)."""
        return pred.backtest(category, borough, horizon, model)

    @app.get("/api/v1/predictions/delay-risk", tags=["predictions"])
    def delay_risk(
        pred: PredDep,
        category: OptCategoryQ = None,
        borough: OptBoroughQ = None,
        min_risk: Annotated[float, Query(ge=0, le=1)] = 0.0,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """Highest-risk recent requests whose 7-day outcome is not yet known."""
        return pred.delay_risk_list(category, borough, min_risk, limit)

    @app.get("/api/v1/predictions/delay-risk/summary", tags=["predictions"])
    def delay_risk_summary(pred: PredDep) -> dict[str, Any]:
        return pred.delay_risk_summary()

    @app.get("/api/v1/predictions/delay-risk/{unique_key}", tags=["predictions"])
    def delay_risk_request(
        pred: PredDep, unique_key: Annotated[str, FastAPIPath(pattern=r"^[0-9]{1,12}$")]
    ) -> dict[str, Any]:
        r = pred.delay_risk_request(unique_key)
        if r is None:
            raise HTTPException(404, "request is not in the scored set (test period or recent requests)")
        return r

    @app.get("/api/v1/anomalies", tags=["predictions"])
    def anomalies(
        pred: PredDep,
        category: OptCategoryQ = None,
        borough: Annotated[str | None, Query(max_length=20, pattern=r"^[A-Z ]+$")] = None,
        direction: Annotated[str | None, Query(pattern=r"^(spike|drop)$")] = None,
        start: dt.date | None = None,
        end: dt.date | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, Any]:
        """Detected (unverified) anomalies, newest first."""
        if start and end and end < start:
            raise HTTPException(422, "end must be on or after start")
        return pred.anomalies(category, borough, direction, start, end, limit)

    @app.get("/api/v1/anomalies/series", tags=["predictions"])
    def anomaly_series(
        pred: PredDep,
        category: CategoryQ,
        borough: BoroughQ,
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> dict[str, Any]:
        """Daily actual vs expected counts and anomaly scores for one series (max 400 days)."""
        if start and end and end < start:
            raise HTTPException(422, "end must be on or after start")
        return pred.anomaly_series(category, borough, start, end)

    app.include_router(build_router(lambda request: client_key(request, settings.api_trusted_proxy_hops)))
    return app


app = create_app()
