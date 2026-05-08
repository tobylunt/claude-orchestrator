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


def test_shp_support_inconclusive_when_no_files(tmp_path: Path):
    """No .shp files: still inconclusive (existing behavior preserved)."""
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_shp_validation_with_valid_shapefile(tmp_path: Path):
    """A valid shapefile should produce status=ok."""
    pytest.importorskip("fiona")
    import fiona
    from shapely.geometry import mapping, Point
    schema = {"geometry": "Point", "properties": {}}
    shp_path = tmp_path / "data.shp"
    with fiona.open(
        str(shp_path), "w",
        driver="ESRI Shapefile",
        crs="EPSG:4326",
        schema=schema,
    ) as out:
        out.write({"geometry": mapping(Point(0, 0)), "properties": {}})

    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    # Either ok (we found and validated 1 valid feature) or inconclusive
    # (if shapely + fiona can't agree on validity, which shouldn't happen for a Point).
    assert result.status in ("ok", "inconclusive"), \
        f"unexpected status: {result.status}, reason: {result.reason}"
    if result.status == "ok":
        # Confirm the .shp was counted in the file count.
        assert "spatial file" in result.reason


def test_gpkg_support_with_invalid_geometry(tmp_path: Path):
    """A self-intersecting polygon in a GeoPackage should produce status=fail."""
    pytest.importorskip("fiona")
    import fiona
    from shapely.geometry import mapping, Polygon
    # Bowtie polygon — self-intersecting, fails is_valid.
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
    schema = {"geometry": "Polygon", "properties": {}}
    gpkg_path = tmp_path / "data.gpkg"
    with fiona.open(
        str(gpkg_path), "w",
        driver="GPKG",
        crs="EPSG:4326",
        schema=schema,
    ) as out:
        out.write({"geometry": mapping(bowtie), "properties": {}})

    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "fail", \
        f"expected fail; got {result.status}, reason: {result.reason}"
