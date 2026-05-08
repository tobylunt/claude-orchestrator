"""Tier 3 executor: VS Code devcontainer.json-based sandbox.

Per spec §6.10. Uses the @devcontainers/cli npm package's `devcontainer`
binary. Workflow:
  1. `devcontainer up --workspace-folder <path>` — start (or reuse) the container
  2. `devcontainer exec --workspace-folder <path> -- <cmd>` — run the command

The container is reused across calls to amortize startup cost. If
`devcontainer.json` is missing, raises FileNotFoundError with a hint to
generate one.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class DevcontainerExecutor:
    def __init__(
        self,
        *,
        devcontainer_dir: Path,
        cli_cmd: str = "devcontainer",
    ) -> None:
        self.devcontainer_dir = devcontainer_dir
        self.cli_cmd = cli_cmd
        self._up_done = False

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        # Verify devcontainer.json exists.
        devc_json = self.devcontainer_dir / ".devcontainer" / "devcontainer.json"
        if not devc_json.exists():
            # Also check for top-level devcontainer.json (older convention).
            top_level = self.devcontainer_dir / "devcontainer.json"
            if not top_level.exists():
                raise FileNotFoundError(
                    f"no devcontainer.json found at {devc_json} or {top_level}; "
                    f"create one or use --sandbox docker instead."
                )

        # Ensure the container is up (idempotent — devcontainer up is safe to re-run).
        if not self._up_done:
            up_args = [
                self.cli_cmd, "up",
                "--workspace-folder", str(self.devcontainer_dir),
            ]
            subprocess.run(
                up_args,
                capture_output=True, text=True, timeout=timeout,
            )
            self._up_done = True

        # Now exec the command.
        exec_args = [
            self.cli_cmd, "exec",
            "--workspace-folder", str(self.devcontainer_dir),
        ]
        for key, value in (env or {}).items():
            exec_args.extend(["--remote-env", f"{key}={value}"])
        # The devcontainer exec syntax: ... <command...>  (no -- separator needed).
        exec_args.extend(cmd)

        return subprocess.run(
            exec_args,
            capture_output=True, text=True, timeout=timeout,
        )
