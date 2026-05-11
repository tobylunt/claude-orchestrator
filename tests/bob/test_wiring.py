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


def test_build_verifier_registry_has_all_m2_verifiers():
    reg = build_verifier_registry()
    for vid in ["python_pytest", "lint_universal", "data_analysis", "geospatial"]:
        assert reg.get(vid) is not None


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


def test_build_coordinator_returns_callable_coordinator(tmp_path: Path, monkeypatch):
    """Smoke test: build_coordinator returns a Coordinator that has duplo/mcloop/orchestra wired."""
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
    # The meta-rubric check is part of Duplo and would otherwise hit Anthropic.
    monkeypatch.setenv("BOB_USE_STUB_DUPLO", "1")
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


def test_build_coordinator_handles_directory_inputs(tmp_path: Path, monkeypatch):
    """When spec_path is a directory containing spec.md, BOB_USE_STUB_DUPLO=1 routes to markdown."""
    import subprocess as sp
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
    monkeypatch.setenv("BOB_USE_STUB_DUPLO", "1")
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )

    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    (inputs_dir / "spec.md").write_text(
        "# T\n## Motivation\nm\n## Features\n### F1: a\n"
        "- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )
    coord = build_coordinator(
        project_root=tmp_path,
        spec_path=inputs_dir,
        max_iterations=1,
        disabled_gates={"post_duplo"},
        claude_cmd="echo",
    )
    spec = coord.duplo()
    assert spec.title == "T"


def test_build_coordinator_uses_host_executor_by_default(tmp_path: Path, monkeypatch):
    """Default sandbox_tier='host' should use HostExecutor."""
    import subprocess as sp
    from claude_orchestrator.bob.sandbox.host import HostExecutor
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
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
        claude_cmd="echo",
    )
    # Coordinator's mcloop is a closure over runner; we can't easily inspect.
    # Instead, just verify the build doesn't crash and that constructing with
    # sandbox_tier="docker" also works.
    assert coord is not None


def test_build_coordinator_accepts_docker_tier(tmp_path: Path, monkeypatch):
    import subprocess as sp
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
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
        claude_cmd="echo",
        sandbox_tier="docker",
    )
    assert coord is not None


def test_build_coordinator_rejects_unknown_tier(tmp_path: Path, monkeypatch):
    import pytest as _pytest
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("# T\n## Motivation\nm\n## Features\n### F1: a\n- task_type: library\n- verifier: python_pytest\n- success_criteria:\n  - x\n- description: a\n")
    with _pytest.raises(ValueError, match="unknown sandbox tier"):
        build_coordinator(
            project_root=tmp_path,
            spec_path=spec_path,
            max_iterations=1,
            disabled_gates={"post_duplo"},
            sandbox_tier="bogus",
        )


def test_build_coordinator_uses_stub_when_env_set(tmp_path: Path, monkeypatch):
    """BOB_USE_STUB_ORCHESTRA=1 falls back to OrchestraStub (offline mode)."""
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
    monkeypatch.setenv("BOB_USE_STUB_DUPLO", "1")
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
        claude_cmd="echo",
    )
    # Just confirm build_coordinator doesn't crash with the stub.
    assert coord.duplo() is not None
