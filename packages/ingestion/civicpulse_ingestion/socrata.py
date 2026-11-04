"""Minimal, robust Socrata (SODA) client.

Behaviour verified against the live API on 2026-11-03:
* ``$limit`` is capped at 50,000 on the 2.0 resource endpoint.
* ``$order=:id`` + ``$where=... AND :id > '<last>'`` gives stable keyset pagination.
  ``:id`` values follow Socrata's internal order, NOT string order, so the client never
  compares them locally — it only echoes the last value back to the server.
* Throttling is signalled with HTTP 429 (``X-App-Token`` raises limits).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from civicpulse_shared.config import HttpConfig, SourceConfig
from civicpulse_shared.logging import log

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SocrataError(RuntimeError):
    """Non-retryable error, or retries exhausted."""

    def __init__(self, message: str, *, status_code: int | None = None, attempts: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


def soql_quote(value: str) -> str:
    """Quote a string literal for SoQL (single quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True)
class PageResult:
    records: list[dict[str, Any]]
    url: str
    attempts: int
    elapsed_s: float


class SocrataClient:
    def __init__(
        self,
        source: SourceConfig,
        http: HttpConfig,
        *,
        app_token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.source = source
        self.http = http
        self._sleep = sleep
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        headers = {"Accept": "application/json", "User-Agent": "civicpulse-ai/0.1 (portfolio project)"}
        if app_token:
            headers["X-App-Token"] = app_token
        self._client = httpx.Client(headers=headers, timeout=httpx.Timeout(http.timeout_seconds), transport=transport)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SocrataClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- retry machinery -------------------------------------------------
    def backoff_seconds(self, attempt: int, retry_after: str | None = None) -> float:
        """Exponential backoff with full jitter; honours a numeric Retry-After header."""
        if retry_after:
            try:
                return min(float(retry_after), self.http.backoff_max_seconds)
            except ValueError:
                pass
        cap = min(self.http.backoff_max_seconds, self.http.backoff_base_seconds * (2**attempt))
        return self._rng.uniform(0, cap)

    def _get(self, url: str, params: dict[str, str] | None = None) -> tuple[Any, int, float]:
        attempts = 0
        start = time.monotonic()
        last_error = ""
        last_status: int | None = None
        while True:
            attempts += 1
            try:
                resp = self._client.get(url, params=params)
                last_status = resp.status_code
                if resp.status_code == 200:
                    return resp.json(), attempts, time.monotonic() - start
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                if resp.status_code not in RETRYABLE_STATUS:
                    raise SocrataError(last_error, status_code=resp.status_code, attempts=attempts)
                retry_after = resp.headers.get("Retry-After")
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
                retry_after = None
            if attempts > self.http.max_retries:
                raise SocrataError(
                    f"retries exhausted after {attempts} attempts: {last_error}",
                    status_code=last_status,
                    attempts=attempts,
                )
            delay = self.backoff_seconds(attempts - 1, retry_after)
            log(
                logger,
                logging.WARNING,
                "socrata request failed; retrying",
                attempt=attempts,
                delay_s=round(delay, 2),
                error=last_error[:200],
            )
            self._sleep(delay)

    # -- public API -------------------------------------------------------
    def fetch_page(self, where: str, *, after_id: str | None, limit: int) -> PageResult:
        clause = where if after_id is None else f"({where}) AND :id > {soql_quote(after_id)}"
        params = {
            "$select": ",".join(self.source.system_fields + self.source.fields),
            "$where": clause,
            "$order": ":id",
            "$limit": str(limit),
        }
        data, attempts, elapsed = self._get(self.source.resource_url, params)
        if not isinstance(data, list):
            raise SocrataError(f"unexpected payload type {type(data).__name__}")
        return PageResult(records=data, url=self.source.resource_url, attempts=attempts, elapsed_s=elapsed)

    def count(self, where: str) -> int:
        """Server-side row count for a filter (used to verify that an extraction is complete)."""
        data, _, _ = self._get(self.source.resource_url, {"$select": "count(*) AS n", "$where": where})
        if not isinstance(data, list) or not data or "n" not in data[0]:
            raise SocrataError("unexpected count payload")
        return int(data[0]["n"])

    def fetch_metadata(self) -> dict[str, Any]:
        data, _, _ = self._get(self.source.metadata_url)
        if not isinstance(data, dict):
            raise SocrataError("unexpected metadata payload")
        return data
