"""M1 stub for Duplo: parse a structured markdown spec into Spec/Feature.

The format is intentionally narrow and predictable for M1. M2 replaces this
with a multimodal Anthropic vision call that produces the same Spec shape.
"""

from __future__ import annotations

import re
from pathlib import Path

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
)


class SpecParseError(ValueError):
    """The markdown does not match the M1 expected format."""


_FEATURE_HEADER = re.compile(r"^###\s+F(\d+):\s+(\S.*)$")
_FIELD = re.compile(r"^-\s+(\w+):\s*(.*)$")
_SUB_BULLET = re.compile(r"^\s+-\s+(.+)$")


def parse_markdown_spec(path: Path) -> Spec:
    text = path.read_text()
    lines = text.splitlines()

    title = _extract_h1(lines)
    motivation = _extract_section(lines, "Motivation")
    feature_blocks = _split_feature_blocks(lines)

    if not title:
        raise SpecParseError("spec is missing a title (# heading)")

    features: list[Feature] = [_parse_feature_block(b) for b in feature_blocks]
    return Spec(
        title=title,
        motivation=motivation,
        inputs=[],
        features=features,
        rubric_meta_check_passed=False,
    )


def _extract_h1(lines: list[str]) -> str | None:
    for line in lines:
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return None


def _extract_section(lines: list[str], name: str) -> str:
    """Return text under `## <name>` until the next `##` heading."""
    capturing = False
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## ") and s[3:].strip() == name:
            capturing = True
            continue
        if capturing and s.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(l for l in out if l.strip()).strip()


def _split_feature_blocks(lines: list[str]) -> list[list[str]]:
    """Find each `### F<N>: ...` block and return its lines."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _FEATURE_HEADER.match(line):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            if line.lstrip().startswith("##") and not line.lstrip().startswith("###"):
                blocks.append(current)
                current = None
            else:
                current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _parse_feature_block(block: list[str]) -> Feature:
    header = _FEATURE_HEADER.match(block[0])
    if not header:
        raise SpecParseError(f"bad feature header: {block[0]!r}")
    fid = int(header.group(1))
    name = header.group(2).strip()

    fields: dict[str, str | list[str]] = {}
    current_list_field: str | None = None
    for line in block[1:]:
        m = _FIELD.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value:
                fields[key] = value
                current_list_field = None
            else:
                fields[key] = []
                current_list_field = key
            continue
        sm = _SUB_BULLET.match(line)
        if sm and current_list_field:
            fields[current_list_field].append(sm.group(1).strip())  # type: ignore[union-attr]

    try:
        task_type_str = str(fields["task_type"])
    except KeyError:
        raise SpecParseError(f"feature {fid} missing task_type")
    try:
        task_type = TaskType(task_type_str)
    except ValueError:
        raise SpecParseError(
            f"feature {fid}: unknown task_type {task_type_str!r}; "
            f"valid: {sorted(t.value for t in TaskType)}"
        )

    verifier_id = fields.get("verifier")
    if not verifier_id:
        raise SpecParseError(f"feature {fid} missing verifier")

    success = fields.get("success_criteria", [])
    if isinstance(success, str):
        success = [success]
    description = str(fields.get("description", "")).strip()

    plan = VerificationPlan(
        verifier_id=str(verifier_id),
        success_criteria=success,  # type: ignore[arg-type]
        required_tools=[],
    )
    return Feature(
        id=fid,
        name=name,
        description=description,
        task_type=task_type,
        verification_plan=plan,
        status=FeatureStatus.PENDING,
    )
