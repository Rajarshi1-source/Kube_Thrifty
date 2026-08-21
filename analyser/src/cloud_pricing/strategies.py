#!/usr/bin/env python3
"""
cloud_pricing/strategies.py -- node-count deltas into money, three ways.

THE RULE THIS FILE EXISTS TO ENFORCE:

    savings = (nodes_before - nodes_after) * node_price

Not summed millicores. Not summed gibibytes. Clouds bill per node (or per node-hour); they do not
bill for the difference between what a pod requested and what it used. Reclaiming 400m across
twenty pods saves exactly zero until a whole node becomes empty and goes away. Every "we found
$X of waste" claim that multiplies unused millicores by a per-core price is measuring something
nobody is charged for, and that is the single most common way right-sizing tools overstate results.

Per-pod waste stays a PERCENTAGE. It ranks candidates. It is never printed with a currency symbol.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

# 365 * 24 / 12. Never 720 -- that understates a month by ~1.4%, which is larger than the margin
# most of these recommendations are arguing about.
HOURS_PER_MONTH = 730


@dataclass(frozen=True)
class InstanceType:
    type: str
    cpu_millicores: int
    memory_mib: int
    max_pods: int
    on_demand_hourly: float

    def monthly(self) -> float:
        return self.on_demand_hourly * HOURS_PER_MONTH


@dataclass(frozen=True)
class SavingsReport:
    """The only place in the product where a currency figure is produced."""
    scenario: str
    instance_type: str
    nodes_before: int
    nodes_after: int
    monthly_cost_before: float
    monthly_cost_after: float
    currency: str
    as_of: str
    region: str
    realised: bool
    caveat: str

    @property
    def nodes_removed(self) -> int:
        return self.nodes_before - self.nodes_after

    @property
    def monthly_savings(self) -> float:
        return self.monthly_cost_before - self.monthly_cost_after

    @property
    def savings_pct(self) -> float | None:
        if self.monthly_cost_before <= 0:
            return None
        return self.monthly_savings / self.monthly_cost_before

    def headline(self) -> str:
        """One sentence, honest about whether the money is real."""
        if self.nodes_removed <= 0:
            return "No node can be removed at these sizes; there is no saving to report."
        verb = "saves" if self.realised else "unlocks capacity worth"
        return (
            f"Removing {self.nodes_removed} x {self.instance_type} "
            f"{verb} {self.currency} {self.monthly_savings:,.2f}/month "
            f"({self.scenario}, prices as of {self.as_of}, {self.region})."
        )

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "instance_type": self.instance_type,
            "nodes_before": self.nodes_before,
            "nodes_after": self.nodes_after,
            "nodes_removed": self.nodes_removed,
            "monthly_cost_before": round(self.monthly_cost_before, 2),
            "monthly_cost_after": round(self.monthly_cost_after, 2),
            "monthly_savings": round(self.monthly_savings, 2),
            "savings_pct": self.savings_pct,
            "currency": self.currency,
            "as_of": self.as_of,
            "region": self.region,
            "realised": self.realised,
            "caveat": self.caveat,
            "headline": self.headline(),
        }


class PricingStrategy(ABC):
    """Turns a node-count delta into a SavingsReport."""

    scenario: str = "unknown"
    realises_savings_automatically: bool = False
    surcharge_pct: float = 0.0

    def __init__(self, catalogue: dict) -> None:
        self.catalogue = catalogue
        self.currency = catalogue.get("currency", "USD")
        self.as_of = catalogue.get("as_of", "unknown")
        self.region = catalogue.get("region", "unknown")
        self.instances = {
            i["type"]: InstanceType(
                type=i["type"],
                cpu_millicores=i["cpu_millicores"],
                memory_mib=i["memory_mib"],
                max_pods=i["max_pods"],
                on_demand_hourly=i["on_demand_hourly"],
            )
            for i in catalogue.get("instances", [])
        }

    def _node_monthly(self, instance_type: str) -> float:
        inst = self.instances.get(instance_type)
        if inst is None:
            raise KeyError(
                f"instance type {instance_type!r} is not in the pinned catalogue "
                f"(as_of {self.as_of}). Add it to config/instances.json rather than "
                f"guessing a price at runtime."
            )
        # The surcharge applies per node-hour, so it scales with the nodes you keep.
        return inst.monthly() * (1.0 + self.surcharge_pct / 100.0)

    @property
    @abstractmethod
    def caveat(self) -> str:
        """One sentence stating what the operator still has to do, if anything."""

    def report(self, instance_type: str, nodes_before: int, nodes_after: int) -> SavingsReport:
        unit = self._node_monthly(instance_type)
        return SavingsReport(
            scenario=self.scenario,
            instance_type=instance_type,
            nodes_before=nodes_before,
            nodes_after=nodes_after,
            monthly_cost_before=unit * nodes_before,
            monthly_cost_after=unit * nodes_after,
            currency=self.currency,
            as_of=self.as_of,
            region=self.region,
            realised=self.realises_savings_automatically,
            caveat=self.caveat,
        )


class FixedNodePoolPricing(PricingStrategy):
    scenario = "fixed_node_pool"
    realises_savings_automatically = False

    @property
    def caveat(self) -> str:
        return (
            "Nothing scales in automatically on a fixed node pool. This figure is unlocked "
            "capacity, not a smaller invoice, until somebody removes the node."
        )


class KarpenterPricing(PricingStrategy):
    scenario = "self_managed_karpenter"
    realises_savings_automatically = True

    @property
    def caveat(self) -> str:
        return (
            "Karpenter consolidation (WhenEmptyOrUnderutilized) terminates the drained node "
            "without human action, so this appears on the next invoice."
        )


class EksAutoModePricing(PricingStrategy):
    scenario = "eks_auto_mode"
    realises_savings_automatically = True

    def __init__(self, catalogue: dict, surcharge_pct: float = 12.0) -> None:
        super().__init__(catalogue)
        self.surcharge_pct = surcharge_pct

    @property
    def caveat(self) -> str:
        return (
            f"EKS Auto Mode adds a {self.surcharge_pct:.0f}% management surcharge to every node "
            f"you keep, so the same right-sizing is worth measurably less here than under "
            f"self-managed Karpenter."
        )


def load_catalogue(path: str | Path) -> dict:
    """Load the pinned catalogue. Fails loudly rather than falling back to a guessed price."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"instance catalogue not found at {p}. Prices are pinned in-repo on purpose: an "
            f"analysis run must be reproducible months later, and a pricing-API outage must never "
            f"block a recommendation."
        )
    return json.loads(p.read_text(encoding="utf-8"))


def strategy_for(scenario: str, catalogue: dict) -> PricingStrategy:
    scenarios = catalogue.get("scenarios", {})
    cfg = scenarios.get(scenario)
    if cfg is None:
        raise KeyError(f"unknown pricing scenario {scenario!r}; known: {sorted(scenarios)}")
    if scenario == "eks_auto_mode":
        return EksAutoModePricing(catalogue, surcharge_pct=float(cfg.get("surcharge_pct", 12.0)))
    if scenario == "self_managed_karpenter":
        return KarpenterPricing(catalogue)
    return FixedNodePoolPricing(catalogue)
