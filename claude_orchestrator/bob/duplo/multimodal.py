"""Anthropic vision-aware Duplo client."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    InputRef,
    Spec,
    TaskType,
    VerificationPlan,
)


class MultimodalClient(Protocol):
    def generate_spec(self, inputs: list[InputRef]) -> Spec: ...


class AnthropicMultimodalClient:
    """Production multimodal client backed by Anthropic Messages API."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get("BOB_DUPLO_MODEL", "claude-opus-4-7")

    def generate_spec(self, inputs: list[InputRef]) -> Spec:
        from anthropic import Anthropic
        client = Anthropic()

        content_blocks: list[dict] = []
        for ref in inputs:
            if ref.kind == "text":
                content_blocks.append({"type": "text", "text": ref.value})
            elif ref.kind == "url":
                content_blocks.append({"type": "text", "text": f"URL: {ref.value}"})
            elif ref.kind == "file":
                p = Path(ref.value)
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    media_type = f"image/{p.suffix.lstrip('.').lower()}"
                    if media_type == "image/jpg":
                        media_type = "image/jpeg"
                    encoded = base64.b64encode(p.read_bytes()).decode()
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    })
                else:
                    try:
                        content_blocks.append({"type": "text", "text": p.read_text()})
                    except UnicodeDecodeError:
                        content_blocks.append({"type": "text", "text": f"(binary file: {p.name})"})

        content_blocks.append({"type": "text", "text": _SPEC_PROMPT})

        response = client.messages.create(
            model=self.model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content_blocks}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return _parse_spec_json(text)


_SPEC_PROMPT = """\
Based on the inputs above, produce a Bob spec as JSON with this schema:
{
  "title": "...",
  "motivation": "...",
  "features": [
    {
      "id": 1,
      "name": "...",
      "description": "...",
      "task_type": "library|cli|ui|data_analysis|geospatial|integration|ml_training|infrastructure|custom",
      "verification_plan": {
        "verifier_id": "python_pytest|lint_universal|data_analysis|geospatial",
        "success_criteria": ["..."],
        "required_tools": ["..."]
      }
    }
  ]
}

Reply with JSON only, no prose.
"""


def _parse_spec_json(text: str) -> Spec:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    parsed = json.loads(text)
    features = [
        Feature(
            id=f["id"],
            name=f["name"],
            description=f["description"],
            task_type=TaskType(f["task_type"]),
            verification_plan=VerificationPlan(**f["verification_plan"]),
            status=FeatureStatus.PENDING,
        )
        for f in parsed["features"]
    ]
    return Spec(
        title=parsed["title"],
        motivation=parsed["motivation"],
        inputs=[],
        features=features,
        rubric_meta_check_passed=False,
    )
