"""Tests for the python_pytest verifier.

Uses real pytest in tmp_path workspaces — fast (sub-second) and
the most direct way to validate the verifier's contract.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["all tests pass"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


@pytest.fixture
def workspace_with_passing_test(tmp_path: Path) -> Path:
    (tmp_path / "test_thing.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n"
    )
    return tmp_path


@pytest.fixture
def workspace_with_failing_test(tmp_path: Path) -> Path:
    (tmp_path / "test_thing.py").write_text(
        "def test_fails():\n    assert 1 == 2\n"
    )
    return tmp_path


@pytest.fixture
def workspace_with_no_tests(tmp_path: Path) -> Path:
    return tmp_path


def test_id_and_applies_to():
    v = PythonPytestVerifier()
    assert v.id == "python_pytest"
    assert TaskType.LIBRARY in v.applies_to()


def test_verify_passes_on_green_tests(workspace_with_passing_test: Path):
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_passing_test, _feature())
    assert result.status == "ok"


def test_verify_fails_on_red_tests(workspace_with_failing_test: Path):
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_failing_test, _feature())
    assert result.status == "fail"
    assert "test_fails" in result.reason or "1 == 2" in result.reason


def test_verify_returns_inconclusive_when_no_tests(workspace_with_no_tests: Path):
    """No tests collected is the canonical 'I cannot decide' case (see spec §6.6)."""
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_no_tests, _feature())
    assert result.status == "inconclusive"
    assert "no tests collected" in result.reason.lower()
