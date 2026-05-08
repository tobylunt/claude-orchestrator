"""Tests for HITL gate registry and post-Duplo gate."""
import io
import sys
from pathlib import Path

import pytest

from claude_orchestrator.bob.hitl.gates import (
    GateDecision,
    GateRegistry,
    GateSkipped,
    PostDuploGate,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
)


def _spec() -> Spec:
    return Spec(
        title="Demo",
        motivation="why",
        inputs=[],
        features=[Feature(
            id=1, name="auth", description="login",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["tests pass"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )],
        rubric_meta_check_passed=True,
    )


def test_post_duplo_gate_approves_on_y(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    gate = PostDuploGate()
    decision = gate.run(_spec())
    assert decision == GateDecision.APPROVE


def test_post_duplo_gate_rejects_on_n(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    gate = PostDuploGate()
    decision = gate.run(_spec())
    assert decision == GateDecision.REJECT


def test_registry_skip_via_disable_list():
    reg = GateRegistry(disabled={"post_duplo"})
    with pytest.raises(GateSkipped):
        reg.run("post_duplo", _spec())


def test_registry_runs_when_not_disabled(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    reg = GateRegistry(disabled=set())
    reg.register("post_duplo", PostDuploGate())
    decision = reg.run("post_duplo", _spec())
    assert decision == GateDecision.APPROVE


def test_post_duplo_gate_auto_approves_when_yolo_enabled(monkeypatch):
    """When YoloConfig.enabled=True and rubric_meta_check_passed=True, auto-approve."""
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0)

    # No stdin: input() would block. The auto-approve should kick in BEFORE input().
    spec = _spec()
    assert spec.rubric_meta_check_passed is True

    gate = PostDuploGate(yolo=yolo)
    decision = gate.run(spec)
    assert decision == GateDecision.APPROVE


def test_post_duplo_gate_does_not_auto_approve_without_meta_rubric(monkeypatch):
    """Even with YOLO enabled, fail-closed if rubric_meta_check_passed=False."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))  # user says no
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0)

    spec = _spec()
    spec.rubric_meta_check_passed = False  # meta-rubric did NOT pass

    gate = PostDuploGate(yolo=yolo)
    decision = gate.run(spec)
    # Falls back to interactive prompt; user said 'n' => REJECT.
    assert decision == GateDecision.REJECT


def test_post_duplo_gate_default_no_yolo_still_prompts(monkeypatch):
    """Without YoloConfig (or enabled=False), gate prompts the user."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))

    gate = PostDuploGate()  # no yolo arg
    decision = gate.run(_spec())
    assert decision == GateDecision.APPROVE
