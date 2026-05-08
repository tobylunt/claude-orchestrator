"""Tests for the lint_universal verifier."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.lint_universal import LintUniversalVerifier
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
            verifier_id="lint_universal",
            success_criteria=["lint clean"],
            required_tools=[],
        ),
        status=FeatureStatus.PENDING,
    )


def test_returns_inconclusive_when_no_lint_tool(tmp_path: Path):
    v = LintUniversalVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_runs_ruff_when_pyproject_present(tmp_path: Path):
    """If pyproject.toml has [tool.ruff] config and ruff is installed, run it."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n"
    )
    (tmp_path / "good.py").write_text("def x():\n    pass\n")
    v = LintUniversalVerifier()
    result = v.verify(tmp_path, _feature())
    # Should be 'ok' or 'inconclusive' (if ruff isn't installed) — but never 'fail' for clean code.
    assert result.status in ("ok", "inconclusive")
