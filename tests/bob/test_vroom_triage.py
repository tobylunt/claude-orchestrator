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


def test_triage_gate_yolo_auto_approves_high_severity():
    """In YOLO mode, clusters at/above vroom_severity get auto-approved."""
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                      vroom_severity="high")

    f1 = _f("rule-x", "high", "claude")
    f2 = _f("rule-x", "high", "codex", fp="dup")
    cluster = _cluster("high", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate(yolo=yolo)
    decisions = gate.run([cluster])
    assert len(decisions) == 1
    assert decisions[0].action == "approve"


def test_triage_gate_yolo_auto_skips_below_threshold():
    """Below the threshold, YOLO defaults to skip (no prompt, no merge)."""
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                      vroom_severity="high")

    f1 = _f("rule-x", "low", "claude")
    f2 = _f("rule-x", "low", "codex", fp="dup")
    cluster = _cluster("low", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate(yolo=yolo)
    decisions = gate.run([cluster])
    assert len(decisions) == 1
    assert decisions[0].action == "skip"


def test_triage_gate_yolo_auto_approves_critical():
    """Critical (highest severity) is always at/above any threshold."""
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                      vroom_severity="medium")

    f1 = _f("rule-c", "critical", "claude")
    f2 = _f("rule-c", "critical", "codex", fp="dup-c")
    cluster = _cluster("critical", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate(yolo=yolo)
    decisions = gate.run([cluster])
    assert decisions[0].action == "approve"


def test_triage_gate_yolo_with_mixed_severities():
    """A mix of severities: high+critical auto-approve, low+medium auto-skip."""
    from claude_orchestrator.bob.yolo import YoloConfig
    yolo = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0,
                      vroom_severity="high")

    fc1 = _f("crit", "critical", "claude", fp="c1")
    fc2 = _f("crit", "critical", "codex", fp="c2")
    fh1 = _f("high", "high", "claude", fp="h1")
    fh2 = _f("high", "high", "codex", fp="h2")
    fm1 = _f("med", "medium", "claude", fp="m1")
    fm2 = _f("med", "medium", "codex", fp="m2")
    fl1 = _f("low", "low", "claude", fp="l1")
    fl2 = _f("low", "low", "codex", fp="l2")
    clusters = [
        _cluster("critical", consensus=2, findings=[fc1, fc2]),
        _cluster("high", consensus=2, findings=[fh1, fh2]),
        _cluster("medium", consensus=2, findings=[fm1, fm2]),
        _cluster("low", consensus=2, findings=[fl1, fl2]),
    ]

    gate = VroomTriageGate(yolo=yolo)
    decisions = gate.run(clusters)
    actions = [d.action for d in decisions]
    assert actions == ["approve", "approve", "skip", "skip"]


def test_triage_gate_default_mode_still_prompts(monkeypatch):
    """Without YOLO, the gate still prompts the user."""
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO("a\n"))
    f1 = _f("r", "low", "claude")
    f2 = _f("r", "low", "codex", fp="x")
    cluster = _cluster("low", consensus=2, findings=[f1, f2])

    gate = VroomTriageGate()  # no yolo
    decisions = gate.run([cluster])
    assert decisions[0].action == "approve"  # user said "a"
