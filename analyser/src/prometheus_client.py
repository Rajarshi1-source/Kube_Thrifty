#!/usr/bin/env python3
"""
prometheus_client.py -- the only place the analyser talks to Prometheus.

Four resilience patterns, each here for a specific failure this project has to survive:

  TIMEOUT          Every request carries an explicit deadline. A hung HTTP call in an analysis run
                   holds the per-cluster lock until the watchdog reaps it, so "slow" and "down"
                   must be indistinguishable from the caller's point of view.
  RETRY + JITTER   Exponential backoff with FULL jitter, not fixed sleeps. When Prometheus restarts,
                   every analyser pod fails at the same instant; identical backoff schedules make
                   them retry in lockstep and re-DDoS it. Full jitter spreads the herd.
  CIRCUIT BREAKER  After repeated failures, fail fast instead of queueing work against a dead
                   dependency.
  GRACEFUL         DEGRADATION: a query that cannot be answered returns None, which propagates as
                   "not observed". It never returns 0.0. This is the single most important line in
                   the file: a zero would read as "no throttling, no pressure, safe to shrink".

Retries are safe here because PromQL reads are idempotent. Nothing in this module writes.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from circuitbreaker import CircuitBreakerError, circuit
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .config import get_settings

log = logging.getLogger(__name__)

# Exceptions worth retrying: transport-level problems and 5xx. A 400 (bad PromQL) is a bug in our
# query, and retrying it just burns the budget three times before reporting the same error.
RETRYABLE = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)


class PrometheusUnavailable(RuntimeError):
    """Raised when Prometheus could not be reached after retries, or the breaker is open."""


class PrometheusClient:
    """Thin, resilient PromQL client. Read-only by construction."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        s = get_settings()
        self.base_url = (base_url or s.prometheus_url).rstrip("/")
        self.timeout = timeout_seconds or s.prometheus_timeout_seconds
        self.max_retries = s.prometheus_max_retries if max_retries is None else max_retries
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=min(5.0, self.timeout)),
            # Bulkhead: a bounded pool means a slow Prometheus cannot consume every socket the
            # process owns and starve the database and Redis calls in the same run.
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"User-Agent": "kubethrifty-analyser/3.0"},
        )

    def __enter__(self) -> PrometheusClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- transport ---------------------------------------------------------------------------
    @circuit(
        failure_threshold=5,
        recovery_timeout=60,
        expected_exception=(httpx.HTTPError, PrometheusUnavailable),
    )
    @retry(
        retry=retry_if_exception_type(RETRYABLE),
        stop=stop_after_attempt(3),
        # Full jitter: sleep is drawn from [0, base * 2**attempt], capped. Not a fixed schedule.
        wait=wait_exponential_jitter(initial=0.5, max=8.0, jitter=2.0),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, Any]) -> dict:
        resp = self._client.get(path, params=params)
        if resp.status_code >= 500:
            # Retryable: a 503 during a Prometheus restart is the common case.
            raise httpx.RemoteProtocolError(f"prometheus {resp.status_code}", request=resp.request)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise PrometheusUnavailable(payload.get("error", "prometheus returned status!=success"))
        return payload["data"]

    # -- queries -----------------------------------------------------------------------------
    def instant(self, promql: str) -> list[dict] | None:
        """Instant query. Returns the raw result list, or None if Prometheus is unreachable.

        None means NOT OBSERVED. An empty list means "asked successfully, no series matched" --
        also not zero, but a different fact, and the caller may care about the difference.
        """
        try:
            data = self._get("/api/v1/query", {"query": promql})
        except CircuitBreakerError:
            log.warning("prometheus circuit open; skipping query: %s", promql[:120])
            return None
        except (httpx.HTTPError, PrometheusUnavailable) as e:
            log.warning("prometheus instant query failed (%s): %s", type(e).__name__, promql[:120])
            return None
        return data.get("result", [])

    def range(self, promql: str, start: str, end: str, step: str = "60s") -> list[dict] | None:
        """Range query over [start, end]. Same None-means-unknown contract as `instant`."""
        try:
            data = self._get(
                "/api/v1/query_range",
                {"query": promql, "start": start, "end": end, "step": step},
            )
        except CircuitBreakerError:
            log.warning("prometheus circuit open; skipping range query: %s", promql[:120])
            return None
        except (httpx.HTTPError, PrometheusUnavailable) as e:
            log.warning("prometheus range query failed (%s): %s", type(e).__name__, promql[:120])
            return None
        return data.get("result", [])

    def scalar(self, promql: str) -> float | None:
        """First sample of an instant query as a float, or None.

        Callers must treat None as "not observed" and must NOT coalesce it to 0.0. Every threshold
        comparison in sizing.py is written as `x is not None and x > threshold` for this reason.
        """
        result = self.instant(promql)
        if not result:
            return None
        try:
            return float(result[0]["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            log.warning("unparseable scalar from promql: %s", promql[:120])
            return None

    def healthy(self) -> bool:
        try:
            resp = self._client.get("/-/healthy", timeout=5.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
