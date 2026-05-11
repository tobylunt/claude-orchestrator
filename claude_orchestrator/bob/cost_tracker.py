"""Cost tracking for Bob's API calls.

Each LLM API call (Anthropic, OpenAI) is logged to <project>/.bob/costs.jsonl
with a per-call record: provider, model, tokens in/out, estimated cost in USD,
phase (orchestra/duplo/vroom), run_id, timestamp.

Pricing is a static table covering current models. Unknown models record
tokens but cost_usd=None (we never silently lie about costs).

Aggregation produces totals by run, by provider, by phase, etc.
"""

from __future__ import annotations

import contextvars
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_orchestrator.bob.state_io import append_jsonl


# Prices in USD per 1M tokens. Source: provider pricing pages, current as of
# 2026-05. Keys are (provider, model). Anthropic charges per million tokens
# input + output separately; OpenAI similar.
#
# Format: model_id -> (input_usd_per_mtok, output_usd_per_mtok)
_ANTHROPIC_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.00, 75.00),
    "claude-opus-4-6": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-haiku-4-5": (0.25, 1.25),
    "claude-haiku-4-5-20251001": (0.25, 1.25),
}

_OPENAI_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.4": (3.00, 15.00),
    "gpt-5.2": (2.50, 10.00),
    "gpt-5": (2.00, 8.00),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost(
    *,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
) -> float | None:
    """Return USD cost or None if model isn't in the pricing table."""
    if provider == "anthropic":
        prices = _ANTHROPIC_PRICES.get(model)
    elif provider == "openai":
        prices = _OPENAI_PRICES.get(model)
    else:
        return None

    if prices is None:
        return None
    in_per_mtok, out_per_mtok = prices
    return (tokens_in / 1_000_000) * in_per_mtok + (tokens_out / 1_000_000) * out_per_mtok


def record_call(
    *,
    bob_dir: Path,
    run_id: str,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    phase: str,
    feature_id: int | None = None,
) -> None:
    """Append a per-call cost record to <bob_dir>/costs.jsonl."""
    cost = estimate_cost(
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "provider": provider,
        "model": model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost,
        "phase": phase,
        "feature_id": feature_id,
    }
    append_jsonl(bob_dir / "costs.jsonl", record)


_current_run_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "bob_run_id", default="(no_run)"
)
_current_bob_dir: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "bob_dir", default=None
)
_current_feature_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "bob_feature_id", default=None
)


def set_run_context(*, run_id: str, bob_dir: Path) -> None:
    """Set the run context for cost recording. Called by Coordinator at run start."""
    _current_run_id.set(run_id)
    _current_bob_dir.set(bob_dir)


def set_feature_context(feature_id: int | None) -> None:
    """Set the ambient feature_id for cost rows produced inside a feature's phases.

    Without this, the orchestra/mcloop call-sites (which don't know which feature
    they belong to) record `feature_id=None`, defeating per-feature cost rollup.
    Pass None to clear (e.g., between features or after run completion).
    """
    _current_feature_id.set(feature_id)


def record_call_in_context(
    *,
    provider: str,
    model: str,
    tokens_in: int,
    tokens_out: int,
    phase: str,
    feature_id: int | None = None,
) -> None:
    """Record a call using the active run context. No-op if context isn't set."""
    bob_dir = _current_bob_dir.get()
    if bob_dir is None:
        return
    if feature_id is None:
        feature_id = _current_feature_id.get()
    record_call(
        bob_dir=bob_dir,
        run_id=_current_run_id.get(),
        provider=provider,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        phase=phase,
        feature_id=feature_id,
    )


def aggregate_costs(
    bob_dir: Path,
    *,
    group_by: str | None = None,
) -> dict[str, Any]:
    """Read costs.jsonl and return aggregate totals.

    If group_by is set (e.g., 'run_id', 'provider', 'phase', 'model'),
    additionally returns a 'groups' dict mapping group_value -> aggregate.
    """
    costs_path = bob_dir / "costs.jsonl"
    if not costs_path.exists():
        return _empty_agg()

    records: list[dict[str, Any]] = []
    for line in costs_path.read_text().splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    return _aggregate(records, group_by=group_by)


def _empty_agg() -> dict[str, Any]:
    return {
        "total_calls": 0,
        "total_tokens_in": 0,
        "total_tokens_out": 0,
        "total_cost_usd": 0,
        "groups": {},
    }


def _aggregate(records: list[dict], *, group_by: str | None) -> dict[str, Any]:
    if not records:
        return _empty_agg()

    total = {
        "total_calls": len(records),
        "total_tokens_in": sum(r.get("tokens_in", 0) for r in records),
        "total_tokens_out": sum(r.get("tokens_out", 0) for r in records),
        "total_cost_usd": sum(
            r["cost_usd"] for r in records if r.get("cost_usd") is not None
        ),
    }

    groups: dict[str, Any] = {}
    if group_by is not None:
        buckets: dict[str, list[dict]] = {}
        for r in records:
            key = str(r.get(group_by, "(none)"))
            buckets.setdefault(key, []).append(r)
        for key, recs in buckets.items():
            groups[key] = {
                "total_calls": len(recs),
                "total_tokens_in": sum(r.get("tokens_in", 0) for r in recs),
                "total_tokens_out": sum(r.get("tokens_out", 0) for r in recs),
                "total_cost_usd": sum(
                    r["cost_usd"] for r in recs if r.get("cost_usd") is not None
                ),
            }

    total["groups"] = groups
    return total
