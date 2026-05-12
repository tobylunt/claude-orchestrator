"""Tests for the production AutoGen-backed Orchestra (M2)."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.orchestra.real import RealOrchestra
from claude_orchestrator.bob.orchestra.stability import StabilityVerdict
from claude_orchestrator.bob.review_policy import ReviewPolicy
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


def test_real_orchestra_returns_verdict_with_debate_log(tmp_path: Path):
    """A consensus debate produces an approve verdict with a recorded debate log."""
    claude = MagicMock()
    claude.run = MagicMock(return_value=[{"content": "The diff looks correct.", "decision": "approve"}])
    codex = MagicMock()
    codex.run = MagicMock(return_value=[{"content": "I find no security issues.", "decision": "approve"}])
    judge = MagicMock()
    judge.run = MagicMock(return_value=[{"content": "Consensus approve.", "decision": "approve", "confidence": 0.9}])

    orchestra = RealOrchestra(
        claude_agent=claude, codex_agent=codex, judge_agent=judge,
        max_rounds=3,
    )
    verdict = orchestra.review(
        feature=_feature(),
        diff="--- a/x.py\n+++ b/x.py\n",
        debate_log_dir=tmp_path,
    )
    assert verdict.decision == "approve"
    assert verdict.debate_log_path.exists()
    log = verdict.debate_log_path.read_text()
    assert "approve" in log


def test_real_orchestra_abstain_on_max_rounds(tmp_path: Path):
    """When agents never agree across max_rounds, return abstain."""
    claude = MagicMock()
    codex = MagicMock()
    judge = MagicMock()
    claude.run = MagicMock(side_effect=[
        [{"content": "approve", "decision": "approve"}],
        [{"content": "reject", "decision": "reject"}],
        [{"content": "approve", "decision": "approve"}],
    ])
    codex.run = MagicMock(side_effect=[
        [{"content": "reject", "decision": "reject"}],
        [{"content": "approve", "decision": "approve"}],
        [{"content": "reject", "decision": "reject"}],
    ])
    judge.run = MagicMock(side_effect=[
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
    ])

    orchestra = RealOrchestra(
        claude_agent=claude, codex_agent=codex, judge_agent=judge,
        max_rounds=3,
    )
    verdict = orchestra.review(
        feature=_feature(),
        diff="(diff)",
        debate_log_dir=tmp_path,
    )
    assert verdict.decision == "abstain"


def test_real_orchestra_skips_premium_review_for_low_risk_high_confidence(
    tmp_path: Path,
):
    claude = MagicMock()
    claude.run = MagicMock(return_value=[{"content": "ok", "decision": "approve"}])
    codex = MagicMock()
    codex.run = MagicMock(return_value=[{"content": "ok", "decision": "approve"}])
    judge = MagicMock()
    judge.run = MagicMock(
        return_value=[{"content": "approve", "decision": "approve", "confidence": 0.95}]
    )
    premium_judge = MagicMock()
    premium_codex = MagicMock()

    orchestra = RealOrchestra(
        claude_agent=claude,
        codex_agent=codex,
        judge_agent=judge,
        premium_judge_agent=premium_judge,
        premium_codex_agent=premium_codex,
        review_policy=ReviewPolicy(risk_fragments=()),
        max_rounds=1,
    )

    verdict = orchestra.review(
        feature=_feature(),
        diff="diff --git a/docs.md b/docs.md\n+++ b/docs.md\n",
        debate_log_dir=tmp_path,
    )

    assert verdict.decision == "approve"
    premium_judge.run.assert_not_called()
    premium_codex.run.assert_not_called()
    log = json.loads(verdict.debate_log_path.read_text())
    assert log["premium_escalation"]["requested"] is False


def test_real_orchestra_applies_premium_review_when_policy_triggers(
    tmp_path: Path,
):
    claude = MagicMock()
    claude.run = MagicMock(return_value=[{"content": "ok", "decision": "approve"}])
    codex = MagicMock()
    codex.run = MagicMock(return_value=[{"content": "risk", "decision": "reject"}])
    judge = MagicMock()
    judge.run = MagicMock(
        return_value=[{"content": "uncertain", "decision": "approve", "confidence": 0.5}]
    )
    premium_codex = MagicMock()
    premium_codex.run = MagicMock(
        return_value=[{"content": "deep risk", "decision": "reject"}]
    )
    premium_judge = MagicMock()
    premium_judge.run = MagicMock(
        return_value=[
            {"content": "premium reject", "decision": "reject", "confidence": 0.98}
        ]
    )

    orchestra = RealOrchestra(
        claude_agent=claude,
        codex_agent=codex,
        judge_agent=judge,
        premium_judge_agent=premium_judge,
        premium_codex_agent=premium_codex,
        review_policy=ReviewPolicy(min_confidence=0.85, risk_fragments=()),
        max_rounds=1,
    )

    verdict = orchestra.review(
        feature=_feature(),
        diff="diff --git a/src/x.py b/src/x.py\n+++ b/src/x.py\n",
        debate_log_dir=tmp_path,
    )

    assert verdict.decision == "reject"
    assert verdict.confidence == 0.98
    premium_codex.run.assert_called_once()
    premium_judge.run.assert_called_once()
    log = json.loads(verdict.debate_log_path.read_text())
    assert log["premium_escalation"]["applied"] is True
    assert "confidence<0.85" in log["premium_escalation"]["reasons"]
