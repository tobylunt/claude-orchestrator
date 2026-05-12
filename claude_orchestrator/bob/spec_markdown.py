"""Markdown serialization for Bob specs."""

from __future__ import annotations

from claude_orchestrator.models import Spec


def format_markdown_spec(spec: Spec) -> str:
    """Return parser-readable Markdown for a Bob Spec."""
    lines: list[str] = [
        f"# {spec.title.strip() or 'Untitled Spec'}",
        "",
        "## Motivation",
        spec.motivation.strip(),
        "",
        "## Features",
    ]

    for feature in spec.features:
        lines.extend([
            "",
            f"### F{feature.id}: {feature.name.strip() or 'Untitled feature'}",
            f"- task_type: {feature.task_type}",
            f"- verifier: {feature.verification_plan.verifier_id}",
            "- success_criteria:",
        ])
        for criterion in feature.verification_plan.success_criteria:
            lines.append(f"  - {criterion}")
        lines.append("- description: |")
        lines.extend(_indented_block(feature.description))

    return "\n".join(lines).rstrip() + "\n"


def _indented_block(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return ["    TODO"]
    return [f"    {line}" if line else "" for line in stripped.splitlines()]
