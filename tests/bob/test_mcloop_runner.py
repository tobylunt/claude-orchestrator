"""Tests for the McLoop runner.

The runner spawns `claude -p` subprocesses. Tests use a stub `claude`
shell script (created in tmp_path) to exercise the loop deterministically.
"""
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from claude_orchestrator.bob.mcloop.runner import McLoopRunner, McLoopResult
from claude_orchestrator.bob.verifiers.protocol import VerifyResult
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
            verifier_id="fake",
            success_criteria=["x"],
            required_tools=[],
        ),
        status=FeatureStatus.PENDING,
    )


class FakeVerifier:
    """Returns scripted results in order."""

    id = "fake"

    def __init__(self, results: list[VerifyResult]):
        self.results = list(results)
        self.calls = 0

    def applies_to(self): return [TaskType.LIBRARY]
    def required_tools(self): return []
    def preflight(self, ws): return None
    def verify(self, ws, f):
        self.calls += 1
        return self.results.pop(0)


@pytest.fixture
def fake_claude_emits_exit(tmp_path: Path) -> Path:
    """A fake `claude` binary that just emits the exit promise on first call."""
    script = tmp_path / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return script


def test_runner_exits_when_promise_emitted_and_verifier_ok(
    tmp_path: Path, fake_claude_emits_exit: Path
):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("# slice\n")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("# master\n")

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])

    runner = McLoopRunner(
        claude_cmd=str(fake_claude_emits_exit),
        max_iterations=5,
        per_iteration_timeout_s=10,
    )
    result = runner.run(
        feature=feature,
        workspace=workspace,
        master_spec=master_spec,
        feature_dir=feature_dir,
        verifier=verifier,
    )
    assert isinstance(result, McLoopResult)
    assert result.outcome == "exit_signal"
    assert result.iterations == 1


def test_runner_halts_loud_on_inconclusive(tmp_path: Path):
    """An Inconclusive verifier result halts immediately (default mode)."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho ok\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(
            status="inconclusive",
            reason="no tests collected",
            artifacts=[],
            coverage_notes="add a test_*.py file",
        ),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=5,
                         per_iteration_timeout_s=10)
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "halted_inconclusive"
    assert result.iterations == 1
    assert "no tests collected" in result.last_reason


def test_runner_stops_at_max_iterations(tmp_path: Path):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    # claude prints something but never the promise
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho 'still working'\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=2,
                         per_iteration_timeout_s=10)
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "max_iterations"
    assert result.iterations == 2


def test_runner_records_verifier_results(tmp_path: Path):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '<promise>EXIT_SIGNAL</promise>'\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=5,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    log = (feature_dir / "verifier-results.jsonl").read_text()
    assert '"status": "ok"' in log


def test_runner_passes_permission_bypass_flag(tmp_path: Path, monkeypatch):
    """McLoop must invoke claude -p with --permission-mode bypassPermissions
    so tool calls (Edit/Write/Bash) actually execute. Without this flag,
    claude -p exits without running queued tool calls."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    captured_args: list = []

    def capture(args, **kwargs):
        captured_args.append(args)
        # Return a CompletedProcess that emits EXIT_SIGNAL so the loop terminates.
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="<promise>EXIT_SIGNAL</promise>", stderr="")

    monkeypatch.setattr(subprocess, "run", capture)
    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd="claude", max_iterations=1,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    # Inspect the args passed to subprocess.run
    assert len(captured_args) == 1
    args = captured_args[0]
    # Args should be a list like ['claude', '-p', PROMPT, '--permission-mode', 'bypassPermissions']
    assert "--permission-mode" in args, f"missing permission flag in: {args}"
    idx = args.index("--permission-mode")
    assert args[idx + 1] == "bypassPermissions"


def test_runner_persists_per_iteration_log(tmp_path: Path):
    """Each iteration's claude stdout+stderr should be written to a per-iter log file."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    fake_claude.write_text(
        "#!/bin/sh\necho 'agent thinking out loud'\necho 'error message' 1>&2\n"
        "echo '<promise>EXIT_SIGNAL</promise>'\n"
    )
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=1,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    # iter-1.log must exist and contain the captured stdout AND stderr.
    log_path = feature_dir / "iter-1.log"
    assert log_path.exists(), f"per-iteration log missing: {log_path}"
    log = log_path.read_text()
    assert "agent thinking out loud" in log
    assert "error message" in log
    assert "<promise>EXIT_SIGNAL</promise>" in log


def test_runner_passes_stream_json_output_format(tmp_path: Path, monkeypatch):
    """McLoop should pass --output-format stream-json so iter-N.log captures
    the full tool-use trace, not just claude's final text reply."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    captured_args: list = []
    real_run = subprocess.run

    def capture(args, **kwargs):
        captured_args.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="<promise>EXIT_SIGNAL</promise>", stderr="")

    monkeypatch.setattr(subprocess, "run", capture)
    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd="claude", max_iterations=1,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    args = captured_args[0]
    assert "--output-format" in args, f"missing output-format in: {args}"
    idx = args.index("--output-format")
    assert args[idx + 1] == "stream-json"


def test_runner_logs_each_iteration_separately(tmp_path: Path):
    """Multiple iterations produce iter-1.log, iter-2.log, etc."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    # Always emits something but never EXIT_SIGNAL — forces 2 iterations.
    fake_claude.write_text("#!/bin/sh\necho 'iter ran'\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=2,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    assert (feature_dir / "iter-1.log").exists()
    assert (feature_dir / "iter-2.log").exists()
