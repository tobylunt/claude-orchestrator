"""Vroom daemon — long-running audit scheduler.

Cycle triggers (M3):
  - timer_interval_s elapsed since last cycle
  - explicit trigger_now() call (e.g., from `bob vroom now`)

Cycle triggers (M5):
  - file watcher on .git/refs/heads/main (post-merge detection)
"""

from __future__ import annotations

import os
import time
from enum import Enum
from pathlib import Path
from typing import Callable


class VroomCycleTrigger(str, Enum):
    TIMER = "timer"
    MANUAL = "manual"
    FILE_CHANGE = "file_change"


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
        watch_main_ref: bool = False,  # trigger on .git/refs/heads/main mtime change
    ) -> None:
        self.project_root = project_root
        self.audit_cycle = audit_cycle
        self.timer_interval_s = timer_interval_s
        self.bob_dir = project_root / ".bob"
        self._last_run = time.monotonic()
        self._manual_trigger = False
        self.watch_main_ref = watch_main_ref
        self._main_ref_path = project_root / ".git" / "refs" / "heads" / "main"
        self._main_ref_mtime: float | None = self._get_ref_mtime() if watch_main_ref else None

    def _get_ref_mtime(self) -> float | None:
        try:
            return self._main_ref_path.stat().st_mtime
        except (FileNotFoundError, OSError):
            return None

    def trigger_now(self) -> None:
        """Schedule one cycle on the next run_one_iteration call."""
        self._manual_trigger = True

    def run_one_iteration(self) -> VroomCycleTrigger | None:
        """Returns the trigger that fired (TIMER, MANUAL, or FILE_CHANGE), or None."""
        # Manual trigger takes priority.
        if self._manual_trigger:
            self._manual_trigger = False
            self._last_run = time.monotonic()
            self.audit_cycle()
            return VroomCycleTrigger.MANUAL

        # File-change trigger.
        if self.watch_main_ref:
            current_mtime = self._get_ref_mtime()
            if (
                current_mtime is not None
                and self._main_ref_mtime is not None
                and current_mtime > self._main_ref_mtime
            ):
                self._main_ref_mtime = current_mtime
                self._last_run = time.monotonic()
                self.audit_cycle()
                return VroomCycleTrigger.FILE_CHANGE
            # Update the snapshot if it was previously None and now exists.
            if current_mtime is not None and self._main_ref_mtime is None:
                self._main_ref_mtime = current_mtime

        # Timer trigger.
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
