"""Tier 1 executor: runs subprocess on the host directly. No isolation beyond
the existing hooks.py policy layer + git worktree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class HostExecutor:
    """Default executor — runs on the host."""

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            cmd,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
