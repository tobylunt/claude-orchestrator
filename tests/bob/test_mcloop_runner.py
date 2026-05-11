"""Tests for the McLoop runner.

The runner spawns `claude -p` subprocesses. Tests use a stub `claude`
shell script (created in tmp_path) to exercise the loop deterministically.
"""
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from claude_orchestrator.bob.mcloop.runner import McLoopRunner, McLoopResult
from claude_orchestrator.bob.verifiers.protocol import PreflightResult, VerifyResult
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

    def __init__(self, results: list[VerifyResult], preflight: PreflightResult | None = None):
        self.results = list(results)
        self.calls = 0
        self._preflight = preflight or PreflightResult(ok=True)

    def applies_to(self): return [TaskType.LIBRARY]
    def required_tools(self): return []
    def preflight(self, ws): return self._preflight
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


def test_runner_polls_shutdown_between_iterations(tmp_path: Path, monkeypatch):
    """After a SIGINT (shutdown requested), McLoop must NOT start another
    iteration. Without this, claude -p subprocesses ignore propagated SIGINT
    and the loop blithely keeps spending API budget until max_iterations."""
    from claude_orchestrator.bob import signals as _signals
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    for f in ("spec.md", "activity.md", "failed_attempts.md", "verifier-results.jsonl"):
        (feature_dir / f).write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho ok\n")
    fake_claude.chmod(0o755)

    # Pre-arm the shutdown flag so the very first iteration check fires.
    monkeypatch.setattr(_signals, "_shutdown_requested", True)

    verifier = FakeVerifier(results=[])
    runner = McLoopRunner(
        claude_cmd=str(fake_claude),
        max_iterations=10,
        per_iteration_timeout_s=10,
    )
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "error"
    assert result.iterations == 0
    assert "shutdown" in result.last_reason.lower()
    assert verifier.calls == 0


def test_runner_halts_loud_when_preflight_fails(tmp_path: Path):
    """Verifier preflight failure (e.g., pytest not installed) must halt the
    loop BEFORE spending any iteration. Iterating against a broken verifier
    silently burns API budget — the same halt-loud rule as Inconclusive.
    """
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
    fake_claude.write_text("#!/bin/sh\necho should-not-run\nexit 1\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier(
        results=[],
        preflight=PreflightResult(ok=False, missing_tools=["pytest"]),
    )
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=5,
                         per_iteration_timeout_s=10)
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "halted_inconclusive"
    assert result.iterations == 0, "must halt before iterating"
    assert "preflight" in result.last_reason.lower()
    assert "pytest" in result.last_reason
    assert verifier.calls == 0, "verifier.verify() must not be called"


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

    # Find the claude-call args (later calls are autocommit git ops which the
    # patched subprocess.run mocks but are unrelated to this assertion).
    claude_calls = [a for a in captured_args if a and a[0] == "claude"]
    assert len(claude_calls) == 1
    args = claude_calls[0]
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


def test_runner_uses_injected_executor(tmp_path: Path):
    """McLoopRunner uses the injected executor (sandbox tier dispatch)."""
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

    class FakeExecutor:
        def __init__(self):
            self.calls = []

        def run(self, cmd, *, cwd, env, timeout):
            self.calls.append({"cmd": cmd, "cwd": cwd, "env": env, "timeout": timeout})
            from subprocess import CompletedProcess
            return CompletedProcess(cmd, 0, stdout="<promise>EXIT_SIGNAL</promise>", stderr="")

    fake = FakeExecutor()
    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(
        claude_cmd="claude", max_iterations=1, per_iteration_timeout_s=10,
        executor=fake,
    )
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    assert len(fake.calls) == 1
    assert fake.calls[0]["cmd"][0] == "claude"
    assert fake.calls[0]["cwd"] == workspace


