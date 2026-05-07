"""Run the project's pytest suite as a verification step.

Status mapping:
  pytest exit 0      -> ok
  pytest exit 1      -> fail (test failures)
  pytest exit 5      -> inconclusive (no tests collected)
  anything else      -> inconclusive (collection error / config error / etc.)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    Verifier,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class PythonPytestVerifier:
    id = "python_pytest"

    def applies_to(self) -> list[TaskType]:
        return [
            TaskType.LIBRARY,
            TaskType.CLI,
            TaskType.INTEGRATION,
            TaskType.DATA_ANALYSIS,
            TaskType.GEOSPATIAL,
            TaskType.ML_TRAINING,
        ]

    def required_tools(self) -> list[str]:
        return ["pytest"]

    def preflight(self, workspace: Path) -> PreflightResult:
        if shutil.which("pytest") is None:
            return PreflightResult(ok=False, missing_tools=["pytest"])
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        result = subprocess.run(
            ["pytest", "-q", "--tb=short", "--no-header"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
        rc = result.returncode
        output = (result.stdout + result.stderr).strip()

        if rc == 0:
            return VerifyResult(
                status="ok",
                reason="all tests passed",
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 1:
            return VerifyResult(
                status="fail",
                reason=output[-2000:],  # tail to keep the agent's context tight
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 5:
            return VerifyResult(
                status="inconclusive",
                reason="no tests collected — verifier cannot judge",
                artifacts=[],
                coverage_notes="ensure tests/ contains at least one test_*.py file",
            )
        return VerifyResult(
            status="inconclusive",
            reason=f"pytest exited {rc} — {output[-1500:]}",
            artifacts=[],
            coverage_notes="non-standard pytest exit; investigate before proceeding",
        )
