#!/usr/bin/env python3
"""
engine.py -- the SQLAlchemy engine, and nothing else.

Core only, no ORM. The queries here are hypertable inserts and time-bucketed aggregates against
TimescaleDB-specific SQL (`time_bucket`, `approx_percentile`, continuous aggregates). An ORM would
add a mapping layer over statements that have to be hand-written anyway, and would obscure exactly
the part worth reading.

`pool_pre_ping` is on because this process is a KEDA-scaled job that sits idle at zero replicas and
then wakes up: its first connection is frequently one the database closed hours ago.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from sqlalchemy import Engine, create_engine

log = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def get_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        # Small: the analyser is one process doing sequential work, not a web server.
        pool_size=5,
        max_overflow=2,
        pool_pre_ping=True,
        # Recycle before any sensible server-side idle timeout can close the connection under us.
        pool_recycle=1800,
        # A hung INSERT would hold the run lock until the watchdog reaps it.
        connect_args={"connect_timeout": 10},
        future=True,
    )
