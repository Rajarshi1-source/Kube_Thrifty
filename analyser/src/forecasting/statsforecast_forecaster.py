#!/usr/bin/env python3
"""
forecasting/statsforecast_forecaster.py -- the production forecaster.

statsforecast AutoETS with weekly seasonality. The import is deliberately inside `forecast()` so
merely importing this module never requires the native dependency; `default_forecaster()` probes for
it and falls back to SeasonalNaiveForecaster when it is absent.

MSTL is the alternative when a series shows both daily and weekly seasonality.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .types import MIN_POINTS, WEEK, Forecast


class StatsForecaster:
    """Production default: statsforecast AutoETS with weekly seasonality. Used if importable."""
    MODEL_ID = "statsforecast:AutoETS"

    def forecast(self, history: pd.Series, horizon_hours: int) -> Forecast:
        from statsforecast import StatsForecast
        from statsforecast.models import AutoETS
        h = history.dropna()
        future_index = pd.RangeIndex(len(h), len(h) + horizon_hours)
        if len(h) < MIN_POINTS:
            flat = pd.Series(np.repeat(h.to_numpy()[-1] if len(h) else 0.0, horizon_hours),
                             index=future_index)
            return Forecast(horizon_hours, flat, flat, flat, self.MODEL_ID, confident=False)
        df = h.reset_index(drop=True).reset_index()
        df.columns = ["ds", "y"]
        df["unique_id"] = "pod"
        sf = StatsForecast(models=[AutoETS(season_length=WEEK)], freq=1)
        sf.fit(df)
        fc = sf.predict(h=horizon_hours, level=[90])
        return Forecast(
            horizon_hours,
            pd.Series(fc["AutoETS"].to_numpy(), index=future_index),
            pd.Series(fc["AutoETS-lo-90"].to_numpy(), index=future_index),
            pd.Series(fc["AutoETS-hi-90"].to_numpy(), index=future_index),
            self.MODEL_ID, confident=True,
        )
