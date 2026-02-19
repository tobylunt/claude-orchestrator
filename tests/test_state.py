"""Tests for state management."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from claude_orchestrator.models import FeatureResult, FeatureStatus, ProgressEntry
from claude_orchestrator.state import StateManager


class TestLoadFeatures:
    def test_loads_legacy_format(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        features = state.load_features()

        assert len(features) == 3
        assert features[0].id == 1
        assert features[0].name == "Add header component"
        assert features[0].passes is True
        assert features[0].status == FeatureStatus.PASSED
        assert features[1].passes is False
        assert features[1].status == FeatureStatus.PENDING

    def test_loads_steps(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        features = state.load_features()

        assert features[0].steps == ["Create header", "Style it"]
        assert len(features[2].steps) == 3


class TestGetNextFeature:
    def test_returns_first_incomplete(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()

        nxt = state.get_next_feature()
        assert nxt is not None
        assert nxt.id == 2

    def test_respects_start_from(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()

        nxt = state.get_next_feature(start_from=3)
        assert nxt is not None
        assert nxt.id == 3

    def test_returns_none_when_all_complete(self, tmp_path: Path):
        features = [
            {"id": 1, "name": "Done", "passes": True, "steps": []},
        ]
        fp = tmp_path / "features.json"
        fp.write_text(json.dumps(features))
        pp = tmp_path / "progress.txt"
        pp.write_text("")

        state = StateManager(fp, pp)
        state.load_features()
        assert state.get_next_feature() is None

    def test_skips_skipped_features(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        features = state.load_features()

        # Mark feature 2 as skipped
        features[1].status = FeatureStatus.SKIPPED
        state.save_features()
        state.load_features()

        nxt = state.get_next_feature()
        assert nxt is not None
        assert nxt.id == 3


class TestMarkFeature:
    def test_marks_success(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()

        result = FeatureResult(
            feature_id=2,
            success=True,
            session_id="sess-123",
            commit_hash="abc123",
            duration_seconds=60.0,
        )
        state.mark_feature(2, result)

        # Reload and verify
        state.load_features()
        f2 = next(f for f in state._features if f.id == 2)
        assert f2.passes is True
        assert f2.status == FeatureStatus.PASSED
        assert f2.attempts == 1
        assert f2.commit_hash == "abc123"
        assert f2.last_session_id == "sess-123"

    def test_marks_failure(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()

        result = FeatureResult(
            feature_id=2,
            success=False,
            error="Build failed",
            duration_seconds=30.0,
        )
        state.mark_feature(2, result)

        state.load_features()
        f2 = next(f for f in state._features if f.id == 2)
        assert f2.passes is False
        assert f2.status == FeatureStatus.FAILED
        assert f2.last_error == "Build failed"
        assert f2.attempts == 1


class TestSaveFeatures:
    def test_preserves_legacy_format(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()
        state.save_features()

        with open(legacy_features_path) as f:
            data = json.load(f)

        # Legacy fields always present
        assert all("id" in item for item in data)
        assert all("name" in item for item in data)
        assert all("passes" in item for item in data)
        assert all("steps" in item for item in data)

        # Extended fields absent when default
        assert "attempts" not in data[0]
        assert "last_error" not in data[0]

    def test_atomic_write(self, legacy_features_path: Path, progress_path: Path):
        """Verify no .tmp file remains after save."""
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()
        state.save_features()

        tmp = legacy_features_path.with_suffix(".json.tmp")
        assert not tmp.exists()


class TestAppendProgress:
    def test_appends_entry(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)

        entry = ProgressEntry(
            timestamp=datetime(2026, 2, 19, 12, 0),
            feature_id=2,
            feature_name="Add footer component",
            status=FeatureStatus.PASSED,
            summary="Completed successfully in 60s",
            commit_hash="abc123",
            session_id="sess-456",
        )
        state.append_progress(entry)

        content = progress_path.read_text()
        assert "Feature #2" in content
        assert "Add footer component" in content
        assert "abc123" in content
        assert "sess-456" in content


class TestGetProgressSummary:
    def test_summary(self, legacy_features_path: Path, progress_path: Path):
        state = StateManager(legacy_features_path, progress_path)
        state.load_features()

        summary = state.get_progress_summary()
        assert "1/3 complete" in summary
