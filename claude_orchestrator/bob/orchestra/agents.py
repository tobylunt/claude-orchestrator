"""Production debate agents — thin wrappers around Anthropic/OpenAI APIs.

These satisfy the DebateAgent protocol from real.py. We keep them minimal
to avoid pulling all of AutoGen's dependency surface; M3 can swap to
AutoGen's ConversableAgent if needed.
"""

from __future__ import annotations

import json
import os
from typing import Any


class AnthropicDebateAgent:
    """A debate agent that calls Anthropic's API."""

    def __init__(self, *, model: str, system: str, role: str) -> None:
        self.model = model
        self.system = system
        self.role = role

    def run(self, prompt: str) -> list[dict[str, Any]]:
        from anthropic import Anthropic
        from claude_orchestrator.bob.cost_tracker import record_call_in_context

        client = Anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_call_in_context(
                provider="anthropic",
                model=self.model,
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
                phase="orchestra",
            )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return [self._parse_response(text)]

    def _parse_response(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text, "decision": "abstain", "confidence": 0.5}


class OpenAIDebateAgent:
    """A debate agent that calls OpenAI's API."""

    def __init__(self, *, model: str, system: str, role: str) -> None:
        self.model = model
        self.system = system
        self.role = role

    def run(self, prompt: str) -> list[dict[str, Any]]:
        try:
            from openai import OpenAI
        except ImportError:
            return [{"content": "openai SDK not installed", "decision": "abstain", "confidence": 0.0}]
        from claude_orchestrator.bob.cost_tracker import record_call_in_context

        client = OpenAI()
        response = client.chat.completions.create(
            model=self.model,
            # GPT-5+ requires max_completion_tokens; older models also accept it.
            max_completion_tokens=2000,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "user", "content": prompt},
            ],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            record_call_in_context(
                provider="openai",
                model=self.model,
                tokens_in=getattr(usage, "prompt_tokens", 0),
                tokens_out=getattr(usage, "completion_tokens", 0),
                phase="orchestra",
            )

        text = response.choices[0].message.content or ""
        return [self._parse_response(text)]

    def _parse_response(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text, "decision": "abstain", "confidence": 0.5}
