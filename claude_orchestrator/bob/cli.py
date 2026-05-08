"""Bob CLI — subcommands `run`, `status`.

Invoked via `python -m claude_orchestrator.bob.cli` or as `bob`
(when registered in pyproject.toml's [project.scripts]).
"""

from __future__ import annotations

import argparse
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
    if not spec_path.is_file():
        print(f"error: input spec not found: {spec_path}", file=sys.stderr)
        return 2

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

    # Inputs dir: copy or symlink the markdown spec into .bob/inputs/ so it's
    # captured for posterity. (M2 proper handles arbitrary multimodal inputs.)
    bob_dir.mkdir(parents=True, exist_ok=True)
    (bob_dir / "inputs").mkdir(exist_ok=True)
    captured = bob_dir / "inputs" / spec_path.name
    if captured.resolve() != spec_path.resolve():
        captured.write_bytes(spec_path.read_bytes())

    coord = build_coordinator(
        project_root=project_root,
        spec_path=spec_path,
        max_iterations=args.max_iterations,
        disabled_gates=set(args.no_gate),
    )
    coord.run(RunScope(includes_duplo=True))
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
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show current Bob state")
    status.add_argument("--project", default=".", help="project root (default: cwd)")
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
