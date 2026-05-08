"""Bob CLI — subcommands `run`, `status`.

Invoked via `python -m claude_orchestrator.bob.cli` or as `bob`
(when registered in pyproject.toml's [project.scripts]).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from claude_orchestrator.bob.state_io import read_json


def _cmd_run(args: argparse.Namespace) -> int:
    from claude_orchestrator.bob.coordinator import RunScope
    from claude_orchestrator.bob.process_lock import (
        Lock, LockHeld, StalePidDetected, acquire_lock, release_lock,
    )
    from claude_orchestrator.bob.signals import (
        install_handlers, register_cleanup,
    )
    from claude_orchestrator.bob.wiring import build_coordinator

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    if not args.inputs:
        print("error: --inputs is required (path to a markdown spec)", file=sys.stderr)
        return 2
    spec_path = Path(args.inputs).resolve()
    if not spec_path.exists():
        print(f"error: input spec not found: {spec_path}", file=sys.stderr)
        return 2
    if spec_path.is_file():
        # Pre-flight parse so malformed markdown specs fail cleanly without acquiring the lock.
        from claude_orchestrator.bob.duplo.markdown_parser import (
            SpecParseError,
            parse_markdown_spec,
        )
        try:
            parse_markdown_spec(spec_path)
        except SpecParseError as e:
            print(f"spec error: {e}", file=sys.stderr)
            return 4
    elif not spec_path.is_dir():
        print(f"error: --inputs must be a file or directory: {spec_path}", file=sys.stderr)
        return 2
    # Directory path: skip pre-flight (multimodal Duplo will produce the spec at run time).

    bob_dir = project_root / ".bob"
    install_handlers()

    # Acquire single-instance lock; release on shutdown.
    try:
        lock: Lock = acquire_lock(bob_dir)
    except LockHeld as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except StalePidDetected as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    register_cleanup(lambda: release_lock(lock))

    # Inputs dir: capture for posterity.
    bob_dir.mkdir(parents=True, exist_ok=True)
    (bob_dir / "inputs").mkdir(exist_ok=True)
    if spec_path.is_file():
        captured = bob_dir / "inputs" / spec_path.name
        if captured.resolve() != spec_path.resolve():
            captured.write_bytes(spec_path.read_bytes())
    else:
        # Directory: copy each file under .bob/inputs/<dirname>/ for the audit trail.
        import shutil
        target_dir = bob_dir / "inputs" / spec_path.name
        if target_dir.resolve() != spec_path.resolve():
            if target_dir.exists():
                shutil.rmtree(target_dir)
            shutil.copytree(spec_path, target_dir)

    sandbox_tier = (
        args.sandbox
        or os.environ.get("BOB_SANDBOX_TIER")
        or "host"
    )

    from claude_orchestrator.bob.yolo import YoloConfig, YoloInvariantError

    try:
        yolo = YoloConfig.from_env(
            enabled=args.yolo,
            sandbox_tier=sandbox_tier,
            max_cost=args.max_cost,
        )
    except YoloInvariantError as e:
        print(f"yolo error: {e}", file=sys.stderr)
        return 5

    # (yolo is built but not yet plumbed into Coordinator/Wiring — that integration
    # is progressive. For M3, simply validating the config + surfacing it in logs
    # is enough to demonstrate the invariant enforcement.)
    if yolo.enabled:
        print(f"YOLO mode enabled: sandbox={yolo.sandbox_tier} max_cost=${yolo.max_cost} "
              f"max_inconclusive={yolo.max_inconclusive} vroom_severity={yolo.vroom_severity}")

    coord = build_coordinator(
        project_root=project_root,
        spec_path=spec_path,
        max_iterations=args.max_iterations,
        disabled_gates=set(args.no_gate),
        sandbox_tier=sandbox_tier,
    )

    # If --vroom is set, spawn the Vroom daemon as a subprocess.
    vroom_proc = None
    if args.vroom:
        import subprocess as _subprocess
        import sys as _sys
        vroom_cmd = [
            _sys.executable, "-m", "claude_orchestrator.bob.cli",
            "vroom",
            "--project", str(project_root),
            "--interval", "1800",
        ]
        # Pass through stub env vars so the child uses the same offline mode if any.
        child_env = os.environ.copy()
        vroom_proc = _subprocess.Popen(
            vroom_cmd,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            env=child_env,
            start_new_session=True,
        )
        print(f"-> Vroom daemon spawned (pid: {vroom_proc.pid})")

        def _stop_vroom():
            if vroom_proc and vroom_proc.poll() is None:
                # SIGTERM to the process group so any sub-children also die.
                try:
                    import signal as _signal
                    os.killpg(os.getpgid(vroom_proc.pid), _signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    vroom_proc.wait(timeout=5)
                except _subprocess.TimeoutExpired:
                    try:
                        import signal as _signal
                        os.killpg(os.getpgid(vroom_proc.pid), _signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass

        register_cleanup(_stop_vroom)

    coord.run(RunScope(includes_duplo=True))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Parse the spec and report errors cleanly, without acquiring any lock."""
    from claude_orchestrator.bob.duplo.markdown_parser import (
        SpecParseError,
        parse_markdown_spec,
    )

    spec_path = Path(args.inputs).resolve()
    if not spec_path.is_file():
        print(f"error: input spec not found: {spec_path}", file=sys.stderr)
        return 2

    try:
        spec = parse_markdown_spec(spec_path)
    except SpecParseError as e:
        print(f"spec error: {e}", file=sys.stderr)
        return 4

    print(f"OK — spec '{spec.title}' parsed successfully")
    print(f"      motivation: {spec.motivation}")
    print(f"      {len(spec.features)} feature(s):")
    for f in spec.features:
        print(f"        [{f.id}] {f.name} (task_type={f.task_type}, "
              f"verifier={f.verification_plan.verifier_id})")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project).resolve()
    bob_dir = project_root / ".bob"
    if not bob_dir.exists():
        print(f"no .bob/ found in {project_root} (not initialized)")
        return 0

    cursor = read_json(bob_dir / "cursor.json", default={})
    print(f"phase: {cursor.get('current_phase', 'unknown')}")
    print(f"current feature: {cursor.get('current_feature_id', 'n/a')}")
    print(f"last event: {cursor.get('last_event_at', 'n/a')}")

    features_dir = bob_dir / "features"
    if features_dir.exists():
        feats = sorted(d.name for d in features_dir.iterdir() if d.is_dir())
        if feats:
            print(f"features ({len(feats)}):")
            for d in feats:
                state = read_json(features_dir / d / "state.json", default={})
                print(f"  {d}: status={state.get('status', 'unknown')}")
    return 0


