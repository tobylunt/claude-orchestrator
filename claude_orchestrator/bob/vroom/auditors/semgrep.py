"""Semgrep auditor — runs `semgrep --config auto` and parses findings."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.models import Finding, SARIFLocation


class SemgrepAuditor:
    id = "semgrep"

    def triggers_on(self, changed_files: list[Path]) -> bool:
        # Semgrep handles many languages; always run unless the user disables.
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        if shutil.which("semgrep") is None:
            return []  # silently skip if not installed

        try:
            result = subprocess.run(
                ["semgrep", "--config", "auto", "--json", "--quiet", "."],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            return []

        if not result.stdout:
            return []

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        findings: list[Finding] = []
        for r in payload.get("results", []):
            check_id = r.get("check_id", "semgrep.unknown")
            severity_raw = r.get("extra", {}).get("severity", "INFO").upper()
            severity = {
                "ERROR": "high", "WARNING": "medium", "INFO": "info",
                "CRITICAL": "critical",
            }.get(severity_raw, "info")
            path_str = r.get("path", "")
            line = r.get("start", {}).get("line", 0)
            end_line = r.get("end", {}).get("line", line)
            message = r.get("extra", {}).get("message", check_id)

            findings.append(Finding(
                rule_id=check_id,
                severity=severity,  # type: ignore[arg-type]
                location=SARIFLocation(uri=path_str, start_line=line, end_line=end_line),
                message=message[:500],
                proposed_fix=None,
                auditor="semgrep",
                fingerprint=f"semgrep:{check_id}:{path_str}:{line}",
                status="open",
            ))

        return findings
