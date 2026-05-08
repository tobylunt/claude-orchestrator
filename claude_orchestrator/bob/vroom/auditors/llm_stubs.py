"""Stub LLM auditor for M3 (Codex pending real implementation in M4 Task 2)."""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.models import Finding


class CodexSecurityAuditorStub:
    id = "codex_security"

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        return []
