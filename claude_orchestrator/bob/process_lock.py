"""Single-instance lock for `bob run` invocations on a shared .bob/ directory.

Mechanism:
- A `.bob/.bob.lock` PID file. If present and the PID is alive, refuse to start.
- If the PID is dead (kill -9 / power loss), reclaim the lock automatically.
- If the file contents are malformed, surface StalePidDetected; user
  intervention required (the file contents may not be ours).
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path


class LockHeld(RuntimeError):
    """Another live process holds the lock."""


class StalePidDetected(RuntimeError):
    """Lock file present but contents are not a valid PID."""


@dataclass
class Lock:
    path: Path
    released: bool = False


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check via signal 0 (no-op signal that fails if pid is gone)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it. Treat as alive.
        return True
    return True


def acquire_lock(bob_dir: Path) -> Lock:
    """Acquire the .bob.lock PID file, raising LockHeld if a live process holds it."""
    bob_dir.mkdir(parents=True, exist_ok=True)
    lock_path = bob_dir / ".bob.lock"

    if lock_path.exists():
        contents = lock_path.read_text().strip()
        try:
            existing_pid = int(contents)
        except ValueError:
            raise StalePidDetected(
                f"{lock_path} contains non-PID contents: {contents!r}. "
                f"Inspect manually before removing."
            )
        if _pid_alive(existing_pid):
            raise LockHeld(
                f"another bob process (pid {existing_pid}) holds {lock_path}"
            )
        # Stale: dead PID — reclaim.
        lock_path.unlink()

    # Create the lock file with O_EXCL to win the race against any other starter.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise LockHeld(f"{lock_path} appeared between check and create")
        raise
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)

    return Lock(path=lock_path)


def release_lock(lock: Lock) -> None:
    """Remove the lock file. Idempotent."""
    if lock.released:
        return
    try:
        lock.path.unlink(missing_ok=True)
    finally:
        lock.released = True
