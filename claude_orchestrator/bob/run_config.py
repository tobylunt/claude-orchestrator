"""Typed config for `bob run`.

The CLI resolves argparse and environment state once, then passes this object
to application wiring. Keeping this pure makes sandbox/YOLO/gate propagation
testable without spinning up a full Bob run.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from claude_orchestrator.bob.yolo import YoloConfig


VALID_SANDBOX_TIERS = ("host", "docker", "devcontainer")


def resolve_sandbox_tier(
    *,
    cli_value: str | None,
    env: dict[str, str] | None = None,
    default: str = "host",
) -> str:
    """Resolve a sandbox tier with CLI > env > default precedence."""
    if env is None:
        env = os.environ
    tier = cli_value or env.get("BOB_SANDBOX_TIER") or default
    if tier not in VALID_SANDBOX_TIERS:
        raise ValueError(
            f"unknown sandbox tier: {tier!r} "
            f"(must be one of {VALID_SANDBOX_TIERS})"
        )
    return tier


@dataclass(frozen=True)
class RunConfig:
    """Resolved configuration for one `bob run` invocation."""

    project_root: Path
    spec_path: Path
    max_iterations: int
    max_cost: float | None
    sandbox_tier: str
    yolo: YoloConfig
    disabled_gates: frozenset[str]
    vroom: bool
    otel_endpoint: str | None

    @classmethod
    def from_args(
        cls,
        args: argparse.Namespace,
        *,
        env: dict[str, str] | None = None,
    ) -> "RunConfig":
        """Build from parsed `bob run` args.

        Path existence is still a CLI concern so errors can be printed with
        user-facing messages. Missing `--inputs` is rejected here too so no
        downstream layer can silently treat the project root as the spec.
        """
        if env is None:
            env = os.environ

        inputs = getattr(args, "inputs", None)
        if not inputs:
            raise ValueError("--inputs is required")

        sandbox_tier = resolve_sandbox_tier(
            cli_value=getattr(args, "sandbox", None),
            env=env,
        )
        yolo = YoloConfig.from_env(
            enabled=bool(getattr(args, "yolo", False)),
            sandbox_tier=sandbox_tier,
            max_cost=getattr(args, "max_cost", None),
            env=env,
        )

        otel_endpoint = (
            getattr(args, "otel_endpoint", None)
            or env.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        )

        return cls(
            project_root=Path(args.project).resolve(),
            spec_path=Path(inputs).resolve(),
            max_iterations=int(getattr(args, "max_iterations", 30)),
            max_cost=getattr(args, "max_cost", None),
            sandbox_tier=sandbox_tier,
            yolo=yolo,
            disabled_gates=frozenset(getattr(args, "no_gate", []) or []),
            vroom=bool(getattr(args, "vroom", False)),
            otel_endpoint=otel_endpoint,
        )
