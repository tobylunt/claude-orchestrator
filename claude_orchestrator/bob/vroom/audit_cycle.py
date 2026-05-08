"""Full Vroom audit cycle: pool -> coalesce -> persist -> triage -> fix-loop.

Wires M3's pool/coalescer/triage/fix_loop into a single callable that
VroomDaemon invokes on each cycle.
"""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.bob.state_io import append_jsonl
from claude_orchestrator.bob.vroom.auditor_pool import AuditorPool
from claude_orchestrator.bob.vroom.coalescer import (
    FindingCluster,
    coalesce_findings,
)
from claude_orchestrator.bob.vroom.fix_loop import FixLoopDriver
from claude_orchestrator.bob.vroom.triage import (
    TriageDecision,
    VroomTriageGate,
)
from claude_orchestrator.models import Finding


def _finding_id(finding: Finding) -> str:
    """Stable id derived from rule + location for fix-loop branch naming."""
    line = finding.location.start_line
    safe_uri = finding.location.uri.replace("/", "-").replace(".", "-")
    return f"{finding.rule_id}--{safe_uri}--{line}"


class VroomAuditCycle:
    """One full audit cycle: collect findings, coalesce, persist, triage, fix."""

    def __init__(
        self,
        *,
        project_root: Path,
        auditor_pool: AuditorPool,
        triage_gate: VroomTriageGate,
        fix_driver: FixLoopDriver | None = None,
    ) -> None:
        self.project_root = project_root
        self.auditor_pool = auditor_pool
        self.triage_gate = triage_gate
        self.fix_driver = fix_driver
        self.findings_path = project_root / ".bob" / "findings.jsonl"

    def run(self, *, changed_files: list[Path] | None = None) -> list[FindingCluster]:
        """Run one cycle. Returns the coalesced clusters (post-triage actions
        already applied).
        """
        # 1. Collect findings.
        findings = self.auditor_pool.run(
            workspace=self.project_root,
            changed_files=changed_files or [],
        )

        # 2. Persist raw findings to findings.jsonl (append-only).
        for f in findings:
            append_jsonl(self.findings_path, f.model_dump(mode="json"))

        # 3. Coalesce.
        clusters = coalesce_findings(findings)

        # 4. Triage (HITL or YOLO auto-approve).
        decisions: list[TriageDecision] = self.triage_gate.run(clusters)

        # 5. Dispatch fix-loops for approved clusters.
        for d in decisions:
            primary = d.cluster.findings[0]
            fid = _finding_id(primary)
            if d.action == "approve" and self.fix_driver is not None:
                outcome = self.fix_driver.fix(d.cluster, finding_id=fid)
                # Record outcome in findings.jsonl as a status update entry.
                for f in d.cluster.findings:
                    update = f.model_dump(mode="json")
                    update["status"] = "merged" if outcome.merged else "in_progress"
                    if outcome.reason:
                        update["fix_reason"] = outcome.reason
                    append_jsonl(self.findings_path, update)
            elif d.action == "wontfix":
                for f in d.cluster.findings:
                    update = f.model_dump(mode="json")
                    update["status"] = "wontfix"
                    append_jsonl(self.findings_path, update)
            # 'skip' is a no-op (finding stays 'open' for next cycle).

        return clusters
