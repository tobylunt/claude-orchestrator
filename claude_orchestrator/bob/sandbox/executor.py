"""SubprocessExecutor Protocol — abstracts how McLoop's claude subprocess runs.

Tier 1 (HostExecutor): runs on the host directly.
Tier 2 (DockerExecutor): wraps in `docker run ...`.
Tier 3 (DevcontainerExecutor, M4): VS Code Dev Containers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol


class SubprocessExecutor(Protocol):
    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]: ...
