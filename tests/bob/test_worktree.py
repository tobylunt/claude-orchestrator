"""Tests for the git worktree wrapper."""
import subprocess
from pathlib import Path

import pytest

from claude_orchestrator.bob.worktree import (
    WorktreeError,
    add_worktree,
    list_worktrees,
    remove_worktree,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit on main."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


def test_add_worktree_creates_branch_and_path(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    assert target.exists()
    assert (target / "README.md").exists()


def test_add_worktree_lists_after_create(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    worktrees = list_worktrees(repo)
    assert any(wt.path == target for wt in worktrees)


def test_remove_worktree_cleans_up(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    remove_worktree(repo, target)
    assert not target.exists()
    worktrees = list_worktrees(repo)
    assert all(wt.path != target for wt in worktrees)


def test_add_worktree_rejects_existing_path(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    with pytest.raises(WorktreeError):
        add_worktree(repo, target, branch="bob/001-other")


def test_add_worktree_attaches_to_existing_branch(repo: Path, tmp_path: Path):
    """add_worktree should succeed if the branch already exists in the repo."""
    target1 = tmp_path / "wt1"
    add_worktree(repo, target1, branch="bob/feat")
    # Remove the worktree but keep the branch.
    remove_worktree(repo, target1)

    # Now the branch exists with no worktree. Add a new worktree on the same branch.
    target2 = tmp_path / "wt2"
    add_worktree(repo, target2, branch="bob/feat")
    assert target2.exists()
    assert (target2 / "README.md").exists()


def test_add_worktree_handles_stale_registration(repo: Path, tmp_path: Path):
    """If a previous worktree was registered but the directory was deleted,
    add_worktree should prune the stale entry and proceed.
    """
    target = tmp_path / "wt" / "001-feat"
    add_worktree(repo, target, branch="bob/feat")

    # Simulate disk-cleanup: the directory is gone but git's registration remains.
    import shutil
    shutil.rmtree(target)
    # The path is gone but `git worktree list` still has it registered:
    worktrees = list_worktrees(repo)
    assert any(wt.path == target for wt in worktrees), \
        "test setup invalid: expected stale registration to remain after rmtree"

    # add_worktree should succeed by pruning the stale entry first.
    target2 = tmp_path / "wt" / "001-feat-retry"  # different path so registration mismatch
    add_worktree(repo, target2, branch="bob/feat-retry")
    assert target2.exists()

    # AND it should also handle the case where the new target is the SAME path
    # as the stale registration:
    target_same = tmp_path / "wt" / "001-feat"  # back to original (currently registered as missing)
    add_worktree(repo, target_same, branch="bob/feat-take2")
    assert target_same.exists()
