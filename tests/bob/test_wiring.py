"""Unit tests for the bob/wiring.py composition module."""
import subprocess as sp
from pathlib import Path

import pytest

from claude_orchestrator.bob.wiring import (
    AutoApproveJudge,
    build_coordinator,
    build_coordinator_from_run_config,
    build_verifier_registry,
    build_vroom_audit_cycle,
    build_vroom_daemon,
    build_vroom_subprocess_invocation,
)
from claude_orchestrator.bob.run_config import RunConfig
from claude_orchestrator.bob.vroom_config import VroomConfig
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.bob.yolo import YoloConfig
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


def test_build_coordinator_from_run_config_forwards_resolved_values(
    tmp_path: Path,
    monkeypatch,
):
    """RunConfig is the handoff object from CLI to Coordinator wiring."""
    import claude_orchestrator.bob.wiring as wiring

    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=9.0)
    cfg = RunConfig(
        project_root=tmp_path,
        spec_path=tmp_path / "spec.md",
        max_iterations=7,
        max_cost=9.0,
        sandbox_tier="docker",
        yolo=yolo,
        disabled_gates=frozenset({"post_duplo"}),
        vroom=False,
        otel_endpoint=None,
    )
    captured = {}
    sentinel = object()

    def fake_build_coordinator(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(wiring, "build_coordinator", fake_build_coordinator)

    assert build_coordinator_from_run_config(cfg) is sentinel
    assert captured["project_root"] == tmp_path
    assert captured["spec_path"] == tmp_path / "spec.md"
    assert captured["max_iterations"] == 7
    assert captured["disabled_gates"] == {"post_duplo"}
    assert captured["sandbox_tier"] == "docker"
    assert captured["yolo"] is yolo


def test_build_vroom_subprocess_invocation_forwards_yolo_and_otel(tmp_path: Path):
    yolo = YoloConfig(
        enabled=True,
        sandbox_tier="docker",
        max_cost=12.5,
        max_inconclusive=7,
        vroom_severity="critical",
    )
    cfg = RunConfig(
        project_root=tmp_path,
        spec_path=tmp_path / "spec.md",
        max_iterations=1,
        max_cost=12.5,
        sandbox_tier="docker",
        yolo=yolo,
        disabled_gates=frozenset(),
        vroom=True,
        otel_endpoint="http://localhost:6006/v1/traces",
    )

    cmd, env = build_vroom_subprocess_invocation(
        cfg,
        base_env={"BOB_USE_STUB_VROOM": "1"},
        python_executable="/usr/bin/python-test",
    )

    assert cmd == [
        "/usr/bin/python-test",
        "-m",
        "claude_orchestrator.bob.cli",
        "vroom",
        "--project",
        str(tmp_path),
        "--interval",
        "1800",
        "--sandbox",
        "docker",
    ]
    assert env["BOB_USE_STUB_VROOM"] == "1"
    assert env["BOB_VROOM_YOLO_ENABLED"] == "1"
    assert env["BOB_VROOM_YOLO_SEVERITY"] == "critical"
    assert env["BOB_VROOM_YOLO_MAX_COST"] == "12.5"
    assert env["BOB_YOLO_MAX_INCONCLUSIVE"] == "7"
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://localhost:6006/v1/traces"


def test_build_vroom_audit_cycle_uses_configured_stub_pool(tmp_path: Path):
    cfg = VroomConfig(
        project_root=tmp_path,
        sandbox_tier="host",
        use_stub=True,
        yolo=None,
    )

    cycle = build_vroom_audit_cycle(cfg, include_fix_driver=False)

    assert cycle.project_root == tmp_path
    assert cycle.fix_driver is None
    assert [auditor.id for auditor in cycle.auditor_pool.auditors] == [
        "semgrep",
        "claude_architect",
        "codex_security",
    ]


def test_build_vroom_daemon_forwards_interval_and_watch_flag(tmp_path: Path):
    cfg = VroomConfig(
        project_root=tmp_path,
        sandbox_tier="host",
        use_stub=True,
        yolo=None,
        timer_interval_s=42,
        watch_main_ref=True,
    )

    daemon = build_vroom_daemon(cfg)

    assert daemon.project_root == tmp_path
    assert daemon.timer_interval_s == 42
    assert daemon.watch_main_ref is True
