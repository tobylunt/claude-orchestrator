"""Vroom triage gate (third HITL gate, spec §6.5/§6.8).

After the auditor pool produces clusters, the triage gate shows the user
each cluster (severity-sorted) and collects approve/skip/wontfix decisions.
Default consensus threshold is 2 (clusters with single-auditor findings
are suppressed from active triage but persist in findings.jsonl).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from claude_orchestrator.bob.vroom.coalescer import FindingCluster

if TYPE_CHECKING:
    from claude_orchestrator.bob.yolo import YoloConfig

_SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


@dataclass(frozen=True)
class TriageDecision:
    cluster: FindingCluster
    action: Literal["approve", "skip", "wontfix"]


def triage_clusters(
    clusters: list[FindingCluster],
    *,
    min_consensus: int = 2,
) -> list[FindingCluster]:
    """Filter to clusters that meet the minimum consensus threshold."""
    return [c for c in clusters if c.consensus_count >= min_consensus]


class VroomTriageGate:
    """Per-cluster triage gate. Shows each cluster, collects a/s/w."""

    name = "vroom_triage"

    def __init__(
        self,
        *,
        min_consensus: int = 2,
        yolo: "YoloConfig | None" = None,
    ) -> None:
        self.min_consensus = min_consensus
        self.yolo = yolo

    def run(self, clusters: list[FindingCluster]) -> list[TriageDecision]:
        eligible = triage_clusters(clusters, min_consensus=self.min_consensus)
        decisions: list[TriageDecision] = []

        # YOLO mode: auto-decide based on severity threshold.
        if self.yolo is not None and self.yolo.enabled:
            threshold_rank = _SEVERITY_RANK[self.yolo.vroom_severity]
            for c in eligible:
                cluster_rank = _SEVERITY_RANK[c.severity]
                if cluster_rank >= threshold_rank:
                    print(f"[YOLO] auto-approve cluster: {c.primary.rule_id} "
                          f"({c.severity}, {c.consensus_count} auditors)")
                    decisions.append(TriageDecision(cluster=c, action="approve"))
                else:
                    print(f"[YOLO] auto-skip cluster: {c.primary.rule_id} "
                          f"({c.severity}, below {self.yolo.vroom_severity} threshold)")
                    decisions.append(TriageDecision(cluster=c, action="skip"))
            return decisions

        # Default: interactive prompt (unchanged from M3).
        for c in eligible:
            print("\n" + "=" * 60)
            print(f"Vroom finding (severity: {c.severity}, agreed by {c.consensus_count} auditor(s)):")
            primary = c.primary
            print(f"  Rule: {primary.rule_id}")
            print(f"  File: {primary.location.uri}:{primary.location.start_line}")
            print(f"  Auditors: {', '.join(c.auditors)}")
            print(f"  Message: {primary.message}")
            print("=" * 60)
            try:
                answer = input("Action? [a]pprove / [s]kip / [w]ontfix: ").strip().lower()
            except EOFError:
                answer = "s"
            if answer.startswith("a"):
                action = "approve"
            elif answer.startswith("w"):
                action = "wontfix"
            else:
                action = "skip"
            decisions.append(TriageDecision(cluster=c, action=action))  # type: ignore[arg-type]
        return decisions
