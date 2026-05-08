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
    coord = build_coordinator(
        project_root=project_root,
        spec_path=spec_path,
        max_iterations=args.max_iterations,
        disabled_gates=set(args.no_gate),
        sandbox_tier=sandbox_tier,
    )
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
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show current Bob state")
    status.add_argument("--project", default=".", help="project root (default: cwd)")
    status.set_defaults(func=_cmd_status)

    validate = sub.add_parser("validate", help="parse and validate a spec file")
    validate.add_argument("--inputs", required=True,
                          help="path to a markdown spec")
    validate.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
