#!/usr/bin/env python3
"""
config.py -- every tunable in one typed place, loaded from the environment.

Two rules that shaped this file:

  * The SAFETY constants are not configurable. Margins, floors, thresholds and tolerances live in
    `sizing.py` and `verification/signals.py` as module constants, deliberately: a threshold that
    can be relaxed by an environment variable will eventually be relaxed by an environment
    variable, and the eval gates grade the constants, not whatever a Deployment happened to set.
    What lives here is plumbing -- URLs, credentials, timeouts, feature switches.

  * Every default is the SAFE one. Rehearsals off, auto-PR off, dry run on. A misconfigured
    analyser must degrade to "computes and explains but changes nothing", never to "acts".
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_catalogue() -> Path:
    """
    Locate `config/instances.json` by walking up from this module.

    This file is `<root>/analyser/src/config.py`, so the repo root is two parents up. Walking rather
    than hard-coding `parents[2]` keeps it working under the container layout too, where the code
    lives at `/app/src` and the catalogue at `/app/config`.
    """
    here = Path(__file__).resolve()
    for candidate in here.parents:
        found = candidate / "config" / "instances.json"
        if found.exists():
            return found
    # Nothing found: return the conventional location so the error message names a real path.
    return here.parents[2] / "config" / "instances.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False,
    )

    # --- identity -----------------------------------------------------------------------------
    cluster_name: str = Field(default="kubethrifty-demo")

    # --- Prometheus ---------------------------------------------------------------------------
    prometheus_url: str = Field(default="http://localhost:9090")
    # Explicit and non-negotiable: an HTTP call with no timeout is how one slow dependency becomes
    # a hung analysis run that holds the cluster lock until the watchdog reaps it.
    prometheus_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    prometheus_max_retries: int = Field(default=3, ge=0, le=6)
    # Circuit breaker: after this many consecutive failures, stop asking for a while.
    prometheus_breaker_failures: int = Field(default=5, ge=1)
    prometheus_breaker_reset_seconds: int = Field(default=60, ge=5)

    # --- persistence --------------------------------------------------------------------------
    # Port 5433, matching the compose host binding. 5432 is deliberately avoided as a default: on a
    # machine with a native PostgreSQL it would connect successfully to entirely the wrong database,
    # which fails far later and far more confusingly than a refused connection.
    database_url: SecretStr = Field(
        default=SecretStr("postgresql+psycopg://kubethrifty:kubethrifty_dev_only@localhost:5433/kubethrifty")
    )
    redis_url: str = Field(default="redis://localhost:6379/0")

    # --- pricing ------------------------------------------------------------------------------
    # Anchored to the repo root, NOT the working directory. `python -m src.main` is run from
    # `analyser/`, so a plain relative "config/instances.json" resolves to
    # `analyser/config/instances.json` and the run silently loses its prices -- reporting
    # percentages only, with the reason buried in a warning. An explicit INSTANCE_CATALOGUE (which
    # the container image sets to /app/config/instances.json) still overrides this.
    instance_catalogue: str = Field(default_factory=lambda: str(_default_catalogue()))
    pricing_scenario: str = Field(default="fixed_node_pool")

    # --- GitHub -------------------------------------------------------------------------------
    # Absent by default. Without a token the analyser computes, persists and explains, but opens
    # no PR -- the correct behaviour for a dev box or a misconfigured deploy.
    github_token: SecretStr | None = Field(default=None)
    github_repo: str | None = Field(default=None)
    github_base_branch: str = Field(default="main")

    # --- behaviour switches -------------------------------------------------------------------
    # Off by default. A durable resource change reaches a cluster only through a merged PR, and a
    # PR only gets opened when somebody deliberately turned this on.
    auto_pr: bool = Field(default=False)
    # Rehearsals mutate a live pod (via patch pods/resize, then always revert). Opt-in, globally.
    rehearsal_enabled: bool = Field(default=False)
    rehearsal_max_concurrent: int = Field(default=3, ge=1, le=10)
    rehearsal_observe_seconds: int = Field(default=1800, ge=300)
    rehearsal_baseline_seconds: int = Field(default=300, ge=60)
    rehearsal_resize_timeout_seconds: int = Field(default=120, ge=30)

    # Blast-radius cap: refuse to act on more than this fraction of a namespace in one run.
    max_workloads_per_run: int = Field(default=50, ge=1)

    log_level: str = Field(default="INFO")

    @field_validator("prometheus_url", "redis_url")
    @classmethod
    def _no_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("database_url")
    @classmethod
    def _force_psycopg3_driver(cls, v: SecretStr) -> SecretStr:
        """
        Normalise a plain `postgresql://` URL to `postgresql+psycopg://`.

        SQLAlchemy resolves a bare `postgresql://` scheme to the psycopg2 dialect, which this project
        does not install -- so the run fails with `No module named 'psycopg2'`. That error is
        actively misleading: it reads like a missing dependency when the real cause is a URL scheme,
        and the failure path here is silent degradation (the analyser logs a warning and continues
        WITHOUT persistence, so a scheduled run computes recommendations and stores none of them).
        Normalising is safer than documenting, because `postgresql://` is the standard form that
        every Secret, connection string and copy-paste will use.
        """
        raw = v.get_secret_value()
        if raw.startswith("postgresql://"):
            return SecretStr(raw.replace("postgresql://", "postgresql+psycopg://", 1))
        if raw.startswith("postgres://"):
            return SecretStr(raw.replace("postgres://", "postgresql+psycopg://", 1))
        return v

    @field_validator("log_level")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @property
    def can_open_pr(self) -> bool:
        """Auto-PR requires the switch AND the credentials. Half-configured means no PR."""
        return bool(self.auto_pr and self.github_token and self.github_repo)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
