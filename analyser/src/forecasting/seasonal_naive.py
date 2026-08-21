#!/usr/bin/env python3
"""
forecasting/seasonal_naive.py -- the dependency-free forecaster (numpy only).

This is BOTH the deterministic fallback the product relies on when statsforecast is unavailable or
the series is too short, AND what lets CI score the forecast gate fast without native ML deps.

Linear trend + hour-of-week seasonal profile + empirical residual interval.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .types import MIN_POINTS, WEEK, Z90, Forecast


class SeasonalNaiveForecaster:
    """numpy-only. Captures linear trend + weekly (hour-of-week) seasonality."""
    MODEL_ID = "seasonal-naive:hour-of-week+trend"

    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast:
        y = history.dropna().to_numpy(dtype=float)
        n = len(y)
        future_index = pd.RangeIndex(n, n + horizon_hours)
        if n < MIN_POINTS:
            flat = pd.Series(np.repeat(y[-1] if n else 0.0, horizon_hours), index=future_index)
            return Forecast(horizon_hours, flat, flat, flat, self.MODEL_ID, confident=False)

        t = np.arange(n)
        slope, intercept = np.polyfit(t, y, 1)         # linear trend
        trend = intercept + slope * t
        resid = y - trend
        how = t % WEEK                                  # hour-of-week bucket
        profile = np.array([resid[how == h].mean() if np.any(how == h) else 0.0 for h in range(WEEK)])
        sigma = float((resid - profile[how]).std(ddof=1)) or 1e-6

        ft = np.arange(n, n + horizon_hours)
        fhow = ft % WEEK
        point = intercept + slope * ft + profile[fhow]
        point = np.clip(point, 1e-4, None)
        lower = np.clip(point - Z90 * sigma, 0.0, None)
        upper = point + Z90 * sigma
        return Forecast(
            horizon_hours,
            pd.Series(point, index=future_index),
            pd.Series(lower, index=future_index),
            pd.Series(upper, index=future_index),
            self.MODEL_ID, confident=True,
        )
