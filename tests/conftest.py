"""Shared test fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with features.json and progress.txt."""
    features = [
        {"id": 1, "name": "Add header component", "passes": True, "steps": ["Create header", "Style it"]},
        {"id": 2, "name": "Add footer component", "passes": False, "steps": ["Create footer", "Add links"]},
        {"id": 3, "name": "Add navigation", "passes": False, "steps": ["Create nav", "Add routes", "Style"]},
    ]
    features_path = tmp_path / "features.json"
    features_path.write_text(json.dumps(features, indent=2) + "\n")

    progress_path = tmp_path / "progress.txt"
    progress_path.write_text("=== Initial setup ===\nProject initialized.\n")

    return tmp_path


@pytest.fixture
def legacy_features_path(tmp_project: Path) -> Path:
    return tmp_project / "features.json"


@pytest.fixture
def progress_path(tmp_project: Path) -> Path:
    return tmp_project / "progress.txt"
