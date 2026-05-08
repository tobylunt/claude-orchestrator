"""Tests for the geospatial verifier."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.geospatial import GeospatialVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.GEOSPATIAL,
        verification_plan=VerificationPlan(
            verifier_id="geospatial",
            success_criteria=["valid geometries"],
            required_tools=["shapely"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_inconclusive_when_no_spatial_files(tmp_path: Path):
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_ok_on_valid_geojson(tmp_path: Path):
    valid = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {},
            }
        ],
    }
    (tmp_path / "data.geojson").write_text(json.dumps(valid))
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "ok"


def test_fail_on_invalid_polygon(tmp_path: Path):
    invalid = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                # Self-intersecting polygon (bowtie)
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0,0], [1,1], [1,0], [0,1], [0,0]]],
                },
                "properties": {},
            }
        ],
    }
    (tmp_path / "bad.geojson").write_text(json.dumps(invalid))
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "fail"
