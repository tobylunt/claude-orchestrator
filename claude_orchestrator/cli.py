"""CLI entry point: orchestrate run|parse-spec|status."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="orchestrate",
        description="Claude Code orchestrator -- automated feature implementation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- run ---
    run_parser = subparsers.add_parser("run", help="Run the orchestration loop")
    run_parser.add_argument(
        "--project", "-p", type=str, default=".",
        help="Project directory (default: current dir)",
    )
    run_parser.add_argument(
        "--from", dest="start_from_feature", type=int,
        help="Start from feature N",
    )
    run_parser.add_argument(
        "--stop-after", dest="stop_after_feature", type=int,
        help="Stop after feature N",
    )
    run_parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would run without executing",
    )
    run_parser.add_argument("--model", type=str, help="Model override")
    run_parser.add_argument("--max-retries", dest="max_retries", type=int, help="Max retries per feature")
    run_parser.add_argument(
        "--no-commit", action="store_true",
        help="Disable auto-commit",
    )
    run_parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging",
    )

    # --- parse-spec ---
    spec_cmd = subparsers.add_parser(
        "parse-spec", help="Parse a spec.md into features.json",
    )
    spec_cmd.add_argument("spec_file", type=str, help="Path to spec markdown file")
    spec_cmd.add_argument(
        "--output", "-o", type=str, default="features.json",
        help="Output features.json path",
    )
    spec_cmd.add_argument(
        "--model", type=str, default="opus",
        help="Model for spec parsing",
    )

    # --- status ---
    status_cmd = subparsers.add_parser("status", help="Show current progress")
    status_cmd.add_argument(
        "--project", "-p", type=str, default=".",
        help="Project directory",
    )

    args = parser.parse_args()

    if args.command == "run":
        _run(args)
    elif args.command == "parse-spec":
        _parse_spec(args)
    elif args.command == "status":
        _status(args)


def _run(args: argparse.Namespace) -> None:
    from .config import load_config
    from .orchestrator import Orchestrator

    cli_args = {
        "project": args.project,
        "start_from_feature": args.start_from_feature,
        "stop_after_feature": args.stop_after_feature,
        "dry_run": args.dry_run if args.dry_run else None,
        "model": args.model,
        "max_retries": args.max_retries,
    }
    config = load_config(cli_args)
    if args.no_commit:
        config.auto_commit = False
    if args.verbose:
        config.log_level = "DEBUG"

    orchestrator = Orchestrator(config)
    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        # Signal handler already cleaned up — just exit cleanly
        pass


def _parse_spec(args: argparse.Namespace) -> None:
    from .spec_parser import parse_spec

    spec_path = Path(args.spec_file).resolve()
    output_path = Path(args.output).resolve()
    features = asyncio.run(parse_spec(spec_path, output_path, args.model))
    print(f"Parsed {len(features)} features -> {output_path}")
    for f in features:
        print(f"  #{f.id}: {f.name} ({len(f.steps)} steps)")


def _status(args: argparse.Namespace) -> None:
    from .config import load_config
    from .state import StateManager

    project_dir = Path(args.project).resolve()
    config = load_config({"project": str(project_dir)})
    state = StateManager(
        features_path=project_dir / config.features_file,
        progress_path=project_dir / config.progress_file,
    )
    features = state.load_features()
    print(state.get_progress_summary())
    print()
    for f in features:
        symbol = "PASS" if f.passes else "----"
        extra = ""
        if f.status.value == "failed":
            extra = f" [FAILED x{f.attempts}]"
        elif f.status.value == "skipped":
            extra = " [SKIPPED]"
        print(f"  [{symbol}] #{f.id}: {f.name}{extra}")


def cli_entry() -> None:
    """Entry point for pyproject.toml console_scripts."""
    main()
