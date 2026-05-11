"""Tests for the Vroom auditor pool."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.vroom.auditor_pool import (
    Auditor,
    AuditorPool,
)
from claude_orchestrator.models import Finding, SARIFLocation


class FakeAuditor:
    """A fake auditor that returns scripted findings."""

    def __init__(self, id: str, findings: list[Finding], slow_ms: int = 0):
        self.id = id
        self._findings = findings
        self.slow_ms = slow_ms
        self.calls = 0

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        import time
        if self.slow_ms:
            time.sleep(self.slow_ms / 1000)
        self.calls += 1
        return self._findings


def _f(rule, uri, line, severity, auditor):
    return Finding(
        rule_id=rule,
        severity=severity,
        location=SARIFLocation(uri=uri, start_line=line),
        message=f"{rule}",
        proposed_fix=None,
        auditor=auditor,
        fingerprint=f"{auditor}:{rule}:{uri}:{line}",
        status="open",
    )


def test_auditor_pool_collects_from_all_auditors(tmp_path: Path):
    a1 = FakeAuditor("a1", [_f("rule-1", "x.py", 10, "high", "a1")])
    a2 = FakeAuditor("a2", [_f("rule-2", "y.py", 20, "low", "a2")])
    pool = AuditorPool([a1, a2])
    findings = pool.run(workspace=tmp_path, changed_files=[])
    assert len(findings) == 2
    rules = sorted(f.rule_id for f in findings)
    assert rules == ["rule-1", "rule-2"]


def test_auditor_pool_respects_triggers_on(tmp_path: Path):
    """Auditors whose triggers_on returns False should be skipped."""

    class SkippingAuditor(FakeAuditor):
        def triggers_on(self, changed_files):
            return False

    a1 = FakeAuditor("a1", [_f("rule-1", "x.py", 1, "high", "a1")])
    a2 = SkippingAuditor("a2", [_f("rule-2", "y.py", 2, "high", "a2")])
    pool = AuditorPool([a1, a2])
    findings = pool.run(workspace=tmp_path, changed_files=[])
    assert len(findings) == 1
    assert findings[0].auditor == "a1"
    assert a2.calls == 0


def test_auditor_pool_runs_in_parallel(tmp_path: Path):
    """Two slow auditors should complete in roughly max(slow_ms), not sum."""
    import time
    a1 = FakeAuditor("a1", [], slow_ms=200)
    a2 = FakeAuditor("a2", [], slow_ms=200)
    pool = AuditorPool([a1, a2])
    start = time.time()
    pool.run(workspace=tmp_path, changed_files=[])
    elapsed = time.time() - start
    assert elapsed < 0.35, f"expected parallel (~0.2s), got {elapsed}s — sequential would be ~0.4s"


def test_auditor_pool_surfaces_auditor_exceptions(tmp_path: Path):
    """Auditor failures must not masquerade as a clean zero-finding cycle."""

    class RaisingAuditor(FakeAuditor):
        def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
            raise RuntimeError("api unavailable")

    pool = AuditorPool([RaisingAuditor("codex", [])])
    findings = pool.run(workspace=tmp_path, changed_files=[])
    assert len(findings) == 1
    assert findings[0].rule_id == "vroom.auditor_failed:codex"
    assert "api unavailable" in findings[0].message
