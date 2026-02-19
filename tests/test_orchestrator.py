"""Tests for the orchestrator main loop (with mocked runner)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_orchestrator.config import OrchestratorConfig
from claude_orchestrator.models import FeatureResult
from claude_orchestrator.orchestrator import Orchestrator


@pytest.fixture
def config_with_project(tmp_path: Path) -> OrchestratorConfig:
    """Config pointing at a tmp project with 3 features."""
    features = [
        {"id": 1, "name": "Feature A", "passes": True, "steps": ["step1"]},
        {"id": 2, "name": "Feature B", "passes": False, "steps": ["step1", "step2"]},
        {"id": 3, "name": "Feature C", "passes": False, "steps": ["step1"]},
    ]
    (tmp_path / "features.json").write_text(json.dumps(features, indent=2) + "\n")
    (tmp_path / "progress.txt").write_text("")

    return OrchestratorConfig(
        project_dir=tmp_path,
        features_file=Path("features.json"),
        progress_file=Path("progress.txt"),
        max_retries=2,
        structured_log=False,
        dry_run=False,
    )


class TestDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_no_execution(self, config_with_project: OrchestratorConfig, capsys):
        config_with_project.dry_run = True
        orch = Orchestrator(config_with_project)
        await orch.run()

        captured = capsys.readouterr()
        assert "Feature #2" in captured.out
        assert "Feature #3" in captured.out


class TestMainLoop:
    @pytest.mark.asyncio
    async def test_advances_through_features(self, config_with_project: OrchestratorConfig):
        """Mock the runner to return success, verify all features get completed."""
        orch = Orchestrator(config_with_project)

        call_count = 0

        async def mock_run_feature(feature):
            nonlocal call_count
            call_count += 1
            return FeatureResult(
                feature_id=feature.id,
                success=True,
                commit_hash=f"hash{feature.id}",
                duration_seconds=10.0,
            )

        with patch.object(orch.runner, "run_feature", side_effect=mock_run_feature):
            await orch.run()

        assert call_count == 2  # Features 2 and 3

        # Verify features.json was updated
        features = json.loads(
            (config_with_project.project_dir / "features.json").read_text()
        )
        assert all(f["passes"] for f in features)

    @pytest.mark.asyncio
    async def test_stops_on_stop_after(self, config_with_project: OrchestratorConfig):
        config_with_project.stop_after_feature = 2
        orch = Orchestrator(config_with_project)

        call_count = 0

        async def mock_run_feature(feature):
            nonlocal call_count
            call_count += 1
            return FeatureResult(
                feature_id=feature.id,
                success=True,
                duration_seconds=5.0,
            )

        with patch.object(orch.runner, "run_feature", side_effect=mock_run_feature):
            await orch.run()

        assert call_count == 1  # Only feature 2
