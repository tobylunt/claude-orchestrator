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


def test_daemon_fires_on_main_branch_change(tmp_path: Path):
    """When the watched ref file's mtime changes, audit_cycle should fire."""
    import os
    import time as _time

    # Set up a fake ref file (we don't need a real git repo for this test).
    ref_dir = tmp_path / ".git" / "refs" / "heads"
    ref_dir.mkdir(parents=True)
    ref = ref_dir / "main"
    ref.write_text("aaaaaaaa\n")

    audit_cycle = MagicMock(return_value=[])
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,  # long timer so only file change triggers
        watch_main_ref=True,
    )

    # First iteration: snapshots the current mtime; doesn't fire.
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 0

    # Touch the ref file with a newer mtime.
    _time.sleep(0.05)
    ref.write_text("bbbbbbbb\n")

    # Second iteration: detects the change, fires.
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 1


def test_daemon_does_not_fire_when_file_watch_disabled(tmp_path: Path):
    """If watch_main_ref=False, file changes don't trigger anything."""
    ref_dir = tmp_path / ".git" / "refs" / "heads"
    ref_dir.mkdir(parents=True)
    ref = ref_dir / "main"
    ref.write_text("aaaaaaaa\n")

    audit_cycle = MagicMock(return_value=[])
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,
        watch_main_ref=False,  # explicitly disabled
    )
    daemon.run_one_iteration()
    import time as _time
    _time.sleep(0.05)
    ref.write_text("bbbbbbbb\n")
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 0


def test_daemon_handles_missing_ref_file_gracefully(tmp_path: Path):
    """If the ref file doesn't exist, daemon shouldn't crash."""
    audit_cycle = MagicMock(return_value=[])
    daemon = VroomDaemon(
        project_root=tmp_path,
        audit_cycle=audit_cycle,
        timer_interval_s=3600,
        watch_main_ref=True,
    )
    # No .git/refs/heads/main — should not crash.
    daemon.run_one_iteration()
    assert audit_cycle.call_count == 0
