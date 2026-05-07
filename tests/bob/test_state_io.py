"""Tests for atomic JSON write and append-only JSONL helpers."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.state_io import (
    append_jsonl,
    read_json,
    read_jsonl,
    write_json_atomic,
)


def test_write_json_atomic_creates_file(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"phase": "duplo", "feature_id": None})
    assert json.loads(path.read_text()) == {"phase": "duplo", "feature_id": None}


def test_write_json_atomic_overwrites(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}


def test_write_json_atomic_no_partial_writes(bob_dir: Path, monkeypatch):
    """Simulate a crash mid-write; the original file must remain intact."""
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"original": True})
    original = path.read_text()

    # Simulate a crash during the rename step
    real_replace = Path.replace

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(RuntimeError):
        write_json_atomic(path, {"corrupt": True})

    assert path.read_text() == original


def test_read_json_returns_dict(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"x": 1})
    assert read_json(path) == {"x": 1}


def test_read_json_returns_default_for_missing(bob_dir: Path):
    path = bob_dir / "missing.json"
    assert read_json(path, default={"empty": True}) == {"empty": True}


def test_append_jsonl_creates_file(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"event": "started"})
    lines = path.read_text().splitlines()
    assert lines == ['{"event": "started"}']


def test_append_jsonl_appends(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"event": "a"})
    append_jsonl(path, {"event": "b"})
    lines = path.read_text().splitlines()
    assert json.loads(lines[0]) == {"event": "a"}
    assert json.loads(lines[1]) == {"event": "b"}


def test_read_jsonl_yields_records(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"i": 1})
    append_jsonl(path, {"i": 2})
    assert list(read_jsonl(path)) == [{"i": 1}, {"i": 2}]


def test_read_jsonl_handles_missing_file(bob_dir: Path):
    path = bob_dir / "missing.jsonl"
    assert list(read_jsonl(path)) == []


def test_append_jsonl_rejects_oversized_records(bob_dir: Path):
    """POSIX guarantees atomic appends only for writes < PIPE_BUF (~4KB)."""
    path = bob_dir / "x.jsonl"
    too_big = {"data": "x" * 5000}
    with pytest.raises(ValueError, match="too large"):
        append_jsonl(path, too_big)
