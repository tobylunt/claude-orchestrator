"""State management: features.json and progress.txt."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Feature, FeatureResult, FeatureStatus, ProgressEntry


class StateManager:
    """Manages features.json and progress.txt with atomic writes and legacy compatibility."""

    def __init__(self, features_path: Path, progress_path: Path):
        self.features_path = features_path
        self.progress_path = progress_path
        self._features: list[Feature] = []

    def load_features(self) -> list[Feature]:
        """Load features.json, converting legacy format if needed."""
        with open(self.features_path) as f:
            raw = json.load(f)

        features = []
        for item in raw:
            # Map legacy passes: true/false to status enum
            if "status" in item:
                status = FeatureStatus(item["status"])
            elif item.get("passes"):
                status = FeatureStatus.PASSED
            else:
                status = FeatureStatus.PENDING

            features.append(Feature(
                id=item["id"],
                name=item["name"],
                passes=item.get("passes", False),
                steps=item.get("steps", []),
                status=status,
                attempts=item.get("attempts", 0),
                last_error=item.get("last_error"),
                last_session_id=item.get("last_session_id"),
                commit_hash=item.get("commit_hash"),
            ))

        self._features = features
        return features

    def save_features(self) -> None:
        """Atomically write features.json (write to tmp, then rename).

        Preserves legacy-compatible format with optional extended fields.
        """
        tmp_path = self.features_path.with_suffix(".json.tmp")
        data = []
        for f in self._features:
            entry: dict = {
                "id": f.id,
                "name": f.name,
                "passes": f.passes,
                "steps": f.steps,
            }
            # Include extended fields only when non-default
            if f.attempts > 0:
                entry["attempts"] = f.attempts
            if f.last_error:
                entry["last_error"] = f.last_error
            if f.last_session_id:
                entry["last_session_id"] = f.last_session_id
            if f.commit_hash:
                entry["commit_hash"] = f.commit_hash
            if f.status not in (FeatureStatus.PENDING, FeatureStatus.PASSED):
                entry["status"] = f.status.value
            data.append(entry)

        with open(tmp_path, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        tmp_path.rename(self.features_path)

    def get_next_feature(self, start_from: int | None = None) -> Feature | None:
        """Return the first incomplete, non-skipped feature at or after start_from."""
        for f in self._features:
            if start_from is not None and f.id < start_from:
                continue
            if not f.passes and f.status != FeatureStatus.SKIPPED:
                return f
        return None

    def mark_feature(self, feature_id: int, result: FeatureResult) -> None:
        """Update a feature's state based on execution result."""
        for f in self._features:
            if f.id == feature_id:
                f.attempts += 1
                f.last_session_id = result.session_id
                if result.success:
                    f.passes = True
                    f.status = FeatureStatus.PASSED
                    f.commit_hash = result.commit_hash
                    f.last_error = None
                else:
                    f.status = FeatureStatus.FAILED
                    f.last_error = result.error
                break
        self.save_features()

    def append_progress(self, entry: ProgressEntry) -> None:
        """Append a session summary to the progress log."""
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_path, "a") as f:
            header = (
                f"\n=== Feature #{entry.feature_id}: {entry.feature_name} "
                f"-- {entry.status.value} -- "
                f"{entry.timestamp.strftime('%Y-%m-%d %H:%M')} ==="
            )
            f.write(f"{header}\n")
            f.write(f"{entry.summary}\n")
            if entry.commit_hash:
                f.write(f"- Commit: {entry.commit_hash}\n")
            if entry.session_id:
                f.write(f"- Session: {entry.session_id}\n")
            if entry.error:
                f.write(f"- Error: {entry.error}\n")
            f.write("\n")

    def get_progress_summary(self) -> str:
        """Return completion stats for display."""
        total = len(self._features)
        passed = sum(1 for f in self._features if f.passes)
        failed = sum(1 for f in self._features if f.status == FeatureStatus.FAILED)
        skipped = sum(1 for f in self._features if f.status == FeatureStatus.SKIPPED)
        parts = [f"{passed}/{total} complete"]
        if failed:
            parts.append(f"{failed} failed")
        if skipped:
            parts.append(f"{skipped} skipped")
        return "Progress: " + ", ".join(parts)
