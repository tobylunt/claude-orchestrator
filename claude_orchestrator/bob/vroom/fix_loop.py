"""Vroom fix-loop driver.

For each approved triage cluster:
1. Create a vroom/<finding-id> branch + worktree from current main.
2. Run an isolated McLoop on it, with the finding as the spec.
3. Inspect the resulting diff:
   - If ≤max_lines and ≤max_files: git merge --ff-only (or --no-ff fallback).
     On success: branch deleted, worktree removed, return outcome.merged=True.
   - Else: leave the branch + worktree for human review; return outcome.merged=False
     with reason="diff too large for auto-merge".
4. If McLoop didn't produce changes: return outcome.merged=False with reason.
"""

from __future__ import annotations

import subprocess as sp
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from claude_orchestrator.bob.vroom.coalescer import FindingCluster
from claude_orchestrator.bob.worktree import (
    WorktreeError,
    add_worktree,
    remove_worktree,
)
from claude_orchestrator.models import Finding


@dataclass(frozen=True)
class FixOutcome:
    finding_id: str
    branch: str
    merged: bool
    reason: str | None  # set when not merged


class FixLoopDriver:
    """Drives a fix attempt for one approved finding cluster."""

    def __init__(
        self,
        *,
        repo: Path,
        run_mcloop: Callable[..., bool],
        max_lines: int = 100,
        max_files: int = 5,
        worktree_dir: Path | None = None,
    ) -> None:
        self.repo = repo
        self.run_mcloop = run_mcloop
        self.max_lines = max_lines
        self.max_files = max_files
        self.worktree_dir = worktree_dir or (repo / ".bob" / "vroom-worktrees")

    def fix(
        self,
        cluster: FindingCluster,
        *,
        finding_id: str,
    ) -> FixOutcome:
        branch = f"vroom/{finding_id}"
        target = self.worktree_dir / finding_id

        # 1. Create the worktree on a fresh vroom/<id> branch.
        try:
            add_worktree(self.repo, target, branch=branch)
        except WorktreeError as e:
            return FixOutcome(
                finding_id=finding_id,
                branch=branch,
                merged=False,
                reason=f"worktree creation failed: {e}",
            )

        # 2. Run the injected mcloop callable.
        finding = cluster.findings[0]
        try:
            success = self.run_mcloop(
                branch_name=branch,
                workspace=target,
                finding=finding,
            )
        except Exception as e:
            return FixOutcome(
                finding_id=finding_id,
                branch=branch,
                merged=False,
                reason=f"fix-loop raised: {e}",
            )

        if not success:
            return FixOutcome(
                finding_id=finding_id,
                branch=branch,
                merged=False,
                reason="fix-loop returned False (no progress)",
            )

        # 3. Inspect the diff size.
        diff_stat = sp.run(
            ["git", "-C", str(self.repo), "diff", "--numstat", f"main..{branch}"],
            capture_output=True, text=True,
        )
        if diff_stat.returncode != 0:
            return FixOutcome(
                finding_id=finding_id,
                branch=branch,
                merged=False,
                reason=f"diff failed: {diff_stat.stderr.strip()}",
            )

        files_changed = 0
        lines_changed = 0
        for line in diff_stat.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                files_changed += 1
                try:
                    added = int(parts[0]) if parts[0] != "-" else 0
                    deleted = int(parts[1]) if parts[1] != "-" else 0
                    lines_changed += added + deleted
                except ValueError:
                    pass

        if files_changed > self.max_files or lines_changed > self.max_lines:
            return FixOutcome(
                finding_id=finding_id,
                branch=branch,
                merged=False,
                reason=f"diff too large for auto-merge ({files_changed} files, {lines_changed} lines; thresholds {self.max_files}/{self.max_lines})",
            )

        # 4. Try fast-forward merge first, fallback to no-ff.
        ff = sp.run(
            ["git", "-C", str(self.repo), "merge", "--ff-only", branch],
            capture_output=True, text=True,
        )
        if ff.returncode != 0:
            nf = sp.run(
                ["git", "-C", str(self.repo), "merge", "--no-ff", "-m",
                 f"Vroom auto-merge: {finding_id}", branch],
                capture_output=True, text=True,
            )
            if nf.returncode != 0:
                # Real conflict — abort and leave the branch.
                sp.run(
                    ["git", "-C", str(self.repo), "merge", "--abort"],
                    capture_output=True, text=True,
                )
                return FixOutcome(
                    finding_id=finding_id,
                    branch=branch,
                    merged=False,
                    reason=f"merge conflict: {nf.stderr.strip()}",
                )

        # 5. Cleanup: remove worktree, delete branch.
        try:
            remove_worktree(self.repo, target)
        except WorktreeError:
            pass
        sp.run(
            ["git", "-C", str(self.repo), "branch", "-d", branch],
            capture_output=True, text=True,
        )

        return FixOutcome(
            finding_id=finding_id,
            branch=branch,
            merged=True,
            reason=None,
        )
