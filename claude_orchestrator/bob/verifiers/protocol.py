"""The Verifier protocol — the most important contract in Bob (see spec §6.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from claude_orchestrator.models import Feature, TaskType


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    missing_tools: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    """The output of a single verifier run.

    Status semantics:
      ok            -- the work passes the rubric
      fail          -- the work definitely doesn't pass; agent should iterate
      inconclusive  -- the verifier could not decide (HALT LOUD by default)
    """
    status: Literal["ok", "fail", "inconclusive"]
    reason: str
    artifacts: list[Path]
    coverage_notes: str | None

    def __post_init__(self) -> None:
        if self.status not in ("ok", "fail", "inconclusive"):
            raise ValueError(f"invalid status: {self.status!r}")


class Verifier(Protocol):
    """The protocol every verifier implements. Python's runtime Protocol."""

    id: str

    def applies_to(self) -> list[TaskType]: ...
    def required_tools(self) -> list[str]: ...
    def preflight(self, workspace: Path) -> PreflightResult: ...
    def verify(self, workspace: Path, feature: Feature) -> VerifyResult: ...