def _cmd_vroom_start(args: argparse.Namespace) -> int:
    """Start the Vroom daemon in the foreground (blocking)."""
    import time
    from claude_orchestrator.bob.signals import install_handlers, is_shutdown_requested
    from claude_orchestrator.bob.vroom.auditor_pool import AuditorPool
    from claude_orchestrator.bob.vroom.daemon import VroomDaemon
    from claude_orchestrator.bob.vroom.auditors.semgrep import SemgrepAuditor
    from claude_orchestrator.bob.vroom.audit_cycle import VroomAuditCycle
    from claude_orchestrator.bob.vroom.fix_loop import FixLoopDriver
    from claude_orchestrator.bob.vroom.triage import VroomTriageGate

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    install_handlers()

    use_stub = os.environ.get("BOB_USE_STUB_VROOM", "0") == "1"
    if use_stub:
        from claude_orchestrator.bob.vroom.auditors.llm_stubs import (
            CodexSecurityAuditorStub,
        )

        class _ClaudeStub:
            id = "claude_architect"

            def triggers_on(self, changed_files):
                return True

            def audit(self, workspace, changed_files):
                return []

        claude_aud = _ClaudeStub()
    else:
        from claude_orchestrator.bob.vroom.auditors.claude_architect import ClaudeArchitectAuditor
        claude_aud = ClaudeArchitectAuditor()

    if use_stub:
        from claude_orchestrator.bob.vroom.auditors.llm_stubs import CodexSecurityAuditorStub
        codex_aud = CodexSecurityAuditorStub()
    else:
        from claude_orchestrator.bob.vroom.auditors.codex_security import CodexSecurityAuditor
        codex_aud = CodexSecurityAuditor()
    pool = AuditorPool([SemgrepAuditor(), claude_aud, codex_aud])

    triage_gate = VroomTriageGate()

    # The fix-loop spawns isolated McLoops on vroom/<id> branches.
    from claude_orchestrator.bob.mcloop.runner import McLoopRunner
    from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
    from claude_orchestrator.bob.sandbox.host import HostExecutor

    runner = McLoopRunner(
        claude_cmd="claude",
        max_iterations=10,
        executor=HostExecutor(),
    )
    verifier = PythonPytestVerifier()

    def run_mcloop_for_finding(*, branch_name: str, workspace: Path, finding) -> bool:
        from claude_orchestrator.models import (
            Feature, FeatureStatus, TaskType, VerificationPlan,
        )
        feature = Feature(
            id=0,
            name=f"fix-{finding.rule_id}",
            description=f"Fix: {finding.message}",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["all tests pass"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )
        vroom_feature_dir = project_root / ".bob" / "vroom-features" / branch_name.replace("/", "-")
        vroom_feature_dir.mkdir(parents=True, exist_ok=True)
        for f in ("spec.md", "activity.md", "failed_attempts.md", "verifier-results.jsonl"):
            (vroom_feature_dir / f).write_text("")
        master_spec = project_root / ".bob" / "spec.md"
        if not master_spec.exists():
            master_spec.write_text("# (vroom)\n")

        result = runner.run(
            feature=feature,
            workspace=workspace,
            master_spec=master_spec,
            feature_dir=vroom_feature_dir,
            verifier=verifier,
        )
        return result.outcome == "exit_signal"

    fix_driver = FixLoopDriver(
        repo=project_root,
        run_mcloop=run_mcloop_for_finding,
    )

    cycle = VroomAuditCycle(
        project_root=project_root,
        auditor_pool=pool,
        triage_gate=triage_gate,
        fix_driver=fix_driver,
    )

    daemon = VroomDaemon(
        project_root=project_root,
        audit_cycle=cycle.run,
        timer_interval_s=args.interval,
    )
    daemon.write_pid()
    print(f"vroom daemon started (pid: {os.getpid()}, interval: {args.interval}s)")
    print("Ctrl-C to stop")

    try:
        while not is_shutdown_requested():
            daemon.run_one_iteration()
            time.sleep(min(args.interval, 5))
    finally:
        daemon.remove_pid()
    print("vroom daemon stopped")
    return 0


def _cmd_vroom_stop(args: argparse.Namespace) -> int:
    """Stop the running Vroom daemon by sending SIGTERM."""
    import signal as signal_mod
    project_root = Path(args.project).resolve()
    pid_path = project_root / ".bob" / "vroom.pid"
    if not pid_path.exists():
        print(f"no Vroom daemon running (no {pid_path})")
        return 1
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        print(f"malformed pid file: {pid_path}", file=sys.stderr)
        return 2

    try:
        os.kill(pid, signal_mod.SIGTERM)
        print(f"sent SIGTERM to vroom daemon (pid: {pid})")
        return 0
    except ProcessLookupError:
        print(f"vroom daemon (pid {pid}) is already dead; cleaning up pid file")
        pid_path.unlink(missing_ok=True)
        return 0


def _cmd_vroom_now(args: argparse.Namespace) -> int:
    """Run one audit cycle synchronously and exit."""
    from claude_orchestrator.bob.vroom.auditor_pool import AuditorPool
    from claude_orchestrator.bob.vroom.auditors.semgrep import SemgrepAuditor
    from claude_orchestrator.bob.vroom.audit_cycle import VroomAuditCycle
    from claude_orchestrator.bob.vroom.triage import VroomTriageGate

    project_root = Path(args.project).resolve()

    use_stub = os.environ.get("BOB_USE_STUB_VROOM", "0") == "1"
    if use_stub:
        class _ClaudeStub:
            id = "claude_architect"

            def triggers_on(self, changed_files):
                return True

            def audit(self, workspace, changed_files):
                return []

        claude_aud = _ClaudeStub()
    else:
        from claude_orchestrator.bob.vroom.auditors.claude_architect import ClaudeArchitectAuditor
        claude_aud = ClaudeArchitectAuditor()

    if use_stub:
        from claude_orchestrator.bob.vroom.auditors.llm_stubs import CodexSecurityAuditorStub
        codex_aud = CodexSecurityAuditorStub()
    else:
        from claude_orchestrator.bob.vroom.auditors.codex_security import CodexSecurityAuditor
        codex_aud = CodexSecurityAuditor()
    pool = AuditorPool([SemgrepAuditor(), claude_aud, codex_aud])

    triage_gate = VroomTriageGate()
    # No fix_driver in `vroom now` — keep the cycle to "audit + persist + triage" without
    # actually running a fix-loop, so the user can review then run again with --fix.
    cycle = VroomAuditCycle(
        project_root=project_root,
        auditor_pool=pool,
        triage_gate=triage_gate,
        fix_driver=None,
    )
    clusters = cycle.run()
    print(f"vroom cycle complete: {len(clusters)} clusters")
    for c in clusters[:10]:
        primary = c.findings[0]
        print(f"  [{c.severity}] {primary.rule_id} at {primary.location.uri}:"
              f"{primary.location.start_line} (consensus {c.consensus_count})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bob")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a Bob orchestration")
    run.add_argument("--project", default=".", help="project root (default: cwd)")
    run.add_argument("--inputs", required=False,
                     help="path to a markdown spec or directory of inputs (M1: markdown only)")
    run.add_argument("--max-iterations", type=int, default=30,
                     help="max McLoop iterations per feature (default: 30)")
    run.add_argument("--max-cost", type=float, default=None,
                     help="optional USD cap (advisory in subscription mode)")
    run.add_argument("--no-gate", action="append", default=[],
                     help="disable a HITL gate by name (repeatable)")
    run.add_argument(
        "--sandbox",
        choices=["host", "docker"],
        default=None,  # None means "fall back to env var or default"
        help="sandbox tier (default: host; or BOB_SANDBOX_TIER env var)",
    )
    run.add_argument(
        "--vroom",
        action="store_true",
        help="run continuous Vroom audit loop in parallel with the feature loop",
    )
    run.add_argument(
        "--yolo",
        action="store_true",
        help="enable YOLO mode (unattended; requires --sandbox docker and --max-cost)",
    )
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show current Bob state")
    status.add_argument("--project", default=".", help="project root (default: cwd)")
    status.set_defaults(func=_cmd_status)

    validate = sub.add_parser("validate", help="parse and validate a spec file")
    validate.add_argument("--inputs", required=True,
                          help="path to a markdown spec")
    validate.set_defaults(func=_cmd_validate)

    vroom = sub.add_parser("vroom", help="run/stop the Vroom audit daemon")
    vroom_sub = vroom.add_subparsers(dest="vroom_cmd", required=False)

    now = vroom_sub.add_parser("now", help="trigger one Vroom cycle and exit")
    now.add_argument("--project", default=".", help="project root (default: cwd)")
    now.set_defaults(func=_cmd_vroom_now)

    stop = vroom_sub.add_parser("stop", help="stop the running Vroom daemon")
    stop.add_argument("--project", default=".", help="project root (default: cwd)")
    stop.set_defaults(func=_cmd_vroom_stop)

    # Default action when `bob vroom` is invoked without a subcommand: start the daemon.
    vroom.add_argument("--project", default=".", help="project root (default: cwd)")
    vroom.add_argument("--interval", type=int, default=1800, help="seconds between timer-driven cycles (default: 1800)")
    vroom.set_defaults(func=_cmd_vroom_start)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
