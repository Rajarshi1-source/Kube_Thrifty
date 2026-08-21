"""
forecasting -- provider-agnostic demand forecasting behind a single adapter.

The forecast contributes exactly one thing to sizing: `Forecast.upper`, used as a FLOOR inside
max(). It can only ever raise a request. When the backend is unavailable or the history is too
short, `confident=False` sends the caller back to the trailing statistical floor.
"""
from .seasonal_naive import SeasonalNaiveForecaster
from .statsforecast_forecaster import StatsForecaster
from .types import MIN_POINTS, WEEK, Z90, Forecast, Forecaster

__all__ = [
    "MIN_POINTS", "WEEK", "Z90", "Forecast", "Forecaster",
    "SeasonalNaiveForecaster", "StatsForecaster", "default_forecaster",
]


def default_forecaster() -> Forecaster:
    """Use statsforecast if installed; otherwise the dependency-free fallback."""
    try:
        import statsforecast  # noqa: F401
        return StatsForecaster()
    except Exception:
        return SeasonalNaiveForecaster()
