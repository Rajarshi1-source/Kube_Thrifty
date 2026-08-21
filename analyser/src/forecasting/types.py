#!/usr/bin/env python3
"""
forecasting/types.py -- the provider-agnostic Forecaster contract.

The sizing engine imports THIS module only. Concrete backends (statsforecast, the dependency-free
fallback, a cloud forecaster) live beside it and are selected by `default_forecaster()`. That keeps
`statsforecast` out of the engine's import graph, so a missing native dependency can never break
sizing -- it can only downgrade the forecast to the fallback.

`upper` is the only field the engine consumes: it is used as the predictive FLOOR inside max().
`confident=False` is the contract that tells the caller to fall back to the trailing statistical
floor. That fallback is a product guarantee, not an error path.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

MIN_POINTS = 24 * 14          # need ~2 weeks of hourly data to trust a forecast
WEEK = 24 * 7
Z90 = 1.645                   # one-sided 95th / two-sided 90% normal quantile


@dataclass
class Forecast:
    horizon_hours: int
    point: pd.Series          # predicted mean per future hour
    lower: pd.Series          # lower bound of the (~90%) interval
    upper: pd.Series          # upper bound -- used as the predictive FLOOR by the engine
    model_id: str             # provenance, recorded on every recommendation
    confident: bool           # False -> caller falls back to trailing P95


class Forecaster(Protocol):
    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast: ...
