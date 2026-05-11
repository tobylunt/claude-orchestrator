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


# Host env vars forwarded into the container when the caller passes env=None.
# Without this whitelist, DockerExecutor.run(env=None) emits zero -e flags,
# the inner claude/codex subprocess can't authenticate, and McLoop burns all
# its iterations with no output. Callers can override by passing env=dict.
_DEFAULT_FORWARD_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "HOME",
    "PATH",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
)


def _build_forwarded_env() -> dict[str, str]:
    """Read whitelisted host env vars + any BOB_DOCKER_FORWARD_ENV additions."""
    keys = list(_DEFAULT_FORWARD_ENV)
    extra = os.environ.get("BOB_DOCKER_FORWARD_ENV", "")
    if extra:
        keys.extend(k.strip() for k in extra.split(",") if k.strip())
    # Also forward anything starting with BOB_ (config), ANTHROPIC_, OPENAI_.
    prefix_match = [k for k in os.environ if k.startswith(("BOB_", "ANTHROPIC_", "OPENAI_"))]
    keys.extend(prefix_match)
    return {k: os.environ[k] for k in set(keys) if k in os.environ}


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
        add_hosts: dict[str, str] | None = None,
        user: str | None = None,
    ) -> None:
        self.image = image
        self.cpus = cpus
        self.memory = memory
        self.extra_args = list(extra_args or [])
        self.network = network  # None = default (host-bridge); "none" = no network; "<name>" = custom
        self.dockerfile = dockerfile
        self.add_hosts = add_hosts or {}
        # Match host UID:GID by default so files written inside the container
        # are owned by the host user — fixes "non-root user can't write to
        # mounted /workspace" when the example dockerfile uses USER bob.
        self.user = user if user is not None else f"{os.getuid()}:{os.getgid()}"
        self._built_image: str | None = None
        # Extra host paths to mount (host_path → container_path); set via
        # add_volume() and consumed by run().
        self._extra_volumes: dict[Path, str] = {}

    def add_volume(self, host_path: Path, container_path: str) -> None:
        """Register an additional bind-mount for subsequent run() calls.

        Idempotent: re-registering the same host_path overwrites the previous
        container_path mapping.
        """
        self._extra_volumes[Path(host_path).resolve()] = container_path

    def translate_path(self, host_path: Path) -> str:
        """Translate a host path to its in-container equivalent if mounted.

        Returns the host path string if no matching mount is registered, so
        callers can use the same code regardless of executor tier.
        """
        host_path = Path(host_path).resolve()
        for host_root, container_root in self._extra_volumes.items():
            try:
                rel = host_path.relative_to(host_root)
            except ValueError:
                continue
            return f"{container_root}/{rel}" if str(rel) != "." else container_root
        return str(host_path)

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
            "--user", self.user,
        ]

        # Extra bind-mounts registered via add_volume() (e.g., .bob/ for state)
        for host_root, container_root in self._extra_volumes.items():
            docker_args.extend(["--volume", f"{host_root}:{container_root}"])

        # Custom network
        if self.network is not None:
            docker_args.extend(["--network", self.network])

        # Custom add-hosts for users who want to pin DNS resolution.
        # NOTE: --add-host alone does NOT filter egress — it pins or overrides
        # DNS. Setting 0.0.0.0 makes a hostname unreachable; setting an IP
        # forces a specific destination. For real egress filtering, use a
        # custom Docker network with an explicit proxy (BOB_DOCKER_NETWORK).
        for host, ip in self.add_hosts.items():
            docker_args.extend(["--add-host", f"{host}:{ip}"])

        # Env vars: explicit dict if provided, otherwise forward a whitelist
        # from the host. Without this, env=None means "no env in container"
        # and the inner subprocess can't authenticate to Anthropic/OpenAI.
        runtime_env = env if env is not None else _build_forwarded_env()
        for key, value in runtime_env.items():
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
            # Surface the build failure loudly. Silently falling back to the
            # default image strips out everything the Dockerfile installed
            # (Node, Claude CLI, project deps), so the inner claude -p call
            # would fail with "command not found" and McLoop would burn all
            # its iterations with no progress.
            stderr_tail = (result.stderr or "")[-1500:]
            raise RuntimeError(
                f"docker build failed (exit {result.returncode}); aborting before "
                f"running in a stripped-down fallback image.\nstderr tail:\n{stderr_tail}"
            )
        self._built_image = tag
        return self._built_image
