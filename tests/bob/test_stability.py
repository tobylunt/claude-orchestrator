"""KS-statistic adaptive stability detector for multi-agent debate."""
import pytest

from claude_orchestrator.bob.orchestra.stability import (
    StabilityDetector,
    StabilityVerdict,
)


def test_detector_starts_unstable():
    d = StabilityDetector(consecutive_rounds=2)
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE


def test_detector_terminates_on_consistent_consensus():
    """With single-element confidences that hover within tolerance, terminate."""
    d = StabilityDetector(
        consecutive_rounds=2,
        consensus_tolerance=0.1,
    )
    # First round: no comparison yet.
    assert d.update([0.95]) == StabilityVerdict.UNSTABLE
    # Second round: 0.97 within ±0.1 of 0.95 — within tolerance, +1 stable.
    # consecutive_rounds=2 means we need 2 in a row, so still UNSTABLE here.
    assert d.update([0.97]) == StabilityVerdict.UNSTABLE
    # Third round: 0.95 still within tolerance — 2 consecutive stable rounds → STABLE.
    assert d.update([0.95]) == StabilityVerdict.STABLE


def test_detector_resets_on_big_jump():
    """A large jump in confidence resets the consecutive counter."""
    d = StabilityDetector(
        consecutive_rounds=2,
        consensus_tolerance=0.1,
    )
    d.update([0.95])  # round 1
    d.update([0.97])  # round 2, within tolerance
    # Round 3 jumps to 0.30 — way outside ±0.1 → resets counter.
    assert d.update([0.30]) == StabilityVerdict.UNSTABLE
    assert d.update([0.32]) == StabilityVerdict.UNSTABLE  # only 1 consecutive stable
    assert d.update([0.31]) == StabilityVerdict.STABLE   # 2 consecutive at this level


def test_detector_records_history():
    d = StabilityDetector(consecutive_rounds=2)
    d.update([0.95])
    d.update([0.97])
    assert len(d.history) == 2


def test_detector_ks_fallback_for_multi_element_distributions():
    """When given multi-element distributions, fall back to KS statistic."""
    # With identical multi-element distributions, KS=0 < threshold → stable after N consecutive.
    d = StabilityDetector(
        ks_threshold=0.5,
        consecutive_rounds=2,
        consensus_tolerance=0.01,  # tight tolerance forces KS path
    )
    d.update([1, 0, 1])
    d.update([1, 0, 1])  # KS=0 vs prev, increment stable counter
    assert d.update([1, 0, 1]) == StabilityVerdict.STABLE
