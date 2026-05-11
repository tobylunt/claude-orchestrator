"""SubprocessExecutor Protocol — abstracts how McLoop's claude subprocess runs.

Tier 1 (HostExecutor): runs on the host directly.
Tier 2 (DockerExecutor): wraps in `docker run ...`.
Tier 3 (DevcontainerExecutor, M4): VS Code Dev Containers.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol


_DEFAULT_FORWARD_ENV = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_SERVICE_NAME",
)


def build_forwarded_env(*, extra_env_var: str | None = None) -> dict[str, str]:
    """Return the safe default env forwarded into non-host sandboxes.

    Host-only values like HOME and PATH are intentionally absent because they
    commonly point at unmapped paths inside containers. Callers can extend the
    whitelist with an executor-specific comma-separated env var.
    """
    keys = list(_DEFAULT_FORWARD_ENV)
    if extra_env_var:
        extra = os.environ.get(extra_env_var, "")
        if extra:
            keys.extend(k.strip() for k in extra.split(",") if k.strip())

    prefix_match = [
        k for k in os.environ
        if k.startswith(("BOB_", "ANTHROPIC_", "OPENAI_"))
    ]
    keys.extend(prefix_match)
    return {k: os.environ[k] for k in set(keys) if k in os.environ}


class SubprocessExecutor(Protocol):
    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]: ...
