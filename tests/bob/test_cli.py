"""CLI smoke tests for `bob run` and `bob status`.

These tests don't run real Claude — they exercise argument parsing and
verify the run command dispatches with the right config.
"""
import subprocess
import sys
from pathlib import Path


def test_orchestrate_bob_run_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--inputs" in result.stdout
    assert "--max-iterations" in result.stdout


def test_orchestrate_bob_status_on_empty_dir(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no .bob/ found" in result.stdout.lower() or "not initialized" in result.stdout.lower()


def test_orchestrate_bob_status_on_initialized(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    (bob_dir / "cursor.json").write_text(
        '{"run_id": "x", "current_phase": "idle", "current_feature_id": null,'
        ' "last_event_at": "2026-05-07T00:00:00+00:00"}'
    )
    (bob_dir / "features").mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "idle" in result.stdout


def test_orchestrate_bob_run_requires_inputs(tmp_path: Path):
    """`bob run` without --inputs should exit with a clear error."""
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "inputs" in result.stderr.lower() or "inputs" in result.stdout.lower()


def test_orchestrate_bob_run_rejects_missing_spec(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(tmp_path / "does-not-exist.md")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()


def test_orchestrate_bob_run_handles_malformed_spec(tmp_path: Path):
    """Malformed spec produces clean error, not a traceback."""
    spec = tmp_path / "spec.md"
    spec.write_text("## Motivation\nno title\n## Features\n")  # missing # Title

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "title" in out  # the parse error message
    assert "traceback" not in out  # NO traceback


def test_orchestrate_bob_validate_succeeds_on_valid_spec(tmp_path: Path):
    """`bob validate` succeeds on a well-formed spec."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Demo\n## Motivation\nm\n## Features\n"
        "### F1: a\n- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "validate",
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "demo" in result.stdout.lower()
    assert "1 feature" in result.stdout.lower()


def test_orchestrate_bob_run_help_mentions_sandbox():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--sandbox" in result.stdout
    assert "host" in result.stdout
    assert "docker" in result.stdout


def test_orchestrate_bob_validate_fails_on_malformed_spec(tmp_path: Path):
    """`bob validate` reports parse errors with non-zero exit code."""
    spec = tmp_path / "spec.md"
    spec.write_text("## Motivation\nno title\n## Features\n")

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "validate",
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "title" in out
    assert "traceback" not in out
