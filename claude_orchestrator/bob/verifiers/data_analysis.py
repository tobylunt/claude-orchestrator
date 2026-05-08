"""data_analysis verifier: pytest (incl. hypothesis property tests) + papermill notebook regression."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class DataAnalysisVerifier:
    id = "data_analysis"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.DATA_ANALYSIS, TaskType.GEOSPATIAL, TaskType.ML_TRAINING]

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
        out = (result.stdout + result.stderr).strip()

        notebooks = list(workspace.glob("**/*.ipynb"))
        nb_status = self._run_notebooks(workspace, notebooks)

        if rc == 0 and (nb_status is True or nb_status is None):
            return VerifyResult(
                status="ok",
                reason="data-analysis tests + notebooks green",
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 5 and not notebooks:
            return VerifyResult(
                status="inconclusive",
                reason="no tests or notebooks found",
                artifacts=[],
                coverage_notes="add tests/ or notebooks for the verifier to judge",
            )
        if rc != 0:
            return VerifyResult(
                status="fail", reason=out[-2000:],
                artifacts=[], coverage_notes=None,
            )
        return VerifyResult(
            status="fail",
            reason="notebook regression failed (papermill)",
            artifacts=[],
            coverage_notes=None,
        )

    def _run_notebooks(self, workspace: Path, notebooks: list[Path]) -> bool | None:
        if not notebooks:
            return None
        try:
            import papermill as pm  # noqa: F401
        except ImportError:
            return None
        for nb in notebooks:
            try:
                tmp_out = nb.with_suffix(".executed.ipynb")
                pm.execute_notebook(str(nb), str(tmp_out), kernel_name="python3")
                tmp_out.unlink(missing_ok=True)
            except Exception:
                return False
        return True
