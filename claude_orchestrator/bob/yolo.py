"""YOLO mode — single-flag opt-in for unattended overnight runs.

Per spec §6.9, enabling YOLO has documented downstream effects:
- Post-Duplo HITL gate: auto-approve if meta-rubric passed
- Orchestra disagreement HITL: auto-take judge's tentative verdict
- Vroom triage HITL: auto-approve at/above severity threshold
- McLoop Inconclusive: feed back into loop, bounded by max_inconclusive
- Sandbox: required tier 2 (docker)
- max_cost: required (advisory in subscription mode, hard ceiling in api mode)

The integration of these effects is progressive — M3 ships the config object
and CLI wiring; specific gates and the McLoop runner consume it as their
behavior is updated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal


class YoloInvariantError(ValueError):
    """A YOLO config violates a required invariant."""


_VALID_SEVERITIES = ("info", "low", "medium", "high", "critical")


@dataclass
class YoloConfig:
    enabled: bool = False
    sandbox_tier: str = "host"
    max_cost: float | None = None
    max_inconclusive: int = 3
    vroom_severity: Literal["info", "low", "medium", "high", "critical"] = "high"
    notify_channel: str | None = None  # M4: email/slack/desktop

    def __post_init__(self) -> None:
        if not self.enabled:
            return  # invariants only apply when YOLO is on

        # Required tier 2+ sandbox.
        if self.sandbox_tier not in ("docker", "devcontainer"):
            raise YoloInvariantError(
                f"YOLO requires sandbox_tier='docker' or 'devcontainer' (tier 2 or 3); "
                f"got {self.sandbox_tier!r}. Use --sandbox docker or --sandbox devcontainer."
            )

        # Required max_cost.
        if self.max_cost is None:
            raise YoloInvariantError(
                "YOLO requires max_cost to be set (advisory ceiling in subscription mode, "
                "hard stop in api mode). Use --max-cost <usd>."
            )

        if self.vroom_severity not in _VALID_SEVERITIES:
            raise YoloInvariantError(
                f"vroom_severity must be one of {_VALID_SEVERITIES}; got {self.vroom_severity!r}"
            )

    @classmethod
    def from_env(
        cls,
        *,
        enabled: bool = False,
        sandbox_tier: str = "host",
        max_cost: float | None = None,
    ) -> "YoloConfig":
        return cls(
            enabled=enabled,
            sandbox_tier=sandbox_tier,
            max_cost=max_cost,
            max_inconclusive=int(os.environ.get("BOB_YOLO_MAX_INCONCLUSIVE", "3")),
            vroom_severity=os.environ.get("BOB_YOLO_VROOM_SEVERITY", "high"),  # type: ignore[arg-type]
            notify_channel=os.environ.get("BOB_YOLO_NOTIFY"),
        )
