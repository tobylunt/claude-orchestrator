"""Tests for the M1 markdown spec parser.

Input format (M1 stub — multimodal Duplo lands in M2):

  # Title
  ## Motivation
  text...
  ## Features
  ### F1: auth
  - task_type: library
  - verifier: python_pytest
  - success_criteria:
    - users can log in
  - description: |
      Add a login endpoint.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.duplo.markdown_parser import (
    SpecParseError,
    parse_markdown_spec,
)


def test_parse_minimal_spec(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# Demo project
## Motivation
Make a thing.
## Features
### F1: auth
- task_type: library
- verifier: python_pytest
- success_criteria:
  - users can log in
- description: Add a login endpoint.
""")
    spec = parse_markdown_spec(md)
    assert spec.title == "Demo project"
    assert spec.motivation == "Make a thing."
    assert len(spec.features) == 1
    f = spec.features[0]
    assert f.id == 1
    assert f.name == "auth"
    assert str(f.task_type) == "library"
    assert f.verification_plan.verifier_id == "python_pytest"
    assert f.verification_plan.success_criteria == ["users can log in"]
    assert f.description == "Add a login endpoint."


def test_parse_multiple_features(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# Multi
## Motivation
m
## Features
### F1: a
- task_type: library
- verifier: python_pytest
- success_criteria:
  - x
- description: A
### F2: b
- task_type: cli
- verifier: python_pytest
- success_criteria:
  - y
- description: B
""")
    spec = parse_markdown_spec(md)
    assert [f.name for f in spec.features] == ["a", "b"]
    assert [f.id for f in spec.features] == [1, 2]


def test_parse_rejects_missing_title(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("## Motivation\nm\n## Features\n")
    with pytest.raises(SpecParseError, match="title"):
        parse_markdown_spec(md)


def test_parse_rejects_feature_without_verifier(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# T
## Motivation
m
## Features
### F1: a
- task_type: library
- success_criteria:
  - x
- description: a
""")
    with pytest.raises(SpecParseError, match="verifier"):
        parse_markdown_spec(md)


def test_parse_rejects_unknown_task_type(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# T
## Motivation
m
## Features
### F1: a
- task_type: bogus
- verifier: python_pytest
- success_criteria:
  - x
- description: a
""")
    with pytest.raises(SpecParseError, match="task_type"):
        parse_markdown_spec(md)


def test_parse_multiline_description_with_pipe(tmp_path: Path):
    """`description: |` followed by indented text becomes a multi-line description."""
    md = tmp_path / "spec.md"
    md.write_text("""\
# Demo
## Motivation
m
## Features
### F1: a
- task_type: library
- verifier: python_pytest
- success_criteria:
  - x
- description: |
    Line one of description.
    Line two of description.
""")
    spec = parse_markdown_spec(md)
    desc = spec.features[0].description
    assert "Line one" in desc
    assert "Line two" in desc
    assert desc != "|"


def test_parse_singleline_description_still_works(tmp_path: Path):
    """`description: short text` (no pipe) should still parse as before."""
    md = tmp_path / "spec.md"
    md.write_text("""\
# Demo
## Motivation
m
## Features
### F1: a
- task_type: library
- verifier: python_pytest
- success_criteria:
  - x
- description: A short single-line description.
""")
    spec = parse_markdown_spec(md)
    assert spec.features[0].description == "A short single-line description."


def test_parse_multiline_description_strips_common_indent(tmp_path: Path):
    """Common leading whitespace on continuation lines should be stripped."""
    md = tmp_path / "spec.md"
    md.write_text("""\
# Demo
## Motivation
m
## Features
### F1: a
- task_type: library
- verifier: python_pytest
- success_criteria:
  - x
- description: |
    Hello
    World
""")
    spec = parse_markdown_spec(md)
    desc = spec.features[0].description
    # Should not have the 4-space leading indent on every line.
    lines = desc.split("\n")
    # At least the first non-empty line shouldn't start with 4 spaces of indent
    assert lines[0] == "Hello" or lines[0].lstrip() == "Hello"
    # And the second line should be "World" (de-indented)
    assert "World" in desc
