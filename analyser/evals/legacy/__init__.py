"""
Legacy Rev 2 engine, kept deliberately and frozen.

`recommendation.py` is the CPU-only engine that sized BOTH cpu and memory as `P95 * 1.20`. It is
retained unchanged so the Rev 2 -> Rev 3 correction can be diffed and explained: percentile-based
memory sizing passes every float test and still ships an OOMKill.

New work uses `src.sizing`. Nothing in `src/` may import from here.
"""
