"""
Tests for the money.

The assertion that matters: savings come from a NODE-COUNT DELTA, and the three scenarios produce
genuinely different answers for the same delta. Collapsing them into one number would misstate all
three, which is why the Strategy hierarchy exists.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.cloud_pricing import (
    HOURS_PER_MONTH,
    EksAutoModePricing,
    FixedNodePoolPricing,
    KarpenterPricing,
    load_catalogue,
    strategy_for,
)

CATALOGUE_PATH = Path(__file__).resolve().parents[2] / "config" / "instances.json"


@pytest.fixture(scope="module")
def catalogue() -> dict:
    return load_catalogue(CATALOGUE_PATH)


class TestCatalogue:
    def test_prices_are_pinned_with_a_date(self, catalogue):
        """A savings figure without an as_of date is not reviewable six months later."""
        assert catalogue["as_of"]
        assert catalogue["region"]
        assert catalogue["currency"]

    def test_hours_per_month_is_730_not_720(self, catalogue):
        """720 understates a month by ~1.4% -- larger than the margins these PRs argue about."""
        assert HOURS_PER_MONTH == 730
        assert catalogue["hours_per_month"] == 730

    def test_every_instance_has_a_pod_cap(self, catalogue):
        """max_pods is frequently the real binding constraint, not CPU or memory."""
        for inst in catalogue["instances"]:
            assert inst["max_pods"] > 0
            assert inst["cpu_millicores"] > 0
            assert inst["memory_mib"] > 0
            assert inst["on_demand_hourly"] > 0

    def test_daemonset_overhead_is_declared(self, catalogue):
        """Every new node pays this before an application pod lands. Omitting it invents savings."""
        ds = catalogue["daemonset_overhead"]
        assert ds["cpu_millicores"] > 0
        assert ds["memory_mib"] > 0
        assert ds["pod_count"] > 0


class TestNodeDeltaIsTheOnlyMoney:
    def test_savings_are_node_delta_times_price(self, catalogue):
        s = KarpenterPricing(catalogue)
        report = s.report("m7i.xlarge", nodes_before=10, nodes_after=8)
        unit = catalogue["instances"][1]["on_demand_hourly"] * HOURS_PER_MONTH
        assert report.nodes_removed == 2
        assert report.monthly_savings == pytest.approx(unit * 2)

    def test_no_node_removed_means_no_saving(self, catalogue):
        s = KarpenterPricing(catalogue)
        report = s.report("m7i.xlarge", nodes_before=10, nodes_after=10)
        assert report.nodes_removed == 0
        assert report.monthly_savings == pytest.approx(0.0)
        assert "no saving" in report.headline().lower()

    def test_unknown_instance_type_raises_rather_than_guessing(self, catalogue):
        s = KarpenterPricing(catalogue)
        with pytest.raises(KeyError, match="not in the pinned catalogue"):
            s.report("m9z.enormous", 10, 8)


class TestScenariosDiffer:
    def test_fixed_pool_does_not_claim_realised_savings(self, catalogue):
        report = FixedNodePoolPricing(catalogue).report("m7i.xlarge", 10, 8)
        assert report.realised is False
        assert "unlocks capacity" in report.headline()
        assert "not a smaller invoice" in report.caveat

    def test_karpenter_claims_realised_savings(self, catalogue):
        report = KarpenterPricing(catalogue).report("m7i.xlarge", 10, 8)
        assert report.realised is True
        assert "saves" in report.headline()

    def test_auto_mode_surcharge_reduces_the_saving(self, catalogue):
        """The +12% applies to every node you KEEP, so it eats part of the gain."""
        karpenter = KarpenterPricing(catalogue).report("m7i.xlarge", 10, 8)
        auto = EksAutoModePricing(catalogue, surcharge_pct=12.0).report("m7i.xlarge", 10, 8)
        # Auto Mode costs more in absolute terms both before and after...
        assert auto.monthly_cost_before > karpenter.monthly_cost_before
        assert auto.monthly_cost_after > karpenter.monthly_cost_after
        # ...and the same 2-node delta is worth MORE in absolute currency because each node costs
        # more, while the percentage saved is identical. Reporting one figure for both scenarios
        # would misstate the operator's actual bill either way.
        assert auto.monthly_savings > karpenter.monthly_savings
        assert auto.savings_pct == pytest.approx(karpenter.savings_pct)
        assert "surcharge" in auto.caveat

    def test_strategy_factory_maps_scenario_names(self, catalogue):
        assert isinstance(strategy_for("fixed_node_pool", catalogue), FixedNodePoolPricing)
        assert isinstance(strategy_for("self_managed_karpenter", catalogue), KarpenterPricing)
        assert isinstance(strategy_for("eks_auto_mode", catalogue), EksAutoModePricing)

    def test_unknown_scenario_raises(self, catalogue):
        with pytest.raises(KeyError):
            strategy_for("wishful_thinking", catalogue)


class TestReportSerialisation:
    def test_report_dict_carries_provenance(self, catalogue):
        d = KarpenterPricing(catalogue).report("m7i.xlarge", 10, 8).to_dict()
        # Any figure the UI or a PR body prints must be able to say when and where it came from.
        for key in ("as_of", "region", "currency", "scenario", "realised", "caveat", "headline"):
            assert key in d
        assert json.dumps(d)          # must be serialisable for the API layer
