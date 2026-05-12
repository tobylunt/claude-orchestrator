"""Cost-aware premium review policy for Orchestra."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

from claude_orchestrator.models import Feature


PremiumPolicyMode = Literal["adaptive", "always", "never"]

_VALID_MODES = ("adaptive", "always", "never")
_DEFAULT_RISK_FRAGMENTS = (
    "auth",
    "oauth",
    "login",
    "permission",
    "security",
    "secret",
    "token",
    "payment",
    "billing",
    "crypto",
    "sandbox",
    "docker",
    ".github/workflows",
    "deploy",
)


@dataclass(frozen=True)
class PremiumReviewDecision:
    escalate: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewPolicy:
    """Decide when to spend premium Opus/GPT-5.5 review calls."""

    mode: PremiumPolicyMode = "adaptive"
    min_confidence: float = 0.85
    large_diff_bytes: int = 12_000
    large_file_count: int = 8
    risk_fragments: tuple[str, ...] = _DEFAULT_RISK_FRAGMENTS

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ReviewPolicy":
        if env is None:
            env = os.environ
        mode = env.get("BOB_ORCHESTRA_PREMIUM_POLICY", "adaptive").strip().lower()
        if mode not in _VALID_MODES:
            raise ValueError(
                f"BOB_ORCHESTRA_PREMIUM_POLICY must be one of {_VALID_MODES}; "
                f"got {mode!r}"
            )
        raw_fragments = env.get("BOB_ORCHESTRA_PREMIUM_RISK_FRAGMENTS")
        fragments = (
            tuple(f.strip().lower() for f in raw_fragments.split(",") if f.strip())
            if raw_fragments
            else _DEFAULT_RISK_FRAGMENTS
        )
        return cls(
            mode=mode,  # type: ignore[arg-type]
            min_confidence=float(
                env.get("BOB_ORCHESTRA_PREMIUM_MIN_CONFIDENCE", "0.85")
            ),
            large_diff_bytes=int(
                env.get("BOB_ORCHESTRA_PREMIUM_DIFF_BYTES", "12000")
            ),
            large_file_count=int(
                env.get("BOB_ORCHESTRA_PREMIUM_FILE_COUNT", "8")
            ),
            risk_fragments=fragments,
        )

    def decide(
        self,
        *,
        feature: Feature,
        diff: str,
        rounds: list[dict[str, Any]],
        decision: str,
        confidence: float,
    ) -> PremiumReviewDecision:
        if self.mode == "never":
            return PremiumReviewDecision(False, ())
        if self.mode == "always":
            return PremiumReviewDecision(True, ("policy=always",))

        reasons: list[str] = []
        if decision not in ("approve", "reject"):
            reasons.append(f"decision={decision}")
        if confidence < self.min_confidence:
            reasons.append(f"confidence<{self.min_confidence:g}")

        if self._last_round_disagrees(rounds):
            reasons.append("reviewer_disagreement")

        diff_bytes = len(diff.encode("utf-8"))
        if diff_bytes >= self.large_diff_bytes:
            reasons.append(f"diff_bytes>={self.large_diff_bytes}")

        changed_files = changed_files_from_diff(diff)
        if len(changed_files) >= self.large_file_count:
            reasons.append(f"files>={self.large_file_count}")

        risk_hit = self._risk_hit(feature=feature, changed_files=changed_files)
        if risk_hit:
            reasons.append(f"risk_surface={risk_hit}")

        return PremiumReviewDecision(bool(reasons), tuple(reasons))

    def _last_round_disagrees(self, rounds: list[dict[str, Any]]) -> bool:
        if not rounds:
            return False
        last = rounds[-1]
        claude = last.get("claude_decision")
        codex = last.get("codex_decision")
        if claude not in ("approve", "reject") or codex not in ("approve", "reject"):
            return False
        return claude != codex

    def _risk_hit(self, *, feature: Feature, changed_files: list[str]) -> str | None:
        haystack = " ".join(
            [
                feature.name,
                feature.description,
                " ".join(feature.verification_plan.success_criteria),
                " ".join(changed_files),
            ]
        ).lower()
        for fragment in self.risk_fragments:
            if fragment and fragment in haystack:
                return fragment
        return None


def changed_files_from_diff(diff: str) -> list[str]:
    """Extract changed file paths from a unified git diff."""
    files: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        path: str | None = None
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                path = _strip_diff_prefix(parts[3])
        elif line.startswith("+++ "):
            candidate = line[4:].strip()
            if candidate != "/dev/null":
                path = _strip_diff_prefix(candidate)
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def _strip_diff_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path
