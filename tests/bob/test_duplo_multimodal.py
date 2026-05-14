"""Tests for the M2 multimodal Duplo (Anthropic vision)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.duplo.multimodal import _SPEC_PROMPT
from claude_orchestrator.bob.duplo.real import RealDuplo
from claude_orchestrator.models import InputRef, Spec, Feature, FeatureStatus, TaskType, VerificationPlan


class FakeMultimodal:
    """Fake Anthropic multimodal client. Returns scripted responses."""

    def __init__(self, responses: list[Spec]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def generate_spec(self, inputs: list[InputRef]) -> Spec:
        self.calls += 1
        return self.responses.pop(0)


def _spec(title: str = "T") -> Spec:
    return Spec(
        title=title, motivation="m",
        features=[Feature(
            id=1, name="a", description="d",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["x"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )],
        rubric_meta_check_passed=True,
    )


def test_real_duplo_returns_spec_from_inputs(tmp_path: Path):
    fake = FakeMultimodal([_spec()])
    duplo = RealDuplo(multimodal=fake)
    inputs = [InputRef(kind="text", value="Build a thing.")]
    spec = duplo.elicit(inputs)
    assert spec.title == "T"
    assert fake.calls == 1


def test_real_duplo_collects_files_from_directory(tmp_path: Path):
    """elicit_from_directory walks a dir and builds an InputRef list."""
    (tmp_path / "brief.md").write_text("# Brief\nbuild a thing.")
    (tmp_path / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    fake = FakeMultimodal([_spec()])
    duplo = RealDuplo(multimodal=fake)
    spec = duplo.elicit_from_directory(tmp_path)
    assert fake.calls == 1
    assert spec.title == "T"


def test_duplo_prompt_includes_spec_quality_rules():
    """Duplo should avoid self-contradictory specs before McLoop sees them."""
    prompt = _SPEC_PROMPT.lower()

    assert "internal consistency" in prompt
    assert "success_criteria" in prompt
    assert "deterministic" in prompt
    assert "byte-identical" in prompt
    assert "append-only" in prompt
    assert "stable sorting" in prompt
    assert "atomic replace" in prompt
    assert "human approval gate" in prompt
    assert "provenance" in prompt
