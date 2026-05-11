"""Production Judge for MetaRubricChecker — Anthropic-backed coverage judge.

The meta-rubric checker asks an LLM "does the assigned verifier actually
cover this feature's success criteria?" Production answers with Claude;
tests inject a stub.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any


class AnthropicJudge:
    """Calls Anthropic Messages API and returns a JSON verdict dict."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get(
            "BOB_RUBRIC_JUDGE_MODEL", "claude-sonnet-4-6"
        )

    def judge(self, prompt_payload: dict) -> dict:
        from anthropic import Anthropic
        client = Anthropic()

        user_text = (
            "You are evaluating verification rubric coverage for a Bob feature.\n\n"
            f"Feature name: {prompt_payload.get('feature_name', '')}\n"
            f"Description: {prompt_payload.get('feature_description', '')}\n"
            f"Task type: {prompt_payload.get('task_type', '')}\n"
            f"Verifier: {prompt_payload.get('verifier_id', '')}\n"
            f"Required tools: {prompt_payload.get('required_tools', [])}\n"
            f"Success criteria:\n"
            + "\n".join(f"  - {c}" for c in prompt_payload.get("success_criteria", []))
            + "\n\n"
            + str(prompt_payload.get("instruction", ""))
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=512,
            messages=[{"role": "user", "content": user_text}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            from claude_orchestrator.bob.cost_tracker import record_call_in_context
            record_call_in_context(
                provider="anthropic",
                model=self.model,
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
                phase="duplo",
            )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        parsed = _parse_judgment_json(text)
        # Stash the raw model output so the wiring layer can persist it to
        # rubric-judgments.jsonl. Without this, an inadequate-without-detail
        # verdict is unactionable post-hoc — we throw away the evidence we'd
        # need to know whether the model misbehaved or actually had a point.
        parsed.setdefault("_raw", text)
        return parsed


class StubJudge:
    """Test/offline judge — always returns 'adequate' for unit tests + stub mode."""

    def judge(self, prompt_payload: dict) -> dict:
        return {
            "verdict": "adequate",
            "missing": [],
            "reasoning": "stub judge — adequate by default",
            "_raw": '{"stub": true}',
        }


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_judgment_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from the model's reply.

    The model may wrap the JSON in markdown fences or add prose. We tolerate
    both by stripping fences and falling back to a regex match on a bare
    object. If parsing fails entirely we mark the verdict as 'inadequate' so
    the rubric gate stays closed rather than silently passing.
    """
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return {
            "verdict": "inadequate",
            "missing": ["judge response was not parseable JSON"],
            "reasoning": f"could not parse: {text[:200]}",
        }
