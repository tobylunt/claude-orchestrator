"""Parse a markdown spec into features.json using Claude with structured output."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

PARSE_PROMPT_TEMPLATE = """\
You are a technical project planner. Read the specification document below and \
decompose it into a sequentially ordered list of implementation features.

Each feature should:
1. Be a self-contained unit of work that can be implemented and verified in a single session
2. Have a descriptive name (under 80 chars)
3. Include 4-8 concrete implementation steps, each a verifiable acceptance criterion
4. Be ordered by dependency (later features may depend on earlier ones)
5. Be granular enough that one feature = one focused coding session

The specification:

---
{spec_content}
---

Output the features as structured JSON. Each feature gets an incrementing id starting at 1, \
with passes set to false.
"""


class ParsedFeature(BaseModel):
    id: int
    name: str
    passes: bool = False
    steps: list[str]


class ParsedSpec(BaseModel):
    features: list[ParsedFeature]


async def parse_spec(
    spec_path: Path,
    output_path: Path,
    model: str = "opus",
) -> list[ParsedFeature]:
    """Parse a spec.md into features.json using Claude with structured output."""
    spec_content = spec_path.read_text()

    schema = {
        "type": "object",
        "properties": {
            "features": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                        "passes": {"type": "boolean"},
                        "steps": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "name", "passes", "steps"],
                },
            },
        },
        "required": ["features"],
    }

    features: list[ParsedFeature] | None = None

    async for message in query(
        prompt=PARSE_PROMPT_TEMPLATE.format(spec_content=spec_content),
        options=ClaudeAgentOptions(
            model=model,
            output_format={"type": "json_schema", "schema": schema},
            allowed_tools=["Read"],
            cwd=str(spec_path.parent),
        ),
    ):
        if isinstance(message, ResultMessage) and message.structured_output:
            parsed = ParsedSpec.model_validate(message.structured_output)
            features = parsed.features

    if features is None:
        from .errors import SpecParseError
        raise SpecParseError("Spec parsing failed to produce structured output")

    # Write features.json
    data = [f.model_dump() for f in features]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

    return features
