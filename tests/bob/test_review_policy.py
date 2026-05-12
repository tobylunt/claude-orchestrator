"""Tests for cost-aware premium review policy."""

from __future__ import annotations

from claude_orchestrator.bob.review_policy import (
    ReviewPolicy,
    changed_files_from_diff,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature(name: str = "format docs", description: str = "small docs change") -> Feature:
    return Feature(
        id=1,
        name=name,
        description=description,
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["tests pass"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.MCLOOP_DONE,
    )


def test_changed_files_from_diff_extracts_paths():
    diff = (
        "diff --git a/src/a.py b/src/a.py\n"
        "+++ b/src/a.py\n"
        "diff --git a/README.md b/README.md\n"
        "+++ b/README.md\n"
    )
    assert changed_files_from_diff(diff) == ["src/a.py", "README.md"]


def test_review_policy_skips_confident_low_risk_review():
    policy = ReviewPolicy(risk_fragments=())
    decision = policy.decide(
        feature=_feature(),
        diff="diff --git a/docs.md b/docs.md\n+++ b/docs.md\n",
        rounds=[
            {
                "claude_decision": "approve",
                "codex_decision": "approve",
            }
        ],
        decision="approve",
        confidence=0.95,
    )
    assert decision.escalate is False


def test_review_policy_escalates_low_confidence():
    policy = ReviewPolicy(min_confidence=0.85, risk_fragments=())
    decision = policy.decide(
        feature=_feature(),
        diff="",
        rounds=[],
        decision="approve",
        confidence=0.5,
    )
    assert decision.escalate is True
    assert "confidence<0.85" in decision.reasons


def test_review_policy_escalates_reviewer_disagreement():
    policy = ReviewPolicy(risk_fragments=())
    decision = policy.decide(
        feature=_feature(),
        diff="",
        rounds=[
            {
                "claude_decision": "approve",
                "codex_decision": "reject",
            }
        ],
        decision="approve",
        confidence=0.95,
    )
    assert decision.escalate is True
    assert "reviewer_disagreement" in decision.reasons


def test_review_policy_escalates_risky_surface():
    policy = ReviewPolicy(risk_fragments=("auth",))
    decision = policy.decide(
        feature=_feature(name="auth hardening"),
        diff="diff --git a/src/login.py b/src/login.py\n+++ b/src/login.py\n",
        rounds=[],
        decision="approve",
        confidence=0.95,
    )
    assert decision.escalate is True
    assert "risk_surface=auth" in decision.reasons


def test_review_policy_from_env_can_disable_premium():
    policy = ReviewPolicy.from_env({"BOB_ORCHESTRA_PREMIUM_POLICY": "never"})
    decision = policy.decide(
        feature=_feature(name="auth hardening"),
        diff="",
        rounds=[],
        decision="abstain",
        confidence=0.0,
    )
    assert decision.escalate is False
