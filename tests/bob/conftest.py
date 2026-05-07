"""Shared pytest fixtures for bob/ tests."""
from pathlib import Path

import pytest


@pytest.fixture
def bob_dir(tmp_path: Path) -> Path:
    """An empty .bob/ directory rooted at a tmp_path."""
    d = tmp_path / ".bob"
    d.mkdir()
    return d
