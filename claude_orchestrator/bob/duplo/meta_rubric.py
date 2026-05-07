"""Meta-rubric coverage check (see spec §6.6).

Runs an LLM-as-judge that asks: "given this feature's success criteria
and the verifier_id assigned to it, does the verifier actually cover the
criteria?" If the answer is 'inadequate', Duplo refuses to ship the spec.

The judge is injected for testability — production wires in a real
Anthropic API call; tests use a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from claude_orchestrator.models import Feature


@dataclass(frozen=True)
class CoverageJudgment:
    adequate: bool
    missing: list[str]
    reasoning: str

    def __str__(self) -> str:
        if self.adequate:
            return f"adequate: {self.reasoning}"
        return f"inadequate; missing: {self.missing}; reasoning: {self.reasoning}"


class Judge(Protocol):
    """Anything that takes a payload and returns a coverage verdict dict.

    Production implementation calls Claude Opus 4.7. Tests inject a fake.
    """

    def judge(self, prompt_payload: dict) -> dict: ...


class MetaRubricChecker:
    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    def check(self, feature: Feature) -> CoverageJudgment:
        payload = {
            "feature_name": feature.name,
            "feature_description": feature.description,
            "task_type": str(feature.task_type),
            "verifier_id": feature.verification_plan.verifier_id,
            "success_criteria": feature.verification_plan.success_criteria,
            "required_tools": feature.verification_plan.required_tools,
            "instruction": (
                "Decide whether the assigned verifier actually verifies the "
                "feature's success criteria. Reply JSON with keys "
                "'verdict' (one of 'adequate'|'inadequate'), 'missing' (list of "
                "criteria the verifier does not cover; [] when adequate), and "
                "'reasoning' (one short sentence)."
            ),
        }
        result = self._judge.judge(payload)
        verdict = result.get("verdict", "inadequate")
        missing = list(result.get("missing", []))
        reasoning = result.get("reasoning", "")
        return CoverageJudgment(
            adequate=(verdict == "adequate"),
            missing=missing,
            reasoning=reasoning,
        )
