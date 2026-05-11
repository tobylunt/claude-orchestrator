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

import json
import subprocess
from pathlib import Path

from claude_orchestrator.bob.sandbox.executor import build_forwarded_env


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

    def _devcontainer_json(self) -> Path:
        devc_json = self.devcontainer_dir / ".devcontainer" / "devcontainer.json"
        if devc_json.exists():
            return devc_json
        top_level = self.devcontainer_dir / "devcontainer.json"
        if top_level.exists():
            return top_level
        raise FileNotFoundError(
            f"no devcontainer.json found at {devc_json} or {top_level}; "
            f"create one or use --sandbox docker instead."
        )

    def _workspace_folder(self) -> str:
        """Best-effort container workspace path for cwd translation."""
        try:
            data = json.loads(self._devcontainer_json().read_text())
        except (OSError, json.JSONDecodeError):
            data = {}

        configured = data.get("workspaceFolder")
        if isinstance(configured, str) and configured.strip():
            return (
                configured
                .replace("${localWorkspaceFolderBasename}", self.devcontainer_dir.name)
                .replace("${localWorkspaceFolder}", str(self.devcontainer_dir))
            )
        return f"/workspaces/{self.devcontainer_dir.name}"

    def run(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str] | None,
        timeout: int,
    ) -> subprocess.CompletedProcess[str]:
        # Verify devcontainer.json exists.
        self._devcontainer_json()

        # Ensure the container is up (idempotent — devcontainer up is safe to re-run).
        if not self._up_done:
            up_args = [
                self.cli_cmd, "up",
                "--workspace-folder", str(self.devcontainer_dir),
            ]
            up_result = subprocess.run(
                up_args,
                capture_output=True, text=True, timeout=timeout,
            )
            if up_result.returncode != 0:
                stderr_tail = (up_result.stderr or up_result.stdout or "")[-1500:]
                raise RuntimeError(
                    f"devcontainer up failed (exit {up_result.returncode}); "
                    f"aborting before running command.\noutput tail:\n{stderr_tail}"
                )
            self._up_done = True

        # Now exec the command.
        exec_args = [
            self.cli_cmd, "exec",
            "--workspace-folder", str(self.devcontainer_dir),
        ]
        runtime_env = (
            dict(env) if env is not None
            else build_forwarded_env(extra_env_var="BOB_DEVCONTAINER_FORWARD_ENV")
        )
        for key, value in runtime_env.items():
            exec_args.extend(["--remote-env", f"{key}={value}"])
        container_cwd = self.translate_path(cwd)
        exec_args.extend([
            "/bin/sh", "-lc",
            'cd "$1" && shift && exec "$@"',
            "sh", container_cwd,
            *cmd,
        ])

        return subprocess.run(
            exec_args,
            capture_output=True, text=True, timeout=timeout,
        )

    def add_volume(self, host_path: Path, container_path: str) -> None:
        """Devcontainers declare mounts in devcontainer.json statically; we
        can't add them at runtime. Treat as a no-op and rely on the user's
        devcontainer.json to mount .bob/ if needed (see docs)."""

    def translate_path(self, host_path: Path) -> str:
        """Translate project-relative host paths to the devcontainer workspace."""
        host_path = Path(host_path).resolve()
        try:
            rel = host_path.relative_to(self.devcontainer_dir.resolve())
        except ValueError:
            return str(host_path)
        workspace = self._workspace_folder().rstrip("/")
        return workspace if str(rel) == "." else f"{workspace}/{rel}"
