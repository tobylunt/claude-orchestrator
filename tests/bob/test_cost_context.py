"""Tests for the run-context cost recording."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.cost_tracker import (
    record_call_in_context,
    set_run_context,
)


def test_record_in_context(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    set_run_context(run_id="r-1", bob_dir=bob_dir)
    record_call_in_context(
        provider="anthropic",
        model="claude-sonnet-4-6",
        tokens_in=100,
        tokens_out=50,
        phase="orchestra",
    )
    rec = json.loads((bob_dir / "costs.jsonl").read_text().strip())
    assert rec["run_id"] == "r-1"
    assert rec["phase"] == "orchestra"


def test_record_outside_context_is_silent(tmp_path: Path):
    """Without set_run_context, recording is a no-op."""
    import contextvars
    from claude_orchestrator.bob.cost_tracker import _current_bob_dir, _current_run_id

    # Run inside a fresh context where the ContextVars hold their defaults.
    ctx = contextvars.copy_context()

    def _reset_and_record():
        _current_bob_dir.set(None)
        _current_run_id.set("(no_run)")
        record_call_in_context(
            provider="anthropic",
            model="claude-sonnet-4-6",
            tokens_in=100,
            tokens_out=50,
            phase="orchestra",
        )

    ctx.run(_reset_and_record)
    # Should not raise; no file written anywhere.
