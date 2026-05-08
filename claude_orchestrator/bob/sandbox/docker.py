"""Tier 2 executor: Docker dev container per call.

Each invocation:
- spawns an ephemeral container (--rm)
- mounts cwd at /workspace
- passes env vars via -e
- applies CPU + memory caps
- runs the cmd inside the container

Network allowlist + bob.dockerfile auto-detection are out of scope for
M3's first slice; they ship as configurable extensions.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class DockerExecutor:
    def __init__(
        self,
        *,
        image: str = "python:3.10-slim",
        cpus: float = 4.0,
        memory: str = "8g",
        extra_args: list[str] | None = None,
        network: str | None = None,
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.extra_args = list(extra_args or [])
        self.network = network  # None = default (host-bridge); "none" = no network; "<name>" = custom

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        docker_args: list[str] = [
            "docker", "run", "--rm",
            "--volume", f"{cwd}:/workspace",
            "--workdir", "/workspace",
            f"--cpus={self.cpus}",
            f"--memory={self.memory}",
        ]
        if self.network is not None:
            docker_args.extend(["--network", self.network])
        for key, value in (env or {}).items():
            docker_args.extend(["-e", f"{key}={value}"])
        docker_args.extend(self.extra_args)
        docker_args.append(self.image)
        docker_args.extend(cmd)

        return subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
