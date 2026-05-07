"""Orchestra stub for M1.

M1 ships a single-judge LLM-as-judge review. The judge sees the feature
spec and the diff produced by McLoop, and returns approve|reject|abstain
with confidence and reasoning. M2 replaces this with AutoGen GroupChat
(Claude defending, Codex attacking, Opus judging) and KS-stability
termination.

The Verdict schema is the same in both M1 and M2 — only the
implementation behind .review() changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import Feature, Verdict


class SingleJudge(Protocol):
    def judge_diff(self, feature: Feature, diff: str) -> dict: ...


class OrchestraStub:
    def __init__(self, judge: SingleJudge) -> None:
        self._judge = judge

    def review(
        self,
        feature: Feature,
        diff: str,
        debate_log_dir: Path,
    ) -> Verdict:
        result = self._judge.judge_diff(feature, diff)
        decision = result.get("decision", "abstain")
        confidence = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))

        debate_log_path = debate_log_dir / "debate.json"
        debate_log_dir.mkdir(parents=True, exist_ok=True)
        debate_log_path.write_text(json.dumps({
            "feature_id": feature.id,
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "stub": True,
        }, indent=2))

        return Verdict(
            feature_id=feature.id,
            decision=decision,
            confidence=confidence,
            debate_log_path=debate_log_path,
            judge_reasoning=reasoning,
        )
