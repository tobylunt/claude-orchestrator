"""Thin wrapper around `git worktree`.

Just enough to create a per-feature worktree on a branch, list them,
and remove them cleanly. Shells to git rather than using a Python lib
to keep the dependency surface small.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """A `git worktree` command failed."""


@dataclass
class WorktreeEntry:
    path: Path
    branch: str | None
    commit: str | None


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )


def add_worktree(repo: Path, target: Path, branch: str) -> None:
    """Create a new worktree at `target` on a fresh branch `branch`.

    The branch is created from the current HEAD of `repo`.
    """
    if target.exists():
        raise WorktreeError(f"worktree path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        ["git", "worktree", "add", "-b", branch, str(target)],
        cwd=repo,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree add failed: {result.stderr.strip()}"
        )


def remove_worktree(repo: Path, target: Path) -> None:
    """Remove the worktree at `target`. Force flag handles dirty trees."""
    result = _run(
        ["git", "worktree", "remove", "--force", str(target)],
        cwd=repo,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree remove failed: {result.stderr.strip()}"
        )


def list_worktrees(repo: Path) -> list[WorktreeEntry]:
    """Return all registered worktrees for `repo`."""
    result = _run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree list failed: {result.stderr.strip()}"
        )

    entries: list[WorktreeEntry] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                entries.append(_entry_from_dict(current))
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(_entry_from_dict(current))
    return entries


def _entry_from_dict(d: dict[str, str]) -> WorktreeEntry:
    return WorktreeEntry(
        path=Path(d["worktree"]),
        branch=d.get("branch"),
        commit=d.get("HEAD"),
    )
