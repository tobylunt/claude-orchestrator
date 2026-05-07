"""Tests for the Verifier protocol and registry."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.bob.verifiers.registry import (
    VerifierRegistry,
    UnknownVerifier,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


class FakeVerifier:
    id = "fake"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.LIBRARY]

    def required_tools(self) -> list[str]:
        return ["python"]

    def preflight(self, workspace: Path) -> PreflightResult:
        return PreflightResult(ok=True, missing_tools=[])

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        return VerifyResult(status="ok", reason="", artifacts=[], coverage_notes=None)


def _make_feature() -> Feature:
    return Feature(
        id=1, name="x", description="y",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="fake",
            success_criteria=["x"],
            required_tools=["python"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_verify_result_status_constrained():
    with pytest.raises(ValueError):
        VerifyResult(status="oops", reason="", artifacts=[], coverage_notes=None)


def test_registry_register_and_lookup():
    reg = VerifierRegistry()
    reg.register(FakeVerifier())
    found = reg.get("fake")
    assert found.id == "fake"


def test_registry_unknown_raises():
    reg = VerifierRegistry()
    with pytest.raises(UnknownVerifier):
        reg.get("nonexistent")


def test_registry_resolve_for_feature_uses_plan_verifier_id(tmp_path: Path):
    reg = VerifierRegistry()
    reg.register(FakeVerifier())
    feature = _make_feature()
    verifier = reg.resolve_for_feature(feature)
    result = verifier.verify(tmp_path, feature)
    assert result.status == "ok"


def test_registry_refuses_feature_with_missing_verifier():
    reg = VerifierRegistry()  # no verifiers registered
    feature = _make_feature()
    with pytest.raises(UnknownVerifier):
        reg.resolve_for_feature(feature)
