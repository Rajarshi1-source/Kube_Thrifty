"""
KubeThrifty analyser -- the statistical core.

Queries Prometheus, aggregates with pandas, sizes resources with hard safety floors, forecasts so it
never shrinks a service that is trending up, consumes rehearsal evidence where it exists, prices the
waste as a node delta, persists to TimescaleDB, and opens auto-PRs.

The cardinal architecture rule: `sizing` is pure, deterministic, unit-tested Python. Everything
clever -- the forecast, the rehearsal, the pressure signals -- is a FLOOR that can only raise a
request, never lower it.
"""
