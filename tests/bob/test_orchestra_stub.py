"""Tests for the Orchestra stub.

M1 stub: a single LLM-as-judge call that says approve|reject|abstain.
M2 replaces this with AutoGen GroupChat + KS-stability.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.orchestra.stub import (
    OrchestraStub,
    SingleJudge,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="auth", description="login",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["users can log in"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.MCLOOP_DONE,
    )


class FakeJudge:
    def __init__(self, response: dict):
        self.response = response

    def judge_diff(self, feature: Feature, diff: str) -> dict:
        return self.response


def test_stub_returns_approve(tmp_path: Path):
    judge = FakeJudge({
        "decision": "approve", "confidence": 0.9, "reasoning": "lgtm",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="diff goes here", debate_log_dir=tmp_path)
    assert verdict.decision == "approve"
    assert verdict.confidence == pytest.approx(0.9)


def test_stub_returns_reject_with_reasoning(tmp_path: Path):
    judge = FakeJudge({
        "decision": "reject", "confidence": 0.7,
        "reasoning": "missing csrf protection",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="d", debate_log_dir=tmp_path)
    assert verdict.decision == "reject"
    assert "csrf" in verdict.judge_reasoning


def test_stub_writes_debate_log(tmp_path: Path):
    judge = FakeJudge({
        "decision": "approve", "confidence": 1.0, "reasoning": "ok",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="d", debate_log_dir=tmp_path)
    assert verdict.debate_log_path.exists()
    text = verdict.debate_log_path.read_text()
    assert "approve" in text
    assert "ok" in text
