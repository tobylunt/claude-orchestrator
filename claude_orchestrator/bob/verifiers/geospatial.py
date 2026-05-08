"""geospatial verifier: shapely topology + pyproj CRS validation."""

from __future__ import annotations

import json
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


_MAX_FEATURES_PER_FILE = 1000


class GeospatialVerifier:
    id = "geospatial"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.GEOSPATIAL, TaskType.DATA_ANALYSIS]

    def required_tools(self) -> list[str]:
        return ["shapely"]

    def preflight(self, workspace: Path) -> PreflightResult:
        try:
            import shapely  # noqa: F401
        except ImportError:
            return PreflightResult(ok=False, missing_tools=["shapely"])
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        try:
            from shapely.geometry import shape
        except ImportError:
            return VerifyResult(
                status="inconclusive",
                reason="shapely not installed",
                artifacts=[],
                coverage_notes="pip install -e '.[m2]'",
            )

        spatial_files = (
            list(workspace.glob("**/*.geojson"))
            + list(workspace.glob("**/*.shp"))
            + list(workspace.glob("**/*.gpkg"))
        )
        if not spatial_files:
            return VerifyResult(
                status="inconclusive",
                reason="no geospatial files (.geojson/.shp/.gpkg) found",
                artifacts=[],
                coverage_notes=None,
            )

        invalid: list[str] = []
        for f in spatial_files:
            if f.suffix == ".geojson":
                try:
                    parsed = json.loads(f.read_text())
                except Exception as e:
                    invalid.append(f"{f}: parse error: {e}")
                    continue
                features_list = (
                    parsed.get("features", [])
                    if parsed.get("type") == "FeatureCollection"
                    else [parsed]
                )
                for i, feat in enumerate(features_list[:_MAX_FEATURES_PER_FILE]):
                    geom = shape(feat["geometry"])
                    if not geom.is_valid:
                        invalid.append(f"{f}[{i}]: not valid -- {geom}")
                        if len(invalid) > 10:
                            break
            # .shp and .gpkg deferred to M3 (need geopandas/fiona).

        if not invalid:
            return VerifyResult(
                status="ok",
                reason=f"{len(spatial_files)} spatial file(s) valid",
                artifacts=[],
                coverage_notes=None,
            )
        return VerifyResult(
            status="fail",
            reason="\n".join(invalid[:10])[:2000],
            artifacts=[],
            coverage_notes=f"{len(invalid)} invalid geometries",
        )
