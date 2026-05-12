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
    from claude_orchestrator.bob.dotenv_loader import load_env_files
    from claude_orchestrator.bob.process_lock import (
        Lock, LockHeld, StalePidDetected, acquire_lock, release_lock,
    )
    from claude_orchestrator.bob.run_config import RunConfig
    from claude_orchestrator.bob.signals import (
        install_handlers, register_cleanup,
    )
    from claude_orchestrator.bob.wiring import (
        build_coordinator_from_run_config,
        build_vroom_subprocess_invocation,
    )
    from claude_orchestrator.bob.yolo import YoloInvariantError

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    # Auto-load .env files (highest priority: process env; lowest: cwd/.env).
    load_env_files(project_root=project_root, cwd=Path.cwd())

    if not args.inputs:
        print("error: --inputs is required (path to a markdown spec)", file=sys.stderr)
        return 2
    try:
        config = RunConfig.from_args(args)
    except YoloInvariantError as e:
        print(f"yolo error: {e}", file=sys.stderr)
        return 5
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    spec_path = config.spec_path
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

    if config.yolo.enabled:
        print(f"YOLO mode enabled: sandbox={config.yolo.sandbox_tier} "
              f"max_cost=${config.yolo.max_cost} "
              f"max_inconclusive={config.yolo.max_inconclusive} "
              f"vroom_severity={config.yolo.vroom_severity}")

    from claude_orchestrator.bob.observability import setup_tracing
    setup_tracing(
        service_name="bob",
        otlp_endpoint=config.otel_endpoint,
    )

    coord = build_coordinator_from_run_config(config)

    # If --vroom is set, spawn the Vroom daemon as a subprocess.
    vroom_proc = None
    if config.vroom:
        import subprocess as _subprocess
        vroom_cmd, child_env = build_vroom_subprocess_invocation(config)
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
    from claude_orchestrator.bob.dotenv_loader import load_env_files
    from claude_orchestrator.bob.duplo.markdown_parser import (
        SpecParseError,
        parse_markdown_spec,
    )

    # Auto-load .env from cwd (no project_root for validate).
    load_env_files(project_root=None, cwd=Path.cwd())

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
    import uuid
    from claude_orchestrator.bob.cost_tracker import set_run_context
    from claude_orchestrator.bob.dotenv_loader import load_env_files
    from claude_orchestrator.bob.observability import setup_tracing
    from claude_orchestrator.bob.signals import install_handlers, is_shutdown_requested
    from claude_orchestrator.bob.vroom_config import VroomConfig
    from claude_orchestrator.bob.wiring import build_vroom_daemon
    from claude_orchestrator.bob.yolo import YoloInvariantError

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    # Auto-load .env files before reading any env vars.
    load_env_files(project_root=project_root, cwd=Path.cwd())
    try:
        config = VroomConfig.from_daemon_args(args)
    except YoloInvariantError as e:
        print(f"yolo error: {e}", file=sys.stderr)
        return 5
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Wire OTEL in the subprocess so span() calls actually emit.
    # Without this, the daemon's bob.vroom.cycle / bob.mcloop.iter spans are
    # silent no-ops even when the parent process emits to a backend.
    setup_tracing(service_name="bob-vroom")

    # Set the cost-tracking run context so auditor API calls land in
    # costs.jsonl. The daemon doesn't go through Coordinator.
    bob_dir = project_root / ".bob"
    bob_dir.mkdir(parents=True, exist_ok=True)
    daemon_run_id = f"vroom-daemon-{uuid.uuid4()}"
    set_run_context(run_id=daemon_run_id, bob_dir=bob_dir)

    install_handlers()
    daemon = build_vroom_daemon(config)
    daemon.write_pid()
    print(
        f"vroom daemon started (pid: {os.getpid()}, "
        f"interval: {config.timer_interval_s}s)"
    )
    print("Ctrl-C to stop")

    try:
        while not is_shutdown_requested():
            daemon.run_one_iteration()
            time.sleep(min(config.timer_interval_s, 5))
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
    import uuid
    from claude_orchestrator.bob.cost_tracker import set_run_context
    from claude_orchestrator.bob.dotenv_loader import load_env_files
    from claude_orchestrator.bob.observability import setup_tracing
    from claude_orchestrator.bob.vroom_config import VroomConfig
    from claude_orchestrator.bob.wiring import build_vroom_audit_cycle
    from claude_orchestrator.bob.yolo import YoloInvariantError

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    # Auto-load .env files before reading any env vars.
    load_env_files(project_root=project_root, cwd=Path.cwd())
    try:
        config = VroomConfig.from_now_args(args)
    except YoloInvariantError as e:
        print(f"yolo error: {e}", file=sys.stderr)
        return 5
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # Same OTEL hookup as _cmd_vroom_start; without this, vroom spans are no-ops.
    setup_tracing(service_name="bob-vroom")

    # Set the cost-tracking run context so auditor API calls land in
    # costs.jsonl. `bob vroom now` doesn't go through Coordinator, so we
    # must establish the context ourselves.
    bob_dir = project_root / ".bob"
    bob_dir.mkdir(parents=True, exist_ok=True)
    vroom_run_id = f"vroom-{uuid.uuid4()}"
    set_run_context(run_id=vroom_run_id, bob_dir=bob_dir)

    # No fix_driver in `vroom now` — keep the cycle to "audit + persist + triage" without
    # actually running a fix-loop, so the user can review then run again with --fix.
    cycle = build_vroom_audit_cycle(config, include_fix_driver=False)
    clusters = cycle.run()
    print(f"vroom cycle complete: {len(clusters)} clusters")
    for c in clusters[:10]:
        primary = c.findings[0]
        print(f"  [{c.severity}] {primary.rule_id} at {primary.location.uri}:"
              f"{primary.location.start_line} (consensus {c.consensus_count})")
    return 0


