"""lint_universal: detect-and-run the project's linter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class LintUniversalVerifier:
    id = "lint_universal"

    def applies_to(self) -> list[TaskType]:
        return [
            TaskType.LIBRARY, TaskType.CLI, TaskType.UI,
            TaskType.INTEGRATION, TaskType.DATA_ANALYSIS,
            TaskType.GEOSPATIAL, TaskType.ML_TRAINING,
            TaskType.INFRASTRUCTURE,
        ]

    def required_tools(self) -> list[str]:
        return []

    def preflight(self, workspace: Path) -> PreflightResult:
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        tool, cmd = self._detect(workspace)
        if tool is None:
            return VerifyResult(
                status="inconclusive",
                reason="no lint tool detected (no pyproject.toml [tool.ruff] / .eslintrc / go.mod / Cargo.toml)",
                artifacts=[],
                coverage_notes="add a lint config to enable this verifier",
            )

        if shutil.which(cmd[0]) is None:
            return VerifyResult(
                status="inconclusive",
                reason=f"detected {tool} config but {cmd[0]} not on PATH",
                artifacts=[],
                coverage_notes=f"install {cmd[0]} or remove the config",
            )

        result = subprocess.run(
            cmd, cwd=str(workspace), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return VerifyResult(
                status="ok",
                reason=f"{tool} clean",
                artifacts=[],
                coverage_notes=None,
            )
        out = (result.stdout + result.stderr).strip()
        return VerifyResult(
            status="fail",
            reason=out[-2000:],
            artifacts=[],
            coverage_notes=None,
        )

    def _detect(self, workspace: Path) -> tuple[str | None, list[str]]:
        """Return (tool_name, command) or (None, []) if no tool found."""
        pyproject = workspace / "pyproject.toml"
        if pyproject.exists() and "[tool.ruff" in pyproject.read_text():
            return "ruff", ["ruff", "check", "."]
        if (workspace / ".eslintrc").exists() or (workspace / ".eslintrc.json").exists():
            return "eslint", ["eslint", "."]
        if (workspace / "go.mod").exists():
            return "gofmt", ["gofmt", "-l", "."]
        if (workspace / "Cargo.toml").exists():
            return "clippy", ["cargo", "clippy", "--", "-D", "warnings"]
        return None, []
