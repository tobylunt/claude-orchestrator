"""OpenAI model configuration helpers for Bob."""

from __future__ import annotations

import os
from typing import Literal, cast


OpenAIReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]

_VALID_REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh")


def resolve_openai_reasoning_effort(
    *,
    env_var: str,
    default: OpenAIReasoningEffort,
    env: dict[str, str] | None = None,
) -> OpenAIReasoningEffort | None:
    """Resolve an OpenAI reasoning effort setting from env.

    Use "default" or an empty value to omit the API parameter and let the
    provider choose the model default. Otherwise return a validated effort.
    """
    if env is None:
        env = os.environ
    raw = env.get(env_var, default).strip().lower()
    if raw in ("", "default"):
        return None
    if raw not in _VALID_REASONING_EFFORTS:
        raise ValueError(
            f"{env_var} must be one of {_VALID_REASONING_EFFORTS} or 'default'; "
            f"got {raw!r}"
        )
    return cast(OpenAIReasoningEffort, raw)


def openai_reasoning_kwargs(
    *,
    model: str,
    reasoning_effort: OpenAIReasoningEffort | None,
) -> dict[str, str]:
    """Return Chat Completions kwargs for models with reasoning effort support."""
    if reasoning_effort is None:
        return {}
    # Keep explicit older-model overrides working. Bob's defaults are GPT-5
    # family models, whose current docs advertise reasoning.effort support.
    if not model.startswith("gpt-5"):
        return {}
    return {"reasoning_effort": reasoning_effort}
