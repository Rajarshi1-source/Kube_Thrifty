"""
Tests for the regression judge.

These are the tests that matter most in the project, because `evaluate()` is what stands between a
rehearsal saying "safe" and an operator believing it. The asymmetry is deliberate and is asserted
here: quick to say regressed, slow to say safe.
"""
from __future__ import annotations

from src.verification.signals import (
    PSI_FULL_CEILING,
    THROTTLE_RATIO_DELTA,
    THROTTLE_RATIO_FLOOR,
    Window,
    evaluate,
)


class TestThrottleRatio:
    def test_ratio_is_computed_from_counters(self):
        w = Window(cfs_periods=1000, throttled_periods=50)
        assert w.throttle_ratio == 0.05

    def test_missing_denominator_is_none_not_zero(self):
        """A container with no recorded CFS periods was NOT 'never throttled'."""
        assert Window(throttled_periods=50).throttle_ratio is None

    def test_zero_denominator_is_none_not_zero(self):
        assert Window(cfs_periods=0, throttled_periods=0).throttle_ratio is None

    def test_missing_numerator_is_none(self):
        assert Window(cfs_periods=1000).throttle_ratio is None


class TestRawCounterTrap:
    def test_longer_window_with_same_ratio_is_not_a_regression(self):
        """The trap this whole module exists to avoid.

        4,000 throttled periods 'after' vs 1,200 'before' looks like a 3x regression if you compare
        raw counters. Both windows throttled at exactly 20%; nothing got worse -- the second window
        was simply longer. A counter-comparing tool reports a regression here every time.
        """
        before = Window(cfs_periods=6_000, throttled_periods=1_200)
        after = Window(cfs_periods=20_000, throttled_periods=4_000)
        assert before.throttle_ratio == after.throttle_ratio == 0.20
        assert not evaluate(before, after).regressed


class TestThrottleRegression:
    def test_meaningful_rise_to_meaningful_level_regresses(self):
        before = Window(cfs_periods=1000, throttled_periods=10)     # 1%
        after = Window(cfs_periods=1000, throttled_periods=200)     # 20%
        v = evaluate(before, after)
        assert v.regressed
        assert any("throttle ratio" in r for r in v.reasons)

    def test_rise_below_the_noise_floor_does_not_regress(self):
        """0.1% -> 1.9% clears nothing meaningful even though it is a 19x increase."""
        before = Window(cfs_periods=10_000, throttled_periods=10)    # 0.1%
        after = Window(cfs_periods=10_000, throttled_periods=190)    # 1.9%, under the 2% floor
        assert after.throttle_ratio < THROTTLE_RATIO_FLOOR
        assert not evaluate(before, after).regressed

    def test_high_but_unchanged_throttling_does_not_regress(self):
        """Pre-existing throttling is not caused by this change."""
        before = Window(cfs_periods=1000, throttled_periods=300)
        after = Window(cfs_periods=1000, throttled_periods=310)
        assert after.throttle_ratio > THROTTLE_RATIO_FLOOR
        assert not evaluate(before, after).regressed

    def test_delta_exactly_at_gate_does_not_regress(self):
        before = Window(cfs_periods=1000, throttled_periods=100)     # 10%
        after = Window(cfs_periods=1000, throttled_periods=150)      # 15%, delta == gate
        assert abs((after.throttle_ratio - before.throttle_ratio) - THROTTLE_RATIO_DELTA) < 1e-9
        assert not evaluate(before, after).regressed


class TestObservedDeath:
    def test_any_oom_regresses(self):
        v = evaluate(Window(), Window(oom_events=1))
        assert v.regressed

    def test_any_restart_regresses(self):
        """RESTART_TOLERANCE is 0. There is no successful right-sizing that restarted the pod."""
        v = evaluate(Window(), Window(restarts=1))
        assert v.regressed

    def test_zero_restarts_is_fine(self):
        assert not evaluate(Window(), Window(restarts=0)).regressed

    def test_unobserved_oom_does_not_regress_but_is_recorded(self):
        """None means not observed. It must not be treated as a failure OR as an all-clear."""
        v = evaluate(Window(), Window(oom_events=None))
        assert not v.regressed
        assert v.signals["oom_events_after"] is None


class TestPsi:
    def test_full_stall_above_ceiling_regresses(self):
        v = evaluate(Window(), Window(psi_mem_full_ratio=PSI_FULL_CEILING + 0.01))
        assert v.regressed
        assert any("PSI memory" in r for r in v.reasons)

    def test_stall_below_ceiling_is_fine(self):
        assert not evaluate(Window(), Window(psi_mem_full_ratio=0.01)).regressed

    def test_absent_psi_is_not_an_all_clear(self):
        """No PSI must never read as 'no pressure'; it is simply unknown."""
        v = evaluate(Window(), Window(psi_mem_full_ratio=None))
        assert not v.regressed
        assert v.signals["psi_memory_full_after"] is None


class TestSlo:
    def test_burn_rate_above_one_regresses(self):
        assert evaluate(Window(), Window(), slo_burn_rate=1.5).regressed

    def test_burn_rate_below_one_is_fine(self):
        assert not evaluate(Window(), Window(), slo_burn_rate=0.4).regressed


class TestIncomparableWindows:
    def test_missing_throttle_data_is_flagged_not_failed(self):
        v = evaluate(Window(), Window())
        assert not v.regressed
        assert v.signals["throttle_comparable"] is False

    def test_verdict_outcome_string(self):
        assert evaluate(Window(), Window()).outcome == "safe"
        assert evaluate(Window(), Window(oom_events=2)).outcome == "regressed"
