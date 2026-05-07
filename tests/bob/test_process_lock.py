"""Tests for .bob/.bob.lock single-instance enforcement."""
import os
from pathlib import Path

import pytest

from claude_orchestrator.bob.process_lock import (
    LockHeld,
    StalePidDetected,
    acquire_lock,
    release_lock,
)


def test_acquire_lock_creates_file(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    assert (bob_dir / ".bob.lock").exists()
    release_lock(lock)


def test_acquire_lock_writes_pid(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    pid = int((bob_dir / ".bob.lock").read_text().strip())
    assert pid == os.getpid()
    release_lock(lock)


def test_release_lock_removes_file(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    release_lock(lock)
    assert not (bob_dir / ".bob.lock").exists()


def test_acquire_lock_blocks_when_held(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    # Simulate a separate process holding the lock by NOT releasing it.
    with pytest.raises(LockHeld):
        acquire_lock(bob_dir)
    release_lock(lock)


def test_acquire_lock_clears_stale_pid(bob_dir: Path):
    """A lock file with a dead PID should be reclaimed automatically."""
    # Write an obviously dead PID (1 is init / launchd; we use 99999999 instead).
    (bob_dir / ".bob.lock").write_text("99999999")

    lock = acquire_lock(bob_dir)
    assert int((bob_dir / ".bob.lock").read_text().strip()) == os.getpid()
    release_lock(lock)


def test_acquire_lock_complains_about_malformed_lock(bob_dir: Path):
    (bob_dir / ".bob.lock").write_text("not-a-pid")
    with pytest.raises(StalePidDetected):
        acquire_lock(bob_dir)


def test_release_lock_is_idempotent(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    release_lock(lock)
    # second release should not raise
    release_lock(lock)
