"""Tests for Bob's OpenAI model configuration helpers."""

from __future__ import annotations

import pytest

from claude_orchestrator.bob.openai_config import (
    openai_reasoning_kwargs,
    resolve_openai_reasoning_effort,
)


def test_resolve_openai_reasoning_effort_uses_default():
    assert (
        resolve_openai_reasoning_effort(
            env_var="BOB_ORCHESTRA_CODEX_EFFORT",
            default="medium",
            env={},
        )
        == "medium"
    )


def test_resolve_openai_reasoning_effort_allows_provider_default():
    assert (
        resolve_openai_reasoning_effort(
            env_var="BOB_ORCHESTRA_CODEX_EFFORT",
            default="medium",
            env={"BOB_ORCHESTRA_CODEX_EFFORT": "default"},
        )
        is None
    )


def test_resolve_openai_reasoning_effort_normalizes_and_validates():
    assert (
        resolve_openai_reasoning_effort(
            env_var="BOB_ORCHESTRA_CODEX_EFFORT",
            default="medium",
            env={"BOB_ORCHESTRA_CODEX_EFFORT": " XHIGH "},
        )
        == "xhigh"
    )
    with pytest.raises(ValueError, match="BOB_ORCHESTRA_CODEX_EFFORT"):
        resolve_openai_reasoning_effort(
            env_var="BOB_ORCHESTRA_CODEX_EFFORT",
            default="medium",
            env={"BOB_ORCHESTRA_CODEX_EFFORT": "maximum"},
        )


def test_openai_reasoning_kwargs_only_for_gpt5_family():
    assert openai_reasoning_kwargs(model="gpt-5.5", reasoning_effort="xhigh") == {
        "reasoning_effort": "xhigh",
    }
    assert openai_reasoning_kwargs(model="gpt-4o", reasoning_effort="xhigh") == {}
    assert openai_reasoning_kwargs(model="gpt-5.4", reasoning_effort=None) == {}
