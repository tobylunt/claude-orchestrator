"""Adaptive stability detection for multi-agent debate.

Two termination paths:
1. **Consensus tolerance (single-element distributions):** when each round
   contributes a single confidence value (the common case), check if the
   value stayed within ±tolerance of the previous round. N consecutive
   in-tolerance rounds → STABLE.
2. **KS-statistic (multi-element distributions):** when each round
   contributes a vector (e.g., multiple-criteria confidence), use the
   two-sample Kolmogorov-Smirnov test. KS < threshold for N consecutive
   rounds → STABLE.

Based on Hu et al., "Multi-Agent Debate for LLM Judges with Adaptive
Stability Detection" (arXiv:2510.12697, 2025).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from claude_orchestrator.models import StrEnum
from scipy.stats import ks_2samp  # type: ignore[import-not-found]


class StabilityVerdict(StrEnum):
    UNSTABLE = "unstable"
    STABLE = "stable"


@dataclass
class StabilityDetector:
    ks_threshold: float = 0.05
    consecutive_rounds: int = 2
    consensus_tolerance: float = 0.1
    history: list[list[float]] = field(default_factory=list)
    _consecutive: int = 0

    def update(self, distribution: list[float]) -> StabilityVerdict:
        """Add the latest round's judgment distribution; return verdict.

        On the first call there's no comparison; UNSTABLE is returned and
        the round is recorded.
        """
        if not self.history:
            self.history.append(list(distribution))
            return StabilityVerdict.UNSTABLE

        prev = self.history[-1]
        is_stable = self._is_stable_vs(prev, distribution)
        self.history.append(list(distribution))

        if is_stable:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if self._consecutive >= self.consecutive_rounds:
            return StabilityVerdict.STABLE
        return StabilityVerdict.UNSTABLE

    def _is_stable_vs(self, prev: list[float], current: list[float]) -> bool:
        # Single-element on both sides: use tolerance check.
        if len(prev) == 1 and len(current) == 1:
            return abs(prev[0] - current[0]) <= self.consensus_tolerance
        # Otherwise: KS statistic.
        ks_stat, _p = ks_2samp(prev, current)
        return ks_stat < self.ks_threshold
