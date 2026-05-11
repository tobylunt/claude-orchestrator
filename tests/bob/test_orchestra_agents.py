"""Tests for production debate-agent wrappers."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from claude_orchestrator.bob.orchestra.agents import OpenAIDebateAgent


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"content": "ok", "decision": "approve", "confidence": 0.8}'
                    )
                )
            ],
        )


def test_openai_debate_agent_passes_reasoning_effort(monkeypatch):
    completions = _FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda: fake_client),
    )

    agent = OpenAIDebateAgent(
        model="gpt-5.5",
        system="system",
        role="codex",
        reasoning_effort="xhigh",
    )
    result = agent.run("prompt")

    assert result[0]["decision"] == "approve"
    assert completions.kwargs["reasoning_effort"] == "xhigh"


def test_openai_debate_agent_omits_effort_for_older_model(monkeypatch):
    completions = _FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda: fake_client),
    )

    agent = OpenAIDebateAgent(
        model="gpt-4o",
        system="system",
        role="codex",
        reasoning_effort="xhigh",
    )
    agent.run("prompt")

    assert "reasoning_effort" not in completions.kwargs
