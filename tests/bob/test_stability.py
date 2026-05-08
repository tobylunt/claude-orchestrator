"""KS-statistic adaptive stability detector for multi-agent debate."""
import pytest

from claude_orchestrator.bob.orchestra.stability import (
    StabilityDetector,
    StabilityVerdict,
)


def test_detector_starts_unstable():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE


def test_detector_returns_stable_after_n_consecutive():
    d = StabilityDetector(ks_threshold=0.5, consecutive_rounds=2)
    # Identical rounds => KS=0 => stable on round 3.
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE  # round 1, no comparison yet
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE  # round 2, KS=0 once
    assert d.update([1, 0, 1]) == StabilityVerdict.STABLE    # round 3, KS=0 twice


def test_detector_resets_consecutive_on_jump():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    d.update([1, 0, 1])  # round 1
    d.update([1, 0, 1])  # round 2: KS=0
    # Next round wildly different — consecutive resets.
    d.update([0, 1, 0])  # round 3: KS=1 => unstable
    # round 4 same as round 3
    assert d.update([0, 1, 0]) == StabilityVerdict.UNSTABLE  # only 1 consecutive stable
    assert d.update([0, 1, 0]) == StabilityVerdict.STABLE


def test_detector_records_history():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    d.update([1, 0])
    d.update([1, 0])
    assert len(d.history) == 2