def _cmd_costs(args: argparse.Namespace) -> int:
    """Aggregate and display cost data."""
    from claude_orchestrator.bob.cost_tracker import aggregate_costs

    project_root = Path(args.project).resolve()
    bob_dir = project_root / ".bob"

    group_field = {
        "run": "run_id",
        "provider": "provider",
        "phase": "phase",
        "model": "model",
    }[args.by]

    agg = aggregate_costs(bob_dir, group_by=group_field)
    if agg["total_calls"] == 0:
        print(f"No cost data recorded yet (no {bob_dir}/costs.jsonl).")
        return 0

    print(f"Bob cost summary ({project_root})")
    print()
    total = agg["total_cost_usd"]
    print(f"  Total: ${total:.2f}  ({agg['total_calls']} calls, "
          f"{agg['total_tokens_in']:,} input tokens, "
          f"{agg['total_tokens_out']:,} output tokens)")
    print()
    if agg.get("groups"):
        print(f"  By {args.by}:")
        # Sort groups by total cost descending.
        items = sorted(
            agg["groups"].items(),
            key=lambda kv: kv[1]["total_cost_usd"],
            reverse=True,
        )
        for key, sub in items:
            display_key = key[:12] + "…" if len(key) > 12 else key
            print(f"    {display_key:<14}  ${sub['total_cost_usd']:>6.2f}  "
                  f"{sub['total_calls']:>3} calls  "
                  f"{sub['total_tokens_in']:>8,} in / {sub['total_tokens_out']:>7,} out")
    return 0


