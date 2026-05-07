"""End-to-end smoke test for Bob M1.

Exercises the full pipeline with stubbed external calls:
  - markdown spec → Spec/Feature
  - Coordinator → McLoopRunner (with stub `claude` shell script) → python_pytest
  - Orchestra stub auto-approves
  - Coordinator marks the feature merged

No real Claude API calls are made. The test takes ~2-3 seconds.
"""
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from claude_orchestrator.bob.coordinator import Coordinator, RunScope
from claude_orchestrator.bob.duplo.markdown_parser import parse_markdown_spec
from claude_orchestrator.bob.hitl.gates import GateRegistry
from claude_orchestrator.bob.mcloop.runner import McLoopRunner
from claude_orchestrator.bob.orchestra.stub import OrchestraStub
from claude_orchestrator.bob.state_io import read_jsonl, read_json
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import Feature, Verdict


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Initialize a git repo with a tiny passing test in the worktree-to-be."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "test_smoke.py").write_text(
        "def test_passes():\n    assert True\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


@pytest.fixture
def fake_claude(tmp_path_factory) -> Path:
    """A `claude` stub that emits EXIT_SIGNAL on first call."""
    d = tmp_path_factory.mktemp("fake-claude")
    script = d / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return script


def test_e2e_thin_slice_runs_to_merged(project_root: Path, fake_claude: Path):
    spec_path = project_root / "spec.md"
    spec_path.write_text(dedent("""\
        # Smoke
        ## Motivation
        Prove M1 works end-to-end without hitting real APIs.
        ## Features
        ### F1: passing-tests
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: This feature is already implemented; the loop should exit on iteration 1.
    """))

    spec = parse_markdown_spec(spec_path)
    spec.rubric_meta_check_passed = True  # bypass meta-rubric check for E2E

    runner = McLoopRunner(
        claude_cmd=str(fake_claude),
        max_iterations=5,
        per_iteration_timeout_s=10,
    )
    verifier = PythonPytestVerifier()

    class AutoApproveJudge:
        def judge_diff(self, feature: Feature, diff: str) -> dict:
            return {"decision": "approve", "confidence": 1.0, "reasoning": "stub e2e"}

    orchestra = OrchestraStub(judge=AutoApproveJudge())
    gates = GateRegistry(disabled={"post_duplo"})

    def fake_duplo() -> Feature:
        return spec

    def mcloop_callable(*, feature, workspace, master_spec, feature_dir):
        # In the real CLI the coordinator creates a worktree; for this test the
        # workspace is the project root (where the passing test already lives).
        return runner.run(
            feature=feature,
            workspace=project_root,
            master_spec=master_spec,
            feature_dir=feature_dir,
            verifier=verifier,
        )

    def orchestra_callable(*, feature, workspace, feature_dir):
        return orchestra.review(
            feature=feature,
            diff="(stub diff)",
            debate_log_dir=feature_dir,
        )

    coord = Coordinator(
        project_root=project_root,
        duplo=fake_duplo,
        mcloop=mcloop_callable,
        orchestra=orchestra_callable,
        gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    bob_dir = project_root / ".bob"
    feature_dir = next((bob_dir / "features").iterdir())
    state = read_json(feature_dir / "state.json")
    assert state["status"] == "merged"

    events = [e["event"] for e in read_jsonl(bob_dir / "run-log.jsonl")]
    assert "run_started" in events
    assert "feature_merged" in events
    assert "run_finished" in events
