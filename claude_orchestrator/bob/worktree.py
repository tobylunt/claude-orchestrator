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


def _branch_exists(repo: Path, branch: str) -> bool:
    """Check whether `branch` exists locally in `repo`."""
    result = _run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=repo,
    )
    return result.returncode == 0


def add_worktree(repo: Path, target: Path, branch: str) -> None:
    """Create a new worktree at `target` on `branch`.

    If `branch` does not exist, it is created from the current HEAD of `repo`.
    If `branch` already exists, the worktree is attached to it (no -b flag).
    Either case, the resulting worktree is checked out at `branch`'s tip.

    Stale worktree registrations (path missing but still listed by git)
    are pruned automatically — this happens when a previous worktree was
    deleted from disk without going through `git worktree remove`.
    """
    if target.exists():
        raise WorktreeError(f"worktree path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    # Prune any stale registrations (worktree paths that no longer exist on disk).
    # This is safe: git worktree prune only removes registrations whose path is missing.
    _run(["git", "worktree", "prune"], cwd=repo)

    if _branch_exists(repo, branch):
        cmd = ["git", "worktree", "add", str(target), branch]
    else:
        cmd = ["git", "worktree", "add", "-b", branch, str(target)]

    result = _run(cmd, cwd=repo)
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
