"""Data models for the orchestrator."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class FeatureStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Feature(BaseModel):
    """A single feature to implement. Backward-compatible with {id, name, passes, steps} format."""

    id: int
    name: str
    passes: bool = False
    steps: list[str] = Field(default_factory=list)
    status: FeatureStatus = FeatureStatus.PENDING
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
    status: FeatureStatus
    summary: str
    commit_hash: str | None = None
    session_id: str | None = None
    error: str | None = None
