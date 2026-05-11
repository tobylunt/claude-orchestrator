"""Tests for the cost tracker."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.cost_tracker import (
    aggregate_costs,
    estimate_cost,
    record_call,
    record_call_in_context,
    set_feature_context,
    set_run_context,
)


def test_record_call_writes_costs_jsonl(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(
        bob_dir=bob_dir,
        run_id="run-1",
        provider="anthropic",
        model="claude-sonnet-4-6",
        tokens_in=1000,
        tokens_out=500,
        phase="orchestra",
    )

    costs_path = bob_dir / "costs.jsonl"
    assert costs_path.exists()
    rec = json.loads(costs_path.read_text().strip())
    assert rec["provider"] == "anthropic"
    assert rec["model"] == "claude-sonnet-4-6"
    assert rec["tokens_in"] == 1000
    assert rec["tokens_out"] == 500
    assert rec["phase"] == "orchestra"
    assert rec["run_id"] == "run-1"
    assert "ts" in rec
    assert "cost_usd" in rec  # may be None for unknown models, but key exists


def test_estimate_cost_known_model():
    """Sonnet 4.6 has known pricing; cost should be > 0."""
    cost = estimate_cost(
        provider="anthropic",
        model="claude-sonnet-4-6",
        tokens_in=1_000_000,  # 1M tokens
        tokens_out=1_000_000,
    )
    assert cost is not None
    assert cost > 0
    assert cost < 100  # sanity bound


def test_estimate_cost_unknown_model():
    """Unknown models return None (we still record tokens)."""
    cost = estimate_cost(
        provider="anthropic",
        model="claude-future-9000",
        tokens_in=1000,
        tokens_out=500,
    )
    assert cost is None


def test_record_call_with_unknown_model_records_none_cost(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(
        bob_dir=bob_dir,
        run_id="run-1",
        provider="anthropic",
        model="future-model",
        tokens_in=1000,
        tokens_out=500,
        phase="orchestra",
    )
    rec = json.loads((bob_dir / "costs.jsonl").read_text().strip())
    assert rec["cost_usd"] is None


def test_aggregate_costs_sums_total(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    for i in range(3):
        record_call(
            bob_dir=bob_dir,
            run_id="run-1",
            provider="anthropic",
            model="claude-sonnet-4-6",
            tokens_in=10000,
            tokens_out=5000,
            phase="orchestra",
        )
    agg = aggregate_costs(bob_dir)
    assert agg["total_cost_usd"] is not None
    assert agg["total_cost_usd"] > 0
    assert agg["total_calls"] == 3
    assert agg["total_tokens_in"] == 30000
    assert agg["total_tokens_out"] == 15000


def test_aggregate_costs_groups_by_run(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(bob_dir=bob_dir, run_id="run-1", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=1000, tokens_out=500,
                phase="orchestra")
    record_call(bob_dir=bob_dir, run_id="run-2", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=2000, tokens_out=1000,
                phase="orchestra")
    agg = aggregate_costs(bob_dir, group_by="run_id")
    assert "run-1" in agg["groups"]
    assert "run-2" in agg["groups"]
    assert agg["groups"]["run-1"]["total_calls"] == 1
    assert agg["groups"]["run-2"]["total_calls"] == 1


def test_aggregate_costs_groups_by_provider(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    record_call(bob_dir=bob_dir, run_id="run-1", provider="anthropic",
                model="claude-sonnet-4-6", tokens_in=1000, tokens_out=500,
                phase="orchestra")
    record_call(bob_dir=bob_dir, run_id="run-1", provider="openai",
                model="gpt-5.4", tokens_in=2000, tokens_out=1000,
                phase="orchestra")
    agg = aggregate_costs(bob_dir, group_by="provider")
    assert "anthropic" in agg["groups"]
    assert "openai" in agg["groups"]


def test_aggregate_costs_handles_empty_file(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    agg = aggregate_costs(bob_dir)
    assert agg["total_calls"] == 0
    assert agg["total_cost_usd"] == 0


def test_aggregate_costs_handles_missing_file(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    # bob_dir doesn't exist
    agg = aggregate_costs(bob_dir)
    assert agg["total_calls"] == 0


def test_record_call_in_context_inherits_feature_id(tmp_path: Path):
    """set_feature_context should make orchestra/mcloop call-sites attribute
    their costs to the right feature without each call-site passing it."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    set_run_context(run_id="run-1", bob_dir=bob_dir)
    set_feature_context(7)
    try:
        record_call_in_context(
            provider="anthropic", model="claude-sonnet-4-6",
            tokens_in=100, tokens_out=50, phase="orchestra",
        )
    finally:
        set_feature_context(None)

    rows = [json.loads(l) for l in (bob_dir / "costs.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["feature_id"] == 7


def test_record_call_in_context_explicit_feature_id_wins(tmp_path: Path):
    """An explicit feature_id should override the contextvar."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    set_run_context(run_id="run-1", bob_dir=bob_dir)
    set_feature_context(7)
    try:
        record_call_in_context(
            provider="anthropic", model="claude-sonnet-4-6",
            tokens_in=10, tokens_out=10, phase="orchestra",
            feature_id=99,
        )
    finally:
        set_feature_context(None)

    rows = [json.loads(l) for l in (bob_dir / "costs.jsonl").read_text().splitlines()]
    assert rows[0]["feature_id"] == 99
