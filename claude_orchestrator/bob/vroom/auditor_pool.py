"""Parallel auditor pool for Vroom.

Runs N auditors concurrently against a workspace + list of changed files.
Each auditor returns Findings; the pool concatenates them. Coalescing
happens downstream (see coalescer.py).
"""

from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import Finding


class Auditor(Protocol):
    """The protocol every auditor implements."""

    id: str

    def triggers_on(self, changed_files: list[Path]) -> bool: ...

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]: ...


class AuditorPool:
    """Runs auditors in parallel using a thread pool (each is subprocess-heavy)."""

    def __init__(self, auditors: list[Auditor], *, max_workers: int = 8) -> None:
        self.auditors = auditors
        self.max_workers = max_workers

    def run(
        self,
        *,
        workspace: Path,
        changed_files: list[Path],
    ) -> list[Finding]:
        active = [a for a in self.auditors if a.triggers_on(changed_files)]
        results: list[Finding] = []
        if not active:
            return results

        # Snapshot the current context so worker threads inherit ContextVars
        # (e.g., the cost-tracking run context set by the CLI).
        ctx = contextvars.copy_context()

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(active))) as executor:
            futures = {
                executor.submit(ctx.run, a.audit, workspace, changed_files): a
                for a in active
            }
            for future in as_completed(futures):
                try:
                    findings = future.result()
                    results.extend(findings)
                except Exception as e:
                    auditor = futures[future]
                    # Surface auditor failures as INFO-severity findings so they're visible.
                    from claude_orchestrator.models import SARIFLocation
                    results.append(Finding(
                        rule_id=f"vroom.auditor_failed:{auditor.id}",
                        severity="info",
                        location=SARIFLocation(uri="(internal)", start_line=0),
                        message=f"auditor {auditor.id} raised: {e}",
                        proposed_fix=None,
                        auditor=auditor.id,
                        fingerprint=f"{auditor.id}:failed",
                        status="open",
                    ))
        return results
