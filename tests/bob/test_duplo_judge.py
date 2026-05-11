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


class _FixedJudge:
    """Test judge that returns whatever dict you hand it."""
    def __init__(self, reply: dict): self._reply = reply
    def judge(self, payload): return dict(self._reply)


def test_judgment_malformed_flag_set_when_inadequate_has_no_explanation():
    """The real dogfood produced this exact shape:
    {"verdict": "inadequate"} with no missing or reasoning — yields an
    inadequate verdict the user can't act on. Flag it so the wiring layer
    can print a louder warning + the raw response."""
    j = MetaRubricChecker(_FixedJudge({
        "verdict": "inadequate",
        "_raw": '{"verdict":"inadequate"}',
    })).check(_feature())
    assert j.adequate is False
    assert j.malformed is True
    assert j.raw_response == '{"verdict":"inadequate"}'
    assert "malformed" in str(j).lower()


def test_judgment_malformed_false_when_inadequate_has_reasoning():
    """A normal inadequate verdict with at least some explanation is NOT
    malformed — the user can act on it."""
    j = MetaRubricChecker(_FixedJudge({
        "verdict": "inadequate",
        "missing": ["criterion X"],
        "reasoning": "verifier does not exercise X",
        "_raw": "{...}",
    })).check(_feature())
    assert j.adequate is False
    assert j.malformed is False


def test_judgment_malformed_false_when_adequate():
    """An adequate verdict is never malformed, even with empty reasoning."""
    j = MetaRubricChecker(_FixedJudge({
        "verdict": "adequate",
        "missing": [],
        "reasoning": "",
        "_raw": "{...}",
    })).check(_feature())
    assert j.adequate is True
    assert j.malformed is False


def test_judgment_carries_raw_response_through():
    """Raw response must thread from judge.judge() into CoverageJudgment
    so the wiring layer can persist it to rubric-judgments.jsonl."""
    raw = "the model said something weird here"
    j = MetaRubricChecker(_FixedJudge({
        "verdict": "adequate",
        "missing": [],
        "reasoning": "good",
        "_raw": raw,
    })).check(_feature())
    assert j.raw_response == raw
