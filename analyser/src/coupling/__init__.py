"""HPA-collision and QoS guards: stop right-sizing changes that cost more than they save."""
from __future__ import annotations

from .detect import Coupling, CouplingKind, detect
from .qos import QosCheck, check_transition, classify, guaranteed_limits_for
from .simulate import SimulationResult, Verdict, simulate

__all__ = [
    "Coupling",
    "CouplingKind",
    "QosCheck",
    "SimulationResult",
    "Verdict",
    "check_transition",
    "classify",
    "detect",
    "guaranteed_limits_for",
    "simulate",
]
