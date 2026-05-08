"""Tier 2 executor: Docker dev container per call.

Per spec §6.10. Each invocation:
- spawns an ephemeral container (--rm)
- mounts cwd at /workspace
- passes env vars via -e
- applies CPU + memory caps
- optionally builds a custom image from `bob.dockerfile` in the project root
- optionally applies a default network allowlist for known agent hosts
- optionally uses a user-defined Docker network for stricter isolation
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path


# Hostname → IP for the default allowlist. These IPs may shift over time;
# users can override by passing `add_hosts={...}` directly. The point of
# this list is to demonstrate the mechanism in M5; real egress filtering
# requires a user-configured Docker network (use `network` parameter).
#
# We use 0.0.0.0 placeholders to let Docker's default DNS handle resolution.
# The `--add-host` mechanism in Docker is more for forcing specific resolutions
# than for restriction; pair with `--network none` + custom proxy for true
# egress filtering (M6).
_DEFAULT_ALLOWLIST_HOSTS = [
    "api.anthropic.com",
    "api.openai.com",
    "github.com",
    "api.github.com",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
]


class DockerExecutor:
    def __init__(
        self,
        *,
        image: str = "python:3.10-slim",
        cpus: float = 4.0,
        memory: str = "8g",
        extra_args: list[str] | None = None,
        network: str | None = None,
        dockerfile: Path | None = None,
        apply_default_allowlist: bool = False,
        add_hosts: dict[str, str] | None = None,
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.extra_args = list(extra_args or [])
        self.network = network  # None = default (host-bridge); "none" = no network; "<name>" = custom
        self.dockerfile = dockerfile
        self.apply_default_allowlist = apply_default_allowlist
        self.add_hosts = add_hosts or {}
        self._built_image: str | None = None

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        # Resolve image: build from dockerfile if specified, else use self.image.
        image = self._resolve_image(cwd, timeout)

        docker_args: list[str] = [
            "docker", "run", "--rm",
            "--volume", f"{cwd}:/workspace",
            "--workdir", "/workspace",
            f"--cpus={self.cpus}",
            f"--memory={self.memory}",
        ]

        # Custom network
        if self.network is not None:
            docker_args.extend(["--network", self.network])

        # Default allowlist (resolves via Docker's default DNS; for true egress
        # filtering use a custom network).
        if self.apply_default_allowlist:
            for host in _DEFAULT_ALLOWLIST_HOSTS:
                # 0.0.0.0 means "let DNS resolve"; the entry is mostly for documentation.
                docker_args.extend(["--add-host", f"{host}:0.0.0.0"])

        # Custom add-hosts (override / extend the default)
        for host, ip in self.add_hosts.items():
            docker_args.extend(["--add-host", f"{host}:{ip}"])

        # Env vars
        for key, value in (env or {}).items():
            docker_args.extend(["-e", f"{key}={value}"])

        docker_args.extend(self.extra_args)
        docker_args.append(image)
        docker_args.extend(cmd)

        return subprocess.run(
            docker_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def _resolve_image(self, cwd: Path, timeout: int) -> str:
        """If self.dockerfile is set, build it (with caching by dockerfile hash);
        otherwise return self.image directly.
        """
        if self.dockerfile is None:
            return self.image
        if self._built_image is not None:
            return self._built_image

        # Tag the built image deterministically by dockerfile content hash.
        content = self.dockerfile.read_bytes()
        digest = hashlib.sha256(content).hexdigest()[:12]
        tag = f"bob-runtime:{digest}"

        build_args = [
            "docker", "build",
            "--file", str(self.dockerfile),
            "--tag", tag,
            str(self.dockerfile.parent),
        ]
        result = subprocess.run(
            build_args,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            # Build failed; fall back to default image.
            self._built_image = self.image
        else:
            self._built_image = tag
        return self._built_image
