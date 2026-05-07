"""Data models for the orchestrator."""

from __future__ import annotations

from datetime import datetime
import sys
from enum import Enum

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    class StrEnum(str, Enum):  # type: ignore[no-redef]
        """Backport of StrEnum for Python < 3.11.

        Important: overriding __str__ to return self.value matches stdlib
        StrEnum behavior. Without this, str(member) returns the default
        Enum repr (e.g. 'FeatureStatus.PENDING') instead of the value
        ('pending'), which silently corrupts JSON serialization on 3.10.
        """

        def __str__(self) -> str:
            return self.value
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Legacy (pre-Bob) types — kept for backward compatibility with orchestrator.py,
# runner.py, state.py, and existing tests.
# ---------------------------------------------------------------------------


class LegacyFeatureStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class LegacyFeature(BaseModel):
    """A single feature to implement. Backward-compatible with {id, name, passes, steps} format."""

    id: int
    name: str
    passes: bool = False
    steps: list[str] = Field(default_factory=list)
    status: LegacyFeatureStatus = LegacyFeatureStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    last_session_id: str | None = None
    commit_hash: str | None = None


class FeatureResult(BaseModel):
    """Result of executing a single feature."""

    feature_id: int
    success: bool
    error: str | None = None
    session_id: str | None = None
    commit_hash: str | None = None
    duration_seconds: float = 0.0
    cost_usd: float | None = None
    retries_used: int = 0


class ProgressEntry(BaseModel):
    """A single entry in the progress log."""

    timestamp: datetime
    feature_id: int
    feature_name: str
    status: LegacyFeatureStatus
    summary: str
    commit_hash: str | None = None
    session_id: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Bob-era phase contracts
# ---------------------------------------------------------------------------


class TaskType(StrEnum):
    """Open enum: built-in values are conveniences; CUSTOM + verifier_id covers the rest."""

    UI = "ui"
    DATA_ANALYSIS = "data_analysis"
    GEOSPATIAL = "geospatial"
    LIBRARY = "library"
    CLI = "cli"
    INTEGRATION = "integration"
    ML_TRAINING = "ml_training"
    INFRASTRUCTURE = "infrastructure"
    CUSTOM = "custom"


class FeatureStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    MCLOOP_DONE = "mcloop_done"
    ORCHESTRA_PENDING = "orchestra_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"


class VerificationPlan(BaseModel):
    """A feature's declared verification approach."""

    verifier_id: str = Field(..., description="Registered verifier id, e.g. 'python_pytest'")
    success_criteria: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class InputRef(BaseModel):
    """A reference to a multimodal input the user provided to Duplo."""

    kind: Literal["file", "url", "text"]
    value: str
    description: str | None = None


class Feature(BaseModel):
    """One unit of work, scoped to its own worktree and branch."""

    model_config = ConfigDict(use_enum_values=True)

    id: int
    name: str
    description: str
    task_type: TaskType
    verification_plan: VerificationPlan
    branch: str | None = None
    worktree_path: Path | None = None
    status: FeatureStatus = FeatureStatus.PENDING
    attempts: int = 0
    cost_usd: float = 0.0
    last_error: str | None = None
    updated_at: datetime | None = None


class Spec(BaseModel):
    """The master spec produced by Duplo."""

    model_config = ConfigDict(use_enum_values=True)

    title: str
    motivation: str
    inputs: list[InputRef] = Field(default_factory=list)
    features: list[Feature]
    rubric_meta_check_passed: bool = False


class Verdict(BaseModel):
    """Orchestra's per-feature decision."""

    model_config = ConfigDict(use_enum_values=True)

    feature_id: int
    decision: Literal["approve", "reject", "abstain"]
    confidence: float = Field(ge=0.0, le=1.0)
    debate_log_path: Path
    judge_reasoning: str


class SARIFLocation(BaseModel):
    """Subset of SARIF physicalLocation/region."""

    uri: str
    start_line: int
    end_line: int | None = None


class Finding(BaseModel):
    """SARIF-compatible subset for Vroom output. (Vroom is M3, but the type ships now.)"""

    model_config = ConfigDict(use_enum_values=True)

    rule_id: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    location: SARIFLocation
    message: str
    proposed_fix: Path | None = None
    auditor: str
    fingerprint: str
    status: Literal["open", "in_progress", "resolved", "wontfix"] = "open"
