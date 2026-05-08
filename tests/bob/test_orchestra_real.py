"""Tests for the production AutoGen-backed Orchestra (M2)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.orchestra.real import RealOrchestra
from claude_orchestrator.bob.orchestra.stability import StabilityVerdict
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
