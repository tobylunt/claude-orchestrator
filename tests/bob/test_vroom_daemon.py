"""Tests for the Vroom daemon."""
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.vroom.daemon import (
    VroomDaemon,
    VroomCycleTrigger,
)


def test_daemon_runs_one_cycle_when_triggered_now(tmp_path: Path):
    """Triggering 'now' once causes one audit cycle."""
    audit_cycle = MagicMock(return_value=[])
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,  # long; we want only the manual trigger to fire
    )
    daemon.trigger_now()
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 1


def test_daemon_skips_when_no_trigger(tmp_path: Path):
    """If no trigger is active, run_one_iteration should not call audit_cycle."""
    audit_cycle = MagicMock(return_value=[])
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,
    )
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 0


def test_daemon_timer_fires_after_interval(tmp_path: Path, monkeypatch):
    """When the timer interval has elapsed, audit runs."""
    audit_cycle = MagicMock(return_value=[])
    fake_time = [1000.0]

    def fake_monotonic():
        return fake_time[0]

    monkeypatch.setattr(
        "claude_orchestrator.bob.vroom.daemon.time.monotonic",
        fake_monotonic,
    )

    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=60,
    )
    daemon.run_one_iteration()  # first call: not yet 60s in (last_run is now)
    assert audit_cycle.call_count == 0  # we just initialized; haven't waited 60s

    fake_time[0] += 30
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 0  # only 30s elapsed

    fake_time[0] += 31
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 1  # >= 60s elapsed since init


def test_daemon_writes_pid_file_on_start(tmp_path: Path):
    """The daemon writes a PID file on start and removes it on stop."""
    audit_cycle = MagicMock(return_value=[])
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,
    )
    daemon.write_pid()
    assert (bob_dir / "vroom.pid").exists()
    pid = int((bob_dir / "vroom.pid").read_text().strip())
    import os
    assert pid == os.getpid()
    daemon.remove_pid()
    assert not (bob_dir / "vroom.pid").exists()
