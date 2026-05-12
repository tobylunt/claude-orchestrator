"""Duplo-only spec drafting helpers."""

from __future__ import annotations

import os
from pathlib import Path

from claude_orchestrator.bob.duplo.markdown_parser import parse_markdown_spec
from claude_orchestrator.models import Spec


def draft_spec_from_inputs(inputs_path: Path) -> Spec:
    """Produce a Spec from a markdown file or multimodal input directory."""
    if inputs_path.is_file():
        return parse_markdown_spec(inputs_path)

    if not inputs_path.is_dir():
        raise FileNotFoundError(f"input path not found: {inputs_path}")

    if os.environ.get("BOB_USE_STUB_DUPLO", "0") == "1":
        md_in_dir = inputs_path / "spec.md"
        if not md_in_dir.exists():
            raise FileNotFoundError(
                f"BOB_USE_STUB_DUPLO=1 but no {md_in_dir} found"
            )
        return parse_markdown_spec(md_in_dir)

    from claude_orchestrator.bob.duplo.multimodal import AnthropicMultimodalClient
    from claude_orchestrator.bob.duplo.real import RealDuplo

    return RealDuplo(multimodal=AnthropicMultimodalClient()).elicit_from_directory(
        inputs_path
    )
