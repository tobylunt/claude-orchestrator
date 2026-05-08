"""Tests for the Vroom triage gate."""
import io
from pathlib import Path

import pytest

from claude_orchestrator.bob.hitl.gates import GateDecision, GateRegistry, GateSkipped
from claude_orchestrator.bob.vroom.coalescer import FindingCluster
from claude_orchestrator.bob.vroom.triage import (
    TriageDecision,
    VroomTriageGate,
    triage_clusters,
)
from claude_orchestrator.models import Finding, SARIFLocation


def _f(rule, severity, auditor, line=1, fp=None):
    return Finding(
        rule_id=rule,
        severity=severity,
        location=SARIFLocation(uri="x.py", start_line=line),
        message=f"{rule} message",
        proposed_fix=None,
        auditor=auditor,
        fingerprint=fp or f"{auditor}:{rule}:{line}",
        status="open",
    )


def _cluster(severity, consensus, findings):
    return FindingCluster(
        findings=findings,
        severity=severity,
        auditors=sorted({f.auditor for f in findings}),
        consensus_count=consensus,
    )


def test_triage_filters_below_consensus_threshold():
    """Single-auditor findings should be filtered out by default (consensus >=2)."""
    f1 = _f("rule-a", "high", "claude")
    f2 = _f("rule-b", "high", "claude", fp="b")
    f3 = _f("rule-b", "high", "codex", line=2, fp="b2")
    clusters = [
        _cluster("high", consensus=1, findings=[f1]),
        _cluster("high", consensus=2, findings=[f2, f3]),
    ]
    triaged = [c for c in clusters if c.consensus_count >= 2]
    assert len(triaged) == 1
    assert triaged[0].findings[0].rule_id == "rule-b"


def test_triage_approve_via_gate(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("a\n"))  # 'a' for approve
    f1 = _f("rule-x", "high", "claude")
    f2 = _f("rule-x", "high", "codex", fp="dup")
    cluster = _cluster("high", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate()
    decision = gate.run([cluster])
    assert len(decision) == 1
    assert decision[0].cluster.findings[0].rule_id == "rule-x"
    assert decision[0].action == "approve"


def test_triage_skip_via_gate(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("s\n"))
    f1 = _f("r", "high", "claude")
    f2 = _f("r", "high", "codex", fp="x")
    cluster = _cluster("high", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate()
    decisions = gate.run([cluster])
    assert decisions[0].action == "skip"


def test_triage_wontfix_via_gate(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("w\n"))
    f1 = _f("r", "high", "claude")
    f2 = _f("r", "high", "codex", fp="x")
    cluster = _cluster("high", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate()
    decisions = gate.run([cluster])
    assert decisions[0].action == "wontfix"


def test_triage_handles_multiple_clusters_in_sequence(monkeypatch):
    """Multiple clusters: one approve, one skip."""
    monkeypatch.setattr("sys.stdin", io.StringIO("a\ns\n"))
    f1 = _f("r1", "critical", "claude")
    f1b = _f("r1", "critical", "codex", fp="r1-dup")
    f2 = _f("r2", "low", "claude")
    f2b = _f("r2", "low", "codex", fp="r2-dup")
    c1 = _cluster("critical", consensus=2, findings=[f1, f1b])
    c2 = _cluster("low", consensus=2, findings=[f2, f2b])

    gate = VroomTriageGate()
    decisions = gate.run([c1, c2])
    assert len(decisions) == 2
    assert decisions[0].action == "approve"
    assert decisions[1].action == "skip"


def test_triage_clusters_helper_filters_consensus():
    """The triage_clusters helper applies the consensus filter."""
    f1 = _f("rule-a", "high", "claude")
    f2 = _f("rule-b", "high", "claude", fp="b")
    f3 = _f("rule-b", "high", "codex", fp="b2")
    clusters = [
        _cluster("high", consensus=1, findings=[f1]),
        _cluster("high", consensus=2, findings=[f2, f3]),
    ]
    eligible = triage_clusters(clusters, min_consensus=2)
    assert len(eligible) == 1
    assert eligible[0].findings[0].rule_id == "rule-b"
