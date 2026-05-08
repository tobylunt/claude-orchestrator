"""Vroom daemon — long-running audit scheduler.

Cycle triggers (M3):
  - timer_interval_s elapsed since last cycle
  - explicit trigger_now() call (e.g., from `bob vroom now`)

Cycle triggers deferred to M4:
  - file watcher on .git/refs/heads/main
  - signal-based 'now' trigger from a separate process
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class VroomCycleTrigger(str, Enum):
    TIMER = "timer"
    MANUAL = "manual"


class VroomDaemon:
    """Long-running daemon. The caller's main loop typically does:

        daemon.write_pid()
        try:
            while not shutdown_requested:
                daemon.run_one_iteration()
                time.sleep(check_interval)
        finally:
            daemon.remove_pid()

    `audit_cycle` is a callable that runs one full audit (auditor pool +
    coalescer + triage + fix-loop). The daemon doesn't care what it does,
    just when to call it.
    """

    def __init__(
        self,
        *,
        project_root: Path,
        audit_cycle: Callable[[], list],
        timer_interval_s: int = 1800,  # 30 min default
    ) -> None:
        self.project_root = project_root
        self.audit_cycle = audit_cycle
        self.timer_interval_s = timer_interval_s
        self.bob_dir = project_root / ".bob"
        self._last_run = time.monotonic()
        self._manual_trigger = False

    def trigger_now(self) -> None:
        """Schedule one cycle on the next run_one_iteration call."""
        self._manual_trigger = True

    def run_one_iteration(self) -> VroomCycleTrigger | None:
        """Returns the trigger that fired (TIMER or MANUAL), or None if no trigger."""
        if self._manual_trigger:
            self._manual_trigger = False
            self._last_run = time.monotonic()
            self.audit_cycle()
            return VroomCycleTrigger.MANUAL

        elapsed = time.monotonic() - self._last_run
        if elapsed >= self.timer_interval_s:
            self._last_run = time.monotonic()
            self.audit_cycle()
            return VroomCycleTrigger.TIMER

        return None

    def write_pid(self) -> None:
        self.bob_dir.mkdir(parents=True, exist_ok=True)
        pid_path = self.bob_dir / "vroom.pid"
        pid_path.write_text(str(os.getpid()))

    def remove_pid(self) -> None:
        pid_path = self.bob_dir / "vroom.pid"
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
