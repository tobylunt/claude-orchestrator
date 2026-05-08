"""Unit tests for the bob/wiring.py composition module."""
import subprocess as sp
from pathlib import Path

import pytest

from claude_orchestrator.bob.wiring import (
    AutoApproveJudge,
    build_coordinator,
    build_verifier_registry,
)
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def test_build_verifier_registry_includes_python_pytest():
    reg = build_verifier_registry()
    v = reg.get("python_pytest")
    assert isinstance(v, PythonPytestVerifier)


def test_auto_approve_judge_returns_approve():
    judge = AutoApproveJudge()
    feature = Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["x"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.MCLOOP_DONE,
    )
    result = judge.judge_diff(feature, diff="(stub)")
    assert result["decision"] == "approve"
    assert result["confidence"] == 1.0


def test_build_coordinator_returns_callable_coordinator(tmp_path: Path):
    """Smoke test: build_coordinator returns a Coordinator that has duplo/mcloop/orchestra wired."""
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "# T\n## Motivation\nm\n## Features\n### F1: a\n"
        "- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )

    coord = build_coordinator(
        project_root=tmp_path,
        spec_path=spec_path,
        max_iterations=1,
        disabled_gates={"post_duplo"},
        claude_cmd="echo",  # placeholder for tests
    )
    # The coordinator's duplo callable should return a Spec when called.
    spec = coord.duplo()
    assert spec.title == "T"
    assert len(spec.features) == 1
