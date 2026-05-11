"""Tests for the SARIF coalescer."""
from claude_orchestrator.bob.vroom.coalescer import (
    FindingCluster,
    coalesce_findings,
)
from claude_orchestrator.models import Finding, SARIFLocation


def _f(rule="r", uri="x.py", line=1, severity="medium", auditor="a", fp=None):
    return Finding(
        rule_id=rule,
        severity=severity,
        location=SARIFLocation(uri=uri, start_line=line),
        message=f"{rule} at {uri}:{line}",
        proposed_fix=None,
        auditor=auditor,
        fingerprint=fp or f"{rule}:{uri}:{line}",
        status="open",
    )


def test_coalesce_dedupes_identical_findings():
    findings = [
        _f(auditor="claude"),
        _f(auditor="codex"),  # same fingerprint
    ]
    clusters = coalesce_findings(findings)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert sorted(cluster.auditors) == ["claude", "codex"]
    assert cluster.findings[0].rule_id == "r"


def test_coalesce_separates_different_rules_in_same_file():
    findings = [
        _f(rule="rule-a", line=1, auditor="claude"),
        _f(rule="rule-b", line=1, auditor="codex"),
    ]
    clusters = coalesce_findings(findings)
    assert len(clusters) == 2


def test_coalesce_clusters_nearby_lines():
    findings = [
        _f(rule="r", uri="x.py", line=10, auditor="claude"),
        _f(rule="r", uri="x.py", line=12, auditor="codex"),  # within proximity
    ]
    clusters = coalesce_findings(findings, proximity=5)
    assert len(clusters) == 1
    assert sorted(clusters[0].auditors) == ["claude", "codex"]


def test_coalesce_does_not_cluster_distant_lines():
    findings = [
        _f(rule="r", uri="x.py", line=10, auditor="claude"),
        _f(rule="r", uri="x.py", line=100, auditor="codex"),
    ]
    clusters = coalesce_findings(findings, proximity=5)
    assert len(clusters) == 2


def test_coalesce_assigns_max_severity_to_cluster():
    findings = [
        _f(severity="low", auditor="claude", fp="f1"),
        _f(severity="critical", auditor="codex", fp="f1"),
    ]
    clusters = coalesce_findings(findings)
    assert clusters[0].severity == "critical"


def test_coalesce_sorts_clusters_by_severity():
    findings = [
        _f(rule="r1", severity="low", fp="r1:1"),
        _f(rule="r2", severity="critical", fp="r2:1"),
        _f(rule="r3", severity="high", fp="r3:1"),
    ]
    clusters = coalesce_findings(findings)
    assert [c.severity for c in clusters] == ["critical", "high", "low"]


def test_cluster_records_consensus_count():
    findings = [
        _f(auditor="claude", fp="f"),
        _f(auditor="codex", fp="f"),
        _f(auditor="semgrep", fp="f"),
    ]
    clusters = coalesce_findings(findings)
    assert clusters[0].consensus_count == 3


def test_coalesce_clusters_findings_with_normalized_rule_ids():
    """claude.sql-injection + codex.sql-injection at the same line should cluster."""
    findings = [
        _f(rule="claude.sql-injection", uri="app.py", line=13,
           severity="critical", auditor="claude_architect", fp="c1"),
        _f(rule="codex.sql-injection", uri="app.py", line=13,
           severity="critical", auditor="codex_security", fp="c2"),
    ]
    clusters = coalesce_findings(findings)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert sorted(cluster.auditors) == ["claude_architect", "codex_security"]
    assert cluster.consensus_count == 2


def test_coalesce_normalizes_with_proximity():
    """Same semantic issue at nearby lines should still cluster as consensus=2."""
    findings = [
        _f(rule="claude.command-injection", uri="app.py", line=20,
           severity="critical", auditor="claude_architect", fp="c1"),
        _f(rule="codex.command-injection", uri="app.py", line=22,
           severity="critical", auditor="codex_security", fp="c2"),
    ]
    clusters = coalesce_findings(findings, proximity=5)
    assert len(clusters) == 1
    assert clusters[0].consensus_count == 2


def test_coalesce_keeps_different_rule_classes_separate():
    """Different rule classes (sql-injection vs command-injection) should NOT cluster."""
    findings = [
        _f(rule="claude.sql-injection", uri="app.py", line=13,
           severity="critical", auditor="claude_architect", fp="c1"),
        _f(rule="codex.command-injection", uri="app.py", line=13,
           severity="critical", auditor="codex_security", fp="c2"),
    ]
    clusters = coalesce_findings(findings)
    assert len(clusters) == 2  # different rule classes, distinct clusters


def test_coalesce_preserves_original_rule_id_in_findings():
    """The Finding's original rule_id should be preserved even after clustering."""
    findings = [
        _f(rule="claude.sql-injection", uri="app.py", line=13,
           severity="critical", auditor="claude_architect", fp="c1"),
        _f(rule="codex.sql-injection", uri="app.py", line=13,
           severity="critical", auditor="codex_security", fp="c2"),
    ]
    clusters = coalesce_findings(findings)
    rule_ids = {f.rule_id for f in clusters[0].findings}
    assert rule_ids == {"claude.sql-injection", "codex.sql-injection"}