def _cmd_runs(args: argparse.Namespace) -> int:
    """Show recent Bob runs."""
    from claude_orchestrator.bob.state_io import read_jsonl
    from claude_orchestrator.bob.cost_tracker import aggregate_costs

    project_root = Path(args.project).resolve()
    bob_dir = project_root / ".bob"
    log_path = bob_dir / "run-log.jsonl"

    if not log_path.exists():
        print(f"No runs recorded yet (no {log_path}).")
        return 0

    # Group events by run_id.
    runs: dict[str, dict] = {}
    for event in read_jsonl(log_path):
        rid = event.get("run_id")
        if not rid:
            continue
        run = runs.setdefault(rid, {
            "run_id": rid,
            "started": None,
            "finished": None,
            "status": "in_progress",
            "feature_outcomes": [],
        })
        if event["event"] == "run_started":
            run["started"] = event["ts"]
            otel_ep = event.get("otel_endpoint")
            if otel_ep:
                run["otel_endpoint"] = otel_ep
        elif event["event"] == "run_finished":
            run["finished"] = event["ts"]
            run["status"] = "finished"
        elif event["event"] == "run_aborted":
            run["finished"] = event["ts"]
            run["status"] = "aborted"
        elif event["event"] == "feature_merged":
            run["feature_outcomes"].append("merged")
        elif event["event"] == "feature_rejected":
            run["feature_outcomes"].append("rejected")
        elif event["event"] == "feature_failed":
            run["feature_outcomes"].append("failed")

    # Pull cost data per run.
    cost_agg = aggregate_costs(bob_dir, group_by="run_id")
    per_run_cost = {
        rid: data["total_cost_usd"]
        for rid, data in cost_agg.get("groups", {}).items()
    }

    # Sort by start time descending.
    ordered = sorted(
        runs.values(),
        key=lambda r: r.get("started") or "",
        reverse=True,
    )
    if args.limit > 0:
        ordered = ordered[:args.limit]

    if not ordered:
        print(f"No runs found in {log_path}.")
        return 0

    print(f"Recent runs in {project_root}:\n")
    print(f"  {'ID':<13}  {'Started':<20}  {'Duration':<9}  {'Status':<10}  "
          f"{'Features':<22}  {'Cost':<7}  Trace")
    for run in ordered:
        rid = run["run_id"]
        rid_display = rid[:8] + "…" if len(rid) > 8 else rid
        started = run.get("started", "")[:19].replace("T", " ")
        duration = "—"
        if run.get("started") and run.get("finished"):
            try:
                from datetime import datetime
                t0 = datetime.fromisoformat(run["started"])
                t1 = datetime.fromisoformat(run["finished"])
                secs = (t1 - t0).total_seconds()
                duration = f"{int(secs)}s" if secs < 60 else f"{int(secs / 60)}m{int(secs % 60)}s"
            except (ValueError, TypeError):
                pass
        status = run["status"]
        outcomes = run["feature_outcomes"]
        if outcomes:
            from collections import Counter
            counts = Counter(outcomes)
            features = ", ".join(f"{n} {k}" for k, n in counts.items())
        else:
            features = "—"
        cost = per_run_cost.get(rid)
        cost_str = f"${cost:.2f}" if cost is not None else "—"
        trace = run.get("otel_endpoint", "—")
        if trace != "—" and len(trace) > 40:
            trace = trace[:37] + "…"
        print(f"  {rid_display:<13}  {started:<20}  {duration:<9}  "
              f"{status:<10}  {features:<22}  {cost_str:<7}  {trace}")
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
        choices=["host", "docker", "devcontainer"],
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
    run.add_argument(
        "--otel-endpoint",
        default=None,
        help="OTLP traces endpoint (default: $OTEL_EXPORTER_OTLP_ENDPOINT). Example: http://localhost:6006/v1/traces",
    )
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show current Bob state")
    status.add_argument("--project", default=".", help="project root (default: cwd)")
    status.set_defaults(func=_cmd_status)

    validate = sub.add_parser("validate", help="parse and validate a spec file")
    validate.add_argument("--inputs", required=True,
                          help="path to a markdown spec")
    validate.set_defaults(func=_cmd_validate)

    costs = sub.add_parser("costs", help="aggregate Bob cost data from .bob/costs.jsonl")
    costs.add_argument("--project", default=".", help="project root (default: cwd)")
    costs.add_argument(
        "--by",
        choices=["run", "provider", "phase", "model"],
        default="run",
        help="grouping (default: run)",
    )
    costs.set_defaults(func=_cmd_costs)

    runs = sub.add_parser("runs", help="show recent Bob runs from .bob/run-log.jsonl")
    runs.add_argument("--project", default=".", help="project root (default: cwd)")
    runs.add_argument(
        "--limit",
        type=int,
        default=10,
        help="number of recent runs to show (0 = all; default: 10)",
    )
    runs.set_defaults(func=_cmd_runs)

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
    vroom.add_argument(
        "--watch-main-ref",
        action="store_true",
        help="trigger a cycle when .git/refs/heads/main changes (post-merge detection)",
    )
    vroom.add_argument(
        "--sandbox",
        choices=["host", "docker", "devcontainer"],
        default=None,
        help="sandbox tier for the fix-loop McLoop (default: $BOB_SANDBOX_TIER or host)",
    )
    vroom.set_defaults(func=_cmd_vroom_start)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
