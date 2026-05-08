"""SIGINT/SIGTERM/SIGHUP handler that flips a shutdown flag.

Mirrors the existing orchestrator.py pattern. The Coordinator should
poll _is_shutdown() between phase transitions in long-running flows.
"""

from __future__ import annotations

import atexit
import logging
import signal
from collections.abc import Callable

log = logging.getLogger(__name__)

_shutdown_requested = False
_cleanup_callbacks: list[Callable[[], None]] = []


def install_handlers() -> None:
    """Install SIGINT/SIGTERM/SIGHUP -> _shutdown_requested = True.

    Idempotent: clears any prior cleanup callbacks (relevant in tests that
    call main() in-process multiple times within a single Python interpreter).
    """
    global _shutdown_requested, _cleanup_callbacks
    _shutdown_requested = False
    _cleanup_callbacks = []
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    atexit.register(_run_cleanups)


def is_shutdown_requested() -> bool:
    return _shutdown_requested


def register_cleanup(fn: Callable[[], None]) -> None:
    _cleanup_callbacks.append(fn)


def _on_signal(signum: int, _frame) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        # Second signal: force exit immediately.
        log.warning("second signal %s received — force exit", signum)
        _run_cleanups()
        raise SystemExit(130)
    _shutdown_requested = True
    log.warning("signal %s received — shutting down gracefully (Ctrl-C again to force)",
                signum)


def _run_cleanups() -> None:
    for fn in reversed(_cleanup_callbacks):
        try:
            fn()
        except Exception:
            log.exception("cleanup callback raised")
