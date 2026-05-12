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
from claude_orchestrator.bob.review_policy import ReviewPolicy
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
        premium_judge_agent: DebateAgent | None = None,
        premium_codex_agent: DebateAgent | None = None,
        review_policy: ReviewPolicy | None = None,
        max_rounds: int = 5,
        ks_threshold: float = 0.05,
        consecutive_rounds: int = 2,
    ) -> None:
        self.claude = claude_agent
        self.codex = codex_agent
        self.judge = judge_agent
        self.premium_judge = premium_judge_agent
        self.premium_codex = premium_codex_agent
        self.review_policy = review_policy or ReviewPolicy()
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
                    "claude_decision": claude_msgs[-1].get("decision", "abstain"),
                    "codex": codex_msgs[-1]["content"],
                    "codex_decision": codex_msgs[-1].get("decision", "abstain"),
                    "judge": judge_final["content"],
                    "judge_decision": decision,
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

        premium_escalation = self._maybe_run_premium_review(
            feature=feature,
            diff=diff,
            prompt_base=prompt_base,
            rounds=rounds,
            decision=latest_decision,
            confidence=latest_confidence,
        )
        if premium_escalation.get("applied"):
            latest_decision = str(premium_escalation["decision"])
            latest_confidence = float(premium_escalation["confidence"])
            latest_reasoning = str(premium_escalation["reasoning"])

        debate_log_dir.mkdir(parents=True, exist_ok=True)
        debate_log_path = debate_log_dir / "debate.json"
        # Atomic write: tempfile + fsync + rename. A SIGKILL mid-write would
        # leave debate.json half-formed, and `bob status` (or any future
        # debate-log-feedback-into-McLoop wiring) would JSON-decode-error.
        from claude_orchestrator.bob.state_io import write_json_atomic
        write_json_atomic(debate_log_path, {
            "feature_id": feature.id,
            "rounds": rounds,
            "final_decision": latest_decision,
            "final_confidence": latest_confidence,
            "premium_escalation": premium_escalation,
            "stability_history": self.detector.history,
        })

        return Verdict(
            feature_id=feature.id,
            decision=latest_decision,
            confidence=latest_confidence,
            debate_log_path=debate_log_path,
            judge_reasoning=latest_reasoning,
        )

    def _maybe_run_premium_review(
        self,
        *,
        feature: Feature,
        diff: str,
        prompt_base: str,
        rounds: list[dict[str, Any]],
        decision: str,
        confidence: float,
    ) -> dict[str, Any]:
        policy_decision = self.review_policy.decide(
            feature=feature,
            diff=diff,
            rounds=rounds,
            decision=decision,
            confidence=confidence,
        )
        record: dict[str, Any] = {
            "requested": policy_decision.escalate,
            "reasons": list(policy_decision.reasons),
            "applied": False,
        }
        if not policy_decision.escalate:
            return record
        if self.premium_judge is None and self.premium_codex is None:
            record["skipped"] = "no_premium_agents_configured"
            return record

        with span("bob.orchestra.premium_review", attrs={
            "feature_id": feature.id,
            "reasons": ",".join(policy_decision.reasons),
        }):
            premium_codex_final: dict[str, Any] | None = None
            if self.premium_codex is not None:
                premium_codex_msgs = self.premium_codex.run(
                    prompt_base
                    + "\nPremium deep review: look for subtle correctness, "
                    + "security, architecture, and maintainability risks."
                )
                premium_codex_final = premium_codex_msgs[-1]
                record["premium_codex"] = premium_codex_final

            if self.premium_judge is None:
                record["skipped"] = "no_premium_judge_configured"
                return record

            judge_prompt = (
                prompt_base
                + "\nBaseline review rounds JSON:\n"
                + json.dumps(rounds[-3:], indent=2)
            )
            if premium_codex_final is not None:
                judge_prompt += (
                    "\nPremium Codex deep review:\n"
                    + str(premium_codex_final.get("content", ""))
                )
            judge_prompt += (
                "\nReturn JSON only: "
                '{"content": "...", "decision": "approve|reject|abstain", '
                '"confidence": 0.0}'
            )
            premium_judge_final = self.premium_judge.run(judge_prompt)[-1]
            record["premium_judge"] = premium_judge_final

            premium_decision = premium_judge_final.get("decision", "abstain")
            if premium_decision not in ("approve", "reject"):
                record["skipped"] = f"premium_decision={premium_decision}"
                return record
            record.update({
                "applied": True,
                "decision": premium_decision,
                "confidence": float(premium_judge_final.get("confidence", 0.0)),
                "reasoning": premium_judge_final.get("content", ""),
            })
            return record
