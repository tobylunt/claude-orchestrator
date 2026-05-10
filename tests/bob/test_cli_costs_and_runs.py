"""Tests for `bob costs` and `bob runs` subcommands."""
import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

from claude_orchestrator.bob.cost_tracker import record_call


def _utc_iso(dt: str) -> str:
    return f"{dt}+00:00"


def test_bob_costs_help():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "costs", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "costs" in result.stdout.lower()
    assert "--by" in result.stdout


def test_bob_runs_help():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "runs", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "runs" in result.stdout.lower()
    assert "--limit" in result.stdout


def test_bob_costs_no_costs_jsonl(tmp_path: Path):
    """No costs.jsonl: should exit 0 with a 'no costs recorded' message."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "costs",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no cost" in result.stdout.lower() or "0 calls" in result.stdout


def test_bob_costs_aggregates_by_run(tmp_path: Path):
    """Populate costs.jsonl with two runs; bob costs --by run should split them."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(bob_dir=bob_dir, run_id="abc12345-aaaa", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=10000, tokens_out=5000,
                phase="orchestra")
    record_call(bob_dir=bob_dir, run_id="def67890-bbbb", provider="openai",
                model="gpt-5.4", tokens_in=20000, tokens_out=10000,
                phase="orchestra")
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "costs",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    # Both run-id prefixes should appear.
    assert "abc12345" in result.stdout
    assert "def67890" in result.stdout
    # Total should be > 0.
    assert "$" in result.stdout


def test_bob_costs_by_provider(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(bob_dir=bob_dir, run_id="r-1", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=10000, tokens_out=5000,
                phase="orchestra")
    record_call(bob_dir=bob_dir, run_id="r-1", provider="openai",
                model="gpt-5.4", tokens_in=20000, tokens_out=10000,
                phase="orchestra")
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "costs",
         "--project", str(tmp_path),
         "--by", "provider"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "anthropic" in result.stdout
    assert "openai" in result.stdout


def test_bob_runs_no_run_log(tmp_path: Path):
    """No run-log.jsonl: should exit 0 with 'no runs' message."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "runs",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no runs" in result.stdout.lower() or "0 runs" in result.stdout.lower()


def test_bob_runs_displays_recent_runs(tmp_path: Path):
    """Populate run-log.jsonl with 2 runs; bob runs should show both."""
    from claude_orchestrator.bob.state_io import append_jsonl
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    log_path = bob_dir / "run-log.jsonl"

    # Run 1
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:14:22+00:00",
        "event": "run_started", "run_id": "fd9b528c-1111",
    })
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:14:25+00:00",
        "event": "feature_started", "feature_id": 1, "name": "x",
        "run_id": "fd9b528c-1111",
    })
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:14:30+00:00",
        "event": "feature_merged", "feature_id": 1,
        "run_id": "fd9b528c-1111",
    })
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:15:01+00:00",
        "event": "run_finished", "run_id": "fd9b528c-1111",
    })

    # Run 2
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:18:11+00:00",
        "event": "run_started", "run_id": "5afbbbd5-2222",
    })
    append_jsonl(log_path, {
        "ts": "2026-05-09T03:18:55+00:00",
        "event": "run_finished", "run_id": "5afbbbd5-2222",
    })

    # Optional: cost data for both runs
    record_call(bob_dir=bob_dir, run_id="fd9b528c-1111", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=10000, tokens_out=5000,
                phase="orchestra")

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "runs",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "fd9b528c" in result.stdout
    assert "5afbbbd5" in result.stdout


def test_bob_runs_respects_limit(tmp_path: Path):
    """--limit 1 should show only the most recent."""
    from claude_orchestrator.bob.state_io import append_jsonl
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    log_path = bob_dir / "run-log.jsonl"

    for i, rid in enumerate(["aaaa1111", "bbbb2222", "cccc3333"]):
        append_jsonl(log_path, {
            "ts": f"2026-05-09T03:0{i}:00+00:00",
            "event": "run_started", "run_id": rid,
        })
        append_jsonl(log_path, {
            "ts": f"2026-05-09T03:0{i}:30+00:00",
            "event": "run_finished", "run_id": rid,
        })

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "runs",
         "--project", str(tmp_path),
         "--limit", "1"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    # Only the latest (cccc3333) should appear; the others should NOT.
    assert "cccc3333" in result.stdout
    assert "aaaa1111" not in result.stdout
