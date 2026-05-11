"""Smoke test for the bob.dockerfile.example template."""
from pathlib import Path

import pytest


def test_dockerfile_template_exists():
    """The repo ships bob.dockerfile.example for users to copy."""
    repo_root = Path(__file__).resolve().parents[2]
    template = repo_root / "bob.dockerfile.example"
    assert template.exists(), f"missing {template}"


def test_dockerfile_template_includes_claude_install():
    """The template installs the claude CLI (otherwise --sandbox docker won't work)."""
    repo_root = Path(__file__).resolve().parents[2]
    template = repo_root / "bob.dockerfile.example"
    content = template.read_text()
    assert "claude-code" in content or "@anthropic-ai/claude" in content


def test_dockerfile_template_includes_python_and_git():
    """The template includes a Python runtime and git."""
    repo_root = Path(__file__).resolve().parents[2]
    template = repo_root / "bob.dockerfile.example"
    content = template.read_text()
    assert "python:" in content.lower() or "FROM python" in content
    assert "git" in content.lower()
