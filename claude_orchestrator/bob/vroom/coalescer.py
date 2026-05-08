"""Coalesce SARIF findings from parallel auditors into deduped, clustered groups."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from claude_orchestrator.models import Finding


_SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


@dataclass
class FindingCluster:
    """One coalesced cluster of related findings."""

    findings: list[Finding]
    severity: Literal["critical", "high", "medium", "low", "info"]
    auditors: list[str]
    consensus_count: int

    @property
    def primary(self) -> Finding:
        """Representative finding for display in triage."""
        return self.findings[0]


def coalesce_findings(
    findings: list[Finding],
    *,
    proximity: int = 5,
) -> list[FindingCluster]:
    """Dedupe by fingerprint, cluster nearby lines on the same (rule_id, uri),
    assign max severity, sort by severity (critical → info).
    """
    # Group by fingerprint first (exact dedupe).
    by_fp: dict[str, list[Finding]] = {}
    for f in findings:
        by_fp.setdefault(f.fingerprint, []).append(f)

    deduped = list(by_fp.values())

    # Group deduped findings into proximity-clusters by (rule_id, uri).
    by_key: dict[tuple[str, str], list[list[list[Finding]]]] = {}

    for group in deduped:
        rep = group[0]
        key = (rep.rule_id, rep.location.uri)
        existing = by_key.setdefault(key, [])
        # Find a cluster within proximity of rep.location.start_line.
        attached = False
        for cluster in existing:
            cluster_lines = [g[0].location.start_line for g in cluster]
            if any(abs(rep.location.start_line - l) <= proximity for l in cluster_lines):
                cluster.append(group)
                attached = True
                break
        if not attached:
            existing.append([group])

    # Build FindingCluster objects.
    output: list[FindingCluster] = []
    for key, cluster_list in by_key.items():
        for cluster in cluster_list:
            all_findings = [f for group in cluster for f in group]
            severities = [_SEVERITY_ORDER[f.severity] for f in all_findings]
            top_severity_idx = min(severities)
            severity_str = next(s for s, i in _SEVERITY_ORDER.items() if i == top_severity_idx)
            auditors = sorted({f.auditor for f in all_findings})
            output.append(FindingCluster(
                findings=all_findings,
                severity=severity_str,  # type: ignore[arg-type]
                auditors=auditors,
                consensus_count=len(auditors),
            ))

    output.sort(key=lambda c: _SEVERITY_ORDER[c.severity])
    return output
