"""Stub LLM auditors for M3.

Real Anthropic/OpenAI-backed auditors land in M4 alongside the real
verifier roster. For M3, these stubs satisfy the Auditor protocol so
the pool can be assembled and exercised, but produce no findings.
"""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.models import Finding


class ClaudeArchitectAuditorStub:
    id = "claude_architect"

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        return []  # M4: real Claude call


class CodexSecurityAuditorStub:
    id = "codex_security"

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        return []  # M4: real Codex call
