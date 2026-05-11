"""Tests for the Duplo meta-rubric Judge implementations."""

from claude_orchestrator.bob.duplo.judge_anthropic import (
    StubJudge,
    _parse_judgment_json,
)
from claude_orchestrator.bob.duplo.meta_rubric import MetaRubricChecker
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["all tests pass"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_stub_judge_always_adequate():
    j = MetaRubricChecker(StubJudge()).check(_feature())
    assert j.adequate is True
    assert j.missing == []


def test_parse_judgment_clean_json():
    out = _parse_judgment_json('{"verdict":"adequate","missing":[],"reasoning":"r"}')
    assert out == {"verdict": "adequate", "missing": [], "reasoning": "r"}


def test_parse_judgment_strips_fence():
    text = '```json\n{"verdict":"inadequate","missing":["x"],"reasoning":"y"}\n```'
    out = _parse_judgment_json(text)
    assert out["verdict"] == "inadequate"
    assert out["missing"] == ["x"]


def test_parse_judgment_unparseable_falls_back_to_inadequate():
    """If the judge response can't be parsed, the rubric gate must stay closed,
    not silently pass through as 'adequate' (halt-loud rule)."""
    out = _parse_judgment_json("sorry, I cannot answer that")
    assert out["verdict"] == "inadequate"
    assert "not parseable" in out["missing"][0]
