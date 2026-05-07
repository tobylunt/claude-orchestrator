"""Tests for the meta-rubric LLM-as-judge coverage check."""
from claude_orchestrator.bob.duplo.meta_rubric import (
    CoverageJudgment,
    MetaRubricChecker,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="auth", description="login",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["users can log in"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


class FakeJudge:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def judge(self, prompt_payload: dict) -> dict:
        self.calls.append(prompt_payload)
        return self.response


def test_meta_rubric_marks_adequate():
    judge = FakeJudge({"verdict": "adequate", "missing": []})
    checker = MetaRubricChecker(judge=judge)
    judgment = checker.check(_feature())
    assert judgment.adequate is True
    assert judgment.missing == []


def test_meta_rubric_marks_inadequate_with_missing():
    judge = FakeJudge({
        "verdict": "inadequate",
        "missing": ["session timeout enforcement", "password complexity check"],
    })
    checker = MetaRubricChecker(judge=judge)
    judgment = checker.check(_feature())
    assert judgment.adequate is False
    assert judgment.missing == [
        "session timeout enforcement",
        "password complexity check",
    ]


def test_meta_rubric_passes_feature_context_to_judge():
    judge = FakeJudge({"verdict": "adequate", "missing": []})
    checker = MetaRubricChecker(judge=judge)
    checker.check(_feature())
    assert len(judge.calls) == 1
    payload = judge.calls[0]
    assert "users can log in" in str(payload)
    assert "python_pytest" in str(payload)


def test_coverage_judgment_is_str():
    j = CoverageJudgment(adequate=False, missing=["x"], reasoning="r")
    s = str(j)
    assert "inadequate" in s.lower() or "missing" in s.lower()