def test_runner_yolo_mode_continues_on_first_inconclusive(tmp_path: Path):
    """In YOLO mode, an Inconclusive should not halt the loop on the first hit."""
    from claude_orchestrator.bob.yolo import YoloConfig
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

    # First two iterations: inconclusive. Third: ok with EXIT_SIGNAL.
    verifier = FakeVerifier([
        VerifyResult(status="inconclusive", reason="missing tests", artifacts=[], coverage_notes="add tests"),
        VerifyResult(status="inconclusive", reason="still missing", artifacts=[], coverage_notes="add tests"),
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])

    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                     max_inconclusive=3)

    # On the 3rd iteration, claude needs to emit EXIT_SIGNAL since verifier returns ok.
    fake_claude_exit = tmp_path / "claude_exit"
    fake_claude_exit.write_text(
        '#!/bin/sh\necho "<promise>EXIT_SIGNAL</promise>"\n'
    )
    fake_claude_exit.chmod(0o755)

    # We need a fake claude that emits EXIT_SIGNAL only on iteration 3. Simplest:
    # always emit EXIT_SIGNAL; the runner only treats it as exit when verifier == ok.
    runner = McLoopRunner(
        claude_cmd=str(fake_claude_exit), max_iterations=5,
        per_iteration_timeout_s=10,
        yolo=yolo,
    )
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "exit_signal"
    assert result.iterations == 3  # ran past 2 inconclusives


def test_runner_yolo_mode_halts_after_max_inconclusive(tmp_path: Path):
    """After max_inconclusive consecutive Inconclusives, YOLO halts loud."""
    from claude_orchestrator.bob.yolo import YoloConfig
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

    # All inconclusive; YOLO max=2, so after 2 in a row the loop halts.
    verifier = FakeVerifier([
        VerifyResult(status="inconclusive", reason="r1", artifacts=[], coverage_notes=None),
        VerifyResult(status="inconclusive", reason="r2", artifacts=[], coverage_notes=None),
        VerifyResult(status="inconclusive", reason="r3", artifacts=[], coverage_notes=None),  # not reached
    ])

    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                     max_inconclusive=2)

    runner = McLoopRunner(
        claude_cmd=str(fake_claude), max_iterations=5,
        per_iteration_timeout_s=10,
        yolo=yolo,
    )
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "halted_inconclusive"
    assert result.iterations == 2  # halted at the 2nd consecutive


def test_runner_default_mode_still_halts_on_first_inconclusive(tmp_path: Path):
    """Without YOLO, first Inconclusive halts loud (M2.2 behavior preserved)."""
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
        VerifyResult(status="inconclusive", reason="halt me", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(
        claude_cmd=str(fake_claude), max_iterations=5,
        per_iteration_timeout_s=10,
    )  # no yolo
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "halted_inconclusive"
    assert result.iterations == 1  # halted on first


def test_runner_autocommits_uncommitted_changes_after_green_verifier(tmp_path: Path):
    """When verifier returns ok, McLoop auto-commits any uncommitted worktree
    changes on the host. Necessary under --sandbox docker because the inner
    claude can't reach the host .git, so its files end up as untracked. If
    we didn't auto-commit, Orchestra's `git diff main..branch` would come
    back empty and the feature would be rejected."""
    import subprocess as sp

    # Set up a real git repo + worktree on a feature branch.
    project = tmp_path / "proj"
    project.mkdir()
    sp.run(["git", "init", "-b", "main", str(project)], check=True, capture_output=True)
    (project / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(project), "add", "."], check=True, capture_output=True)
    sp.run(
        ["git", "-C", str(project), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True, capture_output=True,
    )
    workspace = project / "worktree"
    sp.run(
        ["git", "-C", str(project), "worktree", "add", str(workspace), "-b", "feature/x"],
        check=True, capture_output=True,
    )
    # Simulate claude writing a new file but NOT committing (as happens in
    # --sandbox docker).
    (workspace / "new.py").write_text("def hi(): return 1\n")

    # Stub claude + verifier.
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '<promise>EXIT_SIGNAL</promise>'\n")
    fake_claude.chmod(0o755)
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    for f in ("spec.md", "activity.md", "failed_attempts.md", "verifier-results.jsonl"):
        (feature_dir / f).write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(
        claude_cmd=str(fake_claude),
        max_iterations=1,
        per_iteration_timeout_s=10,
    )
    runner.run(
        feature=_feature(), workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    # After the run, new.py should be committed on feature/x.
    log = sp.run(
        ["git", "-C", str(workspace), "log", "--oneline"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "mcloop iter 1: verifier green" in log, f"autocommit didn't fire; log: {log}"
    diff = sp.run(
        ["git", "-C", str(project), "diff", "--name-only", "main..feature/x"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "new.py" in diff, f"expected new.py in diff main..feature/x; got: {diff!r}"
