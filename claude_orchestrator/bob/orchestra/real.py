"""Production Orchestra: AutoGen GroupChat with KS-stability termination."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from claude_orchestrator.bob.observability import span
from claude_orchestrator.bob.orchestra.stability import (
    StabilityDetector,
    StabilityVerdict,
)
from claude_orchestrator.models import Feature, Verdict


class DebateAgent(Protocol):
    def run(self, prompt: str) -> list[dict[str, Any]]: ...


class RealOrchestra:
    def __init__(
        self,
        *,
        claude_agent: DebateAgent,
        codex_agent: DebateAgent,
        judge_agent: DebateAgent,
        max_rounds: int = 5,
        ks_threshold: float = 0.05,
        consecutive_rounds: int = 2,
    ) -> None:
        self.claude = claude_agent
        self.codex = codex_agent
        self.judge = judge_agent
        self.max_rounds = max_rounds
        self.detector = StabilityDetector(
            ks_threshold=ks_threshold,
            consecutive_rounds=consecutive_rounds,
        )

    def review(
        self,
        *,
        feature: Feature,
        diff: str,
        debate_log_dir: Path,
    ) -> Verdict:
        rounds: list[dict[str, Any]] = []
        latest_decision = "abstain"
        latest_confidence = 0.0
        latest_reasoning = ""

        prompt_base = (
            f"Feature: {feature.name}\n"
            f"Description: {feature.description}\n"
            f"Success criteria: {feature.verification_plan.success_criteria}\n"
            f"Diff:\n{diff[:8000]}\n"
        )

        for round_num in range(1, self.max_rounds + 1):
            with span("bob.orchestra.round", attrs={
                "feature_id": feature.id,
                "round": round_num,
            }):
                claude_msgs = self.claude.run(
                    prompt_base + f"\nRound {round_num}: defend or critique."
                )
                codex_msgs = self.codex.run(
                    prompt_base + f"\nRound {round_num}: critique adversarially."
                )
                judge_msgs = self.judge.run(
                    prompt_base
                    + f"\nClaude: {claude_msgs[-1]['content']}\n"
                    + f"Codex: {codex_msgs[-1]['content']}\n"
                    + f"Round {round_num}: synthesize."
                )

                judge_final = judge_msgs[-1]
                decision = judge_final.get("decision", "abstain")
                confidence = float(judge_final.get("confidence", 0.0))

                rounds.append({
                    "round": round_num,
                    "claude": claude_msgs[-1]["content"],
                    "codex": codex_msgs[-1]["content"],
                    "judge": judge_final["content"],
                    "decision": decision,
                    "confidence": confidence,
                })
                latest_decision = decision
                latest_confidence = confidence
                latest_reasoning = judge_final["content"]

                verdict = self.detector.update([confidence])
                if verdict == StabilityVerdict.STABLE:
                    if decision in ("approve", "reject"):
                        break
                    continue

        if latest_decision not in ("approve", "reject"):
            latest_decision = "abstain"

        debate_log_dir.mkdir(parents=True, exist_ok=True)
        debate_log_path = debate_log_dir / "debate.json"
        debate_log_path.write_text(json.dumps({
            "feature_id": feature.id,
            "rounds": rounds,
            "final_decision": latest_decision,
            "final_confidence": latest_confidence,
            "stability_history": self.detector.history,
        }, indent=2))

        return Verdict(
            feature_id=feature.id,
            decision=latest_decision,
            confidence=latest_confidence,
            debate_log_path=debate_log_path,
            judge_reasoning=latest_reasoning,
        )
