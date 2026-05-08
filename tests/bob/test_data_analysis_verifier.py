"""Tests for the data_analysis verifier."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.data_analysis import DataAnalysisVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.DATA_ANALYSIS,
        verification_plan=VerificationPlan(
            verifier_id="data_analysis",
            success_criteria=["data shape preserved"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_inconclusive_when_no_tests_or_notebooks(tmp_path: Path):
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_ok_on_passing_pytest(tmp_path: Path):
    (tmp_path / "test_d.py").write_text("def test_one():\n    assert 1 == 1\n")
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "ok"


def test_fail_on_failing_pytest(tmp_path: Path):
    (tmp_path / "test_d.py").write_text("def test_one():\n    assert 1 == 2\n")
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "fail"
