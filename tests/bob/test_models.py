"""Tests for Bob-era phase contracts."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Finding,
    SARIFLocation,
    Spec,
    TaskType,
    Verdict,
    VerificationPlan,
)


def test_task_type_has_custom_for_extensibility():
    assert TaskType.CUSTOM == "custom"
    # Make sure standard members exist too
    for name in ("UI", "DATA_ANALYSIS", "GEOSPATIAL", "LIBRARY", "CLI",
                 "INTEGRATION", "ML_TRAINING", "INFRASTRUCTURE"):
        assert name in TaskType.__members__


def test_verification_plan_requires_verifier_id():
    with pytest.raises(ValidationError):
        VerificationPlan(success_criteria=["x"], required_tools=[])


def test_verification_plan_valid():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["all tests pass"],
        required_tools=["pytest"],
    )
    assert plan.verifier_id == "python_pytest"


def test_feature_status_transitions_are_strings():
    # Used for serialization to state.json
    assert FeatureStatus.PENDING.value == "pending"
    assert FeatureStatus.MERGED.value == "merged"


def test_feature_round_trips_through_json():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["tests pass"],
        required_tools=["pytest"],
    )
    feature = Feature(
        id=1,
        name="auth",
        description="Add login",
        task_type=TaskType.LIBRARY,
        verification_plan=plan,
        branch=None,
        worktree_path=None,
        status=FeatureStatus.PENDING,
        attempts=0,
        cost_usd=0.0,
        last_error=None,
    )
    blob = feature.model_dump_json()
    feature2 = Feature.model_validate_json(blob)
    assert feature2 == feature


def test_spec_holds_features():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["x"],
        required_tools=["pytest"],
    )
    feat = Feature(
        id=1, name="a", description="b", task_type=TaskType.LIBRARY,
        verification_plan=plan, branch=None, worktree_path=None,
        status=FeatureStatus.PENDING, attempts=0, cost_usd=0.0, last_error=None,
    )
    spec = Spec(
        title="Demo",
        motivation="why",
        inputs=[],
        features=[feat],
        rubric_meta_check_passed=True,
    )
    assert len(spec.features) == 1


def test_verdict_decisions_are_constrained():
    v = Verdict(
        feature_id=1, decision="approve", confidence=0.9,
        debate_log_path=Path("/tmp/x"), judge_reasoning="lgtm",
    )
    assert v.decision == "approve"
    with pytest.raises(ValidationError):
        Verdict(
            feature_id=1, decision="maybe", confidence=0.9,
            debate_log_path=Path("/tmp/x"), judge_reasoning="lgtm",
        )


def test_finding_is_sarif_compatible_subset():
    f = Finding(
        rule_id="bob.test",
        severity="medium",
        location=SARIFLocation(uri="src/x.py", start_line=1, end_line=2),
        message="tests too slow",
        proposed_fix=None,
        auditor="claude_architect",
        fingerprint="abc123",
        status="open",
    )
    assert f.severity == "medium"


def test_strenum_str_returns_value():
    """Regression: StrEnum members must str() to their value, not the
    'ClassName.MEMBER' default. This guards JSON serialization on 3.10."""
    assert str(TaskType.LIBRARY) == "library"
    assert str(FeatureStatus.PENDING) == "pending"


def test_feature_model_dump_emits_strings_for_enums():
    """Regression: model_dump() must return strings for enum fields, not enum members.
    Without use_enum_values=True this would return the FeatureStatus member, breaking
    json.dumps() in state_io.py."""
    import json
    plan = VerificationPlan(verifier_id="python_pytest", success_criteria=[], required_tools=[])
    feat = Feature(
        id=1, name="x", description="y", task_type=TaskType.LIBRARY,
        verification_plan=plan, status=FeatureStatus.PENDING,
    )
    dumped = feat.model_dump()
    assert dumped["status"] == "pending"
    assert dumped["task_type"] == "library"
    # And it must json-serialize cleanly:
    assert json.loads(json.dumps(dumped, default=str))["status"] == "pending"
