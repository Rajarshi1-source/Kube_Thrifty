"""
Closed-loop verification.

`signals.evaluate(before, after)` is the single regression judge in the system. The same function
decides whether a merged right-sizing PR made things worse and whether a Resize Rehearsal is
allowed to call a candidate size safe. Two implementations would eventually disagree, and the day
they disagreed the product would be claiming a rehearsal-verified floor it had never verified.
"""
from .signals import (
    PSI_FULL_CEILING,
    RESTART_TOLERANCE,
    THROTTLE_RATIO_DELTA,
    THROTTLE_RATIO_FLOOR,
    Verdict,
    Window,
    evaluate,
)

__all__ = [
    "PSI_FULL_CEILING", "RESTART_TOLERANCE", "THROTTLE_RATIO_DELTA", "THROTTLE_RATIO_FLOOR",
    "Verdict", "Window", "evaluate",
]
