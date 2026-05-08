"""Tests for the Vroom fix-loop driver."""
import subprocess as sp
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.vroom.coalescer import FindingCluster
from claude_orchestrator.bob.vroom.fix_loop import (
    FixOutcome,
    FixLoopDriver,
)
from claude_orchestrator.models import Finding, SARIFLocation


def _cluster_with_finding(rule="r", line=1, severity="high"):
    f = Finding(
        rule_id=rule,
        severity=severity,
        location=SARIFLocation(uri="x.py", start_line=line),
        message=f"{rule}",
        proposed_fix=None,
        auditor="claude",
        fingerprint=f"{rule}:x:{line}",
        status="open",
    )
    return FindingCluster(
        findings=[f, f],  # 2 to satisfy consensus
        severity=severity,
        auditors=["claude", "codex"],
        consensus_count=2,
    )


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
    return tmp_path


def test_fix_loop_auto_merges_small_clean_diff(repo: Path):
    """A small clean diff (≤100 lines, ≤5 files) should auto-merge."""
    cluster = _cluster_with_finding()

    def mcloop_runner(*, branch_name: str, workspace: Path, finding):
        # Simulate the fix-loop adding 1 line to x.py and committing.
        (workspace / "x.py").write_text("def thing():\n    return 'fixed'\n")
        sp.run(["git", "-C", str(workspace), "add", "."], check=True)
        sp.run(
            ["git", "-C", str(workspace), "-c", "user.email=t@t.com",
             "-c", "user.name=T", "commit", "-m", "fix"],
            check=True,
        )
        return True  # success

    driver = FixLoopDriver(
        repo=repo,
        run_mcloop=mcloop_runner,
        max_lines=100,
        max_files=5,
    )
    outcome = driver.fix(cluster, finding_id="r-x-1")
    assert outcome.merged is True
    # Main now has the fix.
    main_x = (repo / "x.py").read_text()
    assert "fixed" in main_x


def test_fix_loop_keeps_branch_when_diff_too_large(repo: Path):
    """A diff above the threshold should NOT auto-merge; the branch is preserved."""
    cluster = _cluster_with_finding()

    def mcloop_runner(*, branch_name: str, workspace: Path, finding):
        # Create a 200-line file (above max_lines=100).
        (workspace / "y.py").write_text("\n".join(f"# line {i}" for i in range(200)))
        sp.run(["git", "-C", str(workspace), "add", "."], check=True)
        sp.run(
            ["git", "-C", str(workspace), "-c", "user.email=t@t.com",
             "-c", "user.name=T", "commit", "-m", "big fix"],
            check=True,
        )
        return True

    driver = FixLoopDriver(
        repo=repo,
        run_mcloop=mcloop_runner,
        max_lines=100,
        max_files=5,
    )
    outcome = driver.fix(cluster, finding_id="big-r")
    assert outcome.merged is False
    assert outcome.reason and "too large" in outcome.reason.lower()
    # Branch should still exist.
    branches = sp.run(
        ["git", "-C", str(repo), "branch", "--list"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert f"vroom/big-r" in branches


def test_fix_loop_failure_to_make_progress(repo: Path):
    """If the run_mcloop callable returns False, no merge is attempted."""
    cluster = _cluster_with_finding()

    def mcloop_runner(*, branch_name: str, workspace: Path, finding):
        return False  # mcloop didn't make changes

    driver = FixLoopDriver(
        repo=repo,
        run_mcloop=mcloop_runner,
        max_lines=100,
        max_files=5,
    )
    outcome = driver.fix(cluster, finding_id="failed-r")
    assert outcome.merged is False
    assert outcome.reason and "fix-loop" in outcome.reason.lower()
