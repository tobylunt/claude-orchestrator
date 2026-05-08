"""Adaptive stability detection for multi-agent debate.

Based on Hu et al., "Multi-Agent Debate for LLM Judges with Adaptive
Stability Detection" (arXiv:2510.12697, 2025): a debate terminates when
the judgment-distribution stays similar across N consecutive rounds, as
measured by the Kolmogorov-Smirnov two-sample statistic.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from scipy.stats import ks_2samp  # type: ignore[import-not-found]

from claude_orchestrator.models import StrEnum


class StabilityVerdict(StrEnum):
    UNSTABLE = "unstable"
    STABLE = "stable"


@dataclass
class StabilityDetector:
    ks_threshold: float = 0.05
    consecutive_rounds: int = 2
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
        ks_stat, _p = ks_2samp(prev, distribution)
        self.history.append(list(distribution))

        if ks_stat < self.ks_threshold:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if self._consecutive >= self.consecutive_rounds:
            return StabilityVerdict.STABLE
        return StabilityVerdict.UNSTABLE
