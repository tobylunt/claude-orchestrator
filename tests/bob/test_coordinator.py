"""Tests for the Coordinator state machine.

These tests use stubbed Duplo / McLoop / Orchestra and never hit a real
LLM. The Coordinator's job is choreography; the tests verify it walks
features in order, persists state, and respects HITL gates.
"""
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    from datetime import UTC, datetime
else:
    from datetime import datetime, timezone
    UTC = timezone.utc
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.coordinator import Coordinator, RunScope
from claude_orchestrator.bob.hitl.gates import GateDecision, GateRegistry, PostDuploGate
from claude_orchestrator.bob.mcloop.runner import McLoopResult
from claude_orchestrator.bob.state_io import read_json, read_jsonl
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
    Verdict,
)


def _feature(i: int, name: str) -> Feature:
    return Feature(
        id=i, name=name, description=f"f{i}",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["x"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


def _spec_with_features(*names: str) -> Spec:
    return Spec(
        title="t", motivation="m",
        features=[_feature(i, n) for i, n in enumerate(names, start=1)],
        rubric_meta_check_passed=True,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


def test_coordinator_walks_features_in_order(project_root: Path, monkeypatch):
    spec = _spec_with_features("a", "b")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
    ))
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))

    gates = GateRegistry(disabled={"post_duplo"})  # auto-skip for test

    coord = Coordinator(
        project_root=project_root,
        duplo=duplo,
        mcloop=mcloop,
        orchestra=orchestra,
        gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    bob_dir = project_root / ".bob"
    cursor = read_json(bob_dir / "cursor.json")
    assert cursor["current_phase"] == "idle"
    assert mcloop.call_count == 2
    # call order: feature 1, then feature 2
    assert mcloop.call_args_list[0].kwargs["feature"].name == "a"
    assert mcloop.call_args_list[1].kwargs["feature"].name == "b"


def test_coordinator_writes_run_log(project_root: Path):
    spec = _spec_with_features("a")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
    ))
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    events = list(read_jsonl(project_root / ".bob" / "run-log.jsonl"))
    event_types = [e["event"] for e in events]
    assert "run_started" in event_types
    assert "feature_started" in event_types
    assert "feature_merged" in event_types


def test_coordinator_respects_post_duplo_reject(project_root: Path, monkeypatch):
    spec = _spec_with_features("a")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock()
    orchestra = MagicMock()

    class RejectingGate(PostDuploGate):
        def run(self, _):
            return GateDecision.REJECT

    gates = GateRegistry()
    gates.register("post_duplo", RejectingGate())

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    mcloop.assert_not_called()
    orchestra.assert_not_called()


def test_coordinator_marks_feature_failed_on_mcloop_halt(project_root: Path):
    spec = _spec_with_features("a")
    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="halted_inconclusive", iterations=2,
        last_reason="no tests collected", last_status="inconclusive",
    ))
    orchestra = MagicMock()
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    orchestra.assert_not_called()
    bob_dir = project_root / ".bob"
    state_path = bob_dir / "features" / "001-a" / "state.json"
    state = read_json(state_path)
    assert state["status"] == "failed"
    assert "no tests collected" in state["last_error"]
    # Worktree intentionally LEFT in place on failure for inspection (M2a/b design).
    worktree_path = bob_dir / "worktrees" / "001-a"
    assert worktree_path.exists(), "failed-feature worktree should remain for inspection"


def test_coordinator_creates_and_removes_worktree(project_root: Path):
    """When merge succeeds, Coordinator creates the worktree before McLoop and removes it after merge."""
    spec = _spec_with_features("a")
    duplo = MagicMock(return_value=spec)

    def mcloop_callable(*, feature, workspace, master_spec, feature_dir):
        # The workspace must exist when McLoop is called.
        assert workspace.exists(), f"worktree not created: {workspace}"
        return McLoopResult(
            outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
        )

    orchestra_callable = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop_callable,
        orchestra=orchestra_callable, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    # After merge, the worktree should be removed.
    worktree_path = project_root / ".bob" / "worktrees" / "001-a"
    assert not worktree_path.exists(), \
        f"worktree should have been removed after merge: {worktree_path}"


def test_coordinator_aborts_on_shutdown_request(project_root: Path, monkeypatch):
    """Setting the shutdown flag between features stops the loop."""
    from claude_orchestrator.bob import signals
    spec = _spec_with_features("a", "b")

    duplo = MagicMock(return_value=spec)
    # mcloop sets the shutdown flag during the FIRST feature; the second feature
    # must not run.
    def mcloop_setting_shutdown(*, feature, workspace, master_spec, feature_dir):
        # Simulate Ctrl-C right after the first feature's mcloop returns.
        signals._shutdown_requested = True
        return McLoopResult(
            outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
        )
    mcloop = MagicMock(side_effect=mcloop_setting_shutdown)
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    # Reset the global shutdown flag at the start of the test
    signals._shutdown_requested = False

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    try:
        coord.run(RunScope(includes_duplo=True))
    finally:
        signals._shutdown_requested = False  # leave clean for other tests

    # Only the first feature ran:
    assert mcloop.call_count == 1
    # Run-log records the shutdown:
    events = [e["event"] for e in read_jsonl(project_root / ".bob" / "run-log.jsonl")]
    assert "run_aborted" in events


def test_coordinator_resumes_from_mcloop_done(project_root: Path):
    """When a feature is already MCLOOP_DONE, skip McLoop and run Orchestra directly.

    Simulates a graceful shutdown between McLoop and Orchestra: feature has
    status=MCLOOP_DONE on disk, with the worktree still present.
    """
    import json
    spec = _spec_with_features("a")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock()  # SHOULD NOT BE CALLED
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )

    # First run: get feature to MCLOOP_DONE by simulating the prior state.
    # We materialize the spec then manually set status to MCLOOP_DONE and
    # create a worktree (so the resume path doesn't try to recreate it).
    coord._materialize_spec(spec)
    feature_dir = project_root / ".bob" / "features" / "001-a"
    state = json.loads((feature_dir / "state.json").read_text())
    state["status"] = "mcloop_done"
    (feature_dir / "state.json").write_text(json.dumps(state))

    # Pre-create the worktree (simulating prior run's leftover state).
    from claude_orchestrator.bob.worktree import add_worktree
    worktree_path = project_root / ".bob" / "worktrees" / "001-a"
    add_worktree(project_root, worktree_path, branch="bob/001-a")

    # Now run with includes_duplo=False (we already materialized).
    coord.run(RunScope(includes_duplo=False))

    # McLoop should NOT have been called.
    mcloop.assert_not_called()
    # Orchestra DID get called.
    assert orchestra.call_count == 1
    # Final status: merged.
    final = json.loads((feature_dir / "state.json").read_text())
    assert final["status"] == "merged"
