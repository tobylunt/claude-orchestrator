"""Typed config for Vroom commands."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from claude_orchestrator.bob.run_config import resolve_sandbox_tier
from claude_orchestrator.bob.yolo import YoloConfig


def yolo_from_subprocess_env(
    *,
    sandbox_tier: str,
    env: dict[str, str] | None = None,
) -> YoloConfig | None:
    """Reconstruct the parent's YOLO config inside a Vroom subprocess."""
    if env is None:
        env = os.environ
    if env.get("BOB_VROOM_YOLO_ENABLED") != "1":
        return None
    return YoloConfig(
        enabled=True,
        sandbox_tier=sandbox_tier,
        max_cost=float(env.get("BOB_VROOM_YOLO_MAX_COST", "999999.0")),
        max_inconclusive=int(env.get("BOB_YOLO_MAX_INCONCLUSIVE", "3")),
        vroom_severity=env.get("BOB_VROOM_YOLO_SEVERITY", "high"),  # type: ignore[arg-type]
        notify_channel=env.get("BOB_YOLO_NOTIFY"),
    )


@dataclass(frozen=True)
class VroomConfig:
    """Resolved configuration for one Vroom daemon or one-shot invocation."""

    project_root: Path
    sandbox_tier: str
    use_stub: bool
    yolo: YoloConfig | None
    timer_interval_s: int = 1800
    watch_main_ref: bool = False

    @classmethod
    def from_daemon_args(
        cls,
        args: argparse.Namespace,
        *,
        env: dict[str, str] | None = None,
    ) -> "VroomConfig":
        """Build from `bob vroom` daemon args.

        Sandbox precedence is `--sandbox`, then `BOB_SANDBOX_TIER`, then host.
        """
        if env is None:
            env = os.environ
        sandbox_tier = resolve_sandbox_tier(
            cli_value=getattr(args, "sandbox", None),
            env=env,
            default="host",
        )
        return cls(
            project_root=Path(args.project).resolve(),
            sandbox_tier=sandbox_tier,
            use_stub=env.get("BOB_USE_STUB_VROOM", "0") == "1",
            yolo=yolo_from_subprocess_env(sandbox_tier=sandbox_tier, env=env),
            timer_interval_s=int(getattr(args, "interval", 1800)),
            watch_main_ref=bool(getattr(args, "watch_main_ref", False)),
        )

    @classmethod
    def from_now_args(
        cls,
        args: argparse.Namespace,
        *,
        env: dict[str, str] | None = None,
    ) -> "VroomConfig":
        """Build from `bob vroom now` args.

        `vroom now` historically defaulted YOLO reconstruction to docker when
        no sandbox env var was set. Preserve that default even though the
        one-shot command does not create a fix-loop executor.
        """
        if env is None:
            env = os.environ
        sandbox_tier = resolve_sandbox_tier(
            cli_value=None,
            env=env,
            default="docker",
        )
        return cls(
            project_root=Path(args.project).resolve(),
            sandbox_tier=sandbox_tier,
            use_stub=env.get("BOB_USE_STUB_VROOM", "0") == "1",
            yolo=yolo_from_subprocess_env(sandbox_tier=sandbox_tier, env=env),
        )
