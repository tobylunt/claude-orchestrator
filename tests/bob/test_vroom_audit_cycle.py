"""Tests for the full Vroom audit cycle: pool -> coalesce -> persist -> triage -> fix."""
import io
import json
import subprocess as sp
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.vroom.audit_cycle import VroomAuditCycle
from claude_orchestrator.bob.vroom.coalescer import FindingCluster
from claude_orchestrator.models import Finding, SARIFLocation


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "x.py").write_text("def thing():\n    pass\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    (tmp_path / ".bob").mkdir()
    return tmp_path


def _f(rule, severity, auditor, line=1, fp=None):
    return Finding(
        rule_id=rule,
        severity=severity,
        location=SARIFLocation(uri="x.py", start_line=line),
        message=f"{rule}",
        proposed_fix=None,
        auditor=auditor,
        fingerprint=fp or f"{auditor}:{rule}:{line}",
        status="open",
    )


def test_audit_cycle_persists_findings_jsonl(repo: Path):
    """Findings should be appended to .bob/findings.jsonl."""
    pool = MagicMock()
    pool.run = MagicMock(return_value=[
        _f("rule-a", "high", "claude"),
        _f("rule-a", "high", "codex", fp="dup"),
    ])
    triage_gate = MagicMock()
    triage_gate.run = MagicMock(return_value=[])  # no approvals
    fix_driver = MagicMock()

    cycle = VroomAuditCycle(
        project_root=repo,
        auditor_pool=pool,
        triage_gate=triage_gate,
        fix_driver=fix_driver,
    )
    clusters = cycle.run()

    findings_path = repo / ".bob" / "findings.jsonl"
    assert findings_path.exists()
    lines = findings_path.read_text().splitlines()
    assert len(lines) == 2  # both raw findings persisted
    parsed = [json.loads(l) for l in lines]
    rules = {p["rule_id"] for p in parsed}
    assert rules == {"rule-a"}


def test_audit_cycle_runs_fix_loop_on_approved_clusters(repo: Path):
    """Approved triage decisions trigger fix-loop runs."""
    pool = MagicMock()
    pool.run = MagicMock(return_value=[
        _f("rule-x", "high", "claude"),
        _f("rule-x", "high", "codex", fp="dup-x"),
    ])

    # Triage gate approves the one cluster.
    from claude_orchestrator.bob.vroom.triage import TriageDecision
    def fake_triage_run(clusters):
        return [TriageDecision(cluster=clusters[0], action="approve")]
    triage_gate = MagicMock()
    triage_gate.run = MagicMock(side_effect=fake_triage_run)

    from claude_orchestrator.bob.vroom.fix_loop import FixOutcome
    fix_driver = MagicMock()
    fix_driver.fix = MagicMock(return_value=FixOutcome(
        finding_id="rule-x:x.py:1",
        branch="vroom/x",
        merged=True,
        reason=None,
    ))

    cycle = VroomAuditCycle(
        project_root=repo,
        auditor_pool=pool,
        triage_gate=triage_gate,
        fix_driver=fix_driver,
    )
    cycle.run()

    fix_driver.fix.assert_called_once()


def test_audit_cycle_marks_wontfix_clusters_in_findings_jsonl(repo: Path):
    """Wontfix decisions should append a status='wontfix' record per finding."""
    pool = MagicMock()
    pool.run = MagicMock(return_value=[
        _f("rule-w", "low", "claude"),
        _f("rule-w", "low", "codex", fp="dup-w"),
    ])

    from claude_orchestrator.bob.vroom.triage import TriageDecision
    def fake_triage_run(clusters):
        return [TriageDecision(cluster=clusters[0], action="wontfix")]
    triage_gate = MagicMock()
    triage_gate.run = MagicMock(side_effect=fake_triage_run)

    fix_driver = MagicMock()
    cycle = VroomAuditCycle(
        project_root=repo,
        auditor_pool=pool,
        triage_gate=triage_gate,
        fix_driver=fix_driver,
    )
    cycle.run()

    # findings.jsonl should have the original 2 raw findings + 2 wontfix updates.
    lines = (repo / ".bob" / "findings.jsonl").read_text().splitlines()
    parsed = [json.loads(l) for l in lines]
    statuses = [p["status"] for p in parsed]
    # Two open + two wontfix.
    assert statuses.count("wontfix") == 2
