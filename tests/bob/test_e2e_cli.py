"""End-to-end test exercising `python -m claude_orchestrator.bob.cli run`.

This is the same shape as test_e2e_smoke.py but invoked through the CLI
boundary so we know the full subprocess path works. The fake `claude`
binary in PATH is the only stub.
"""
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Tiny project: git repo with one passing test."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "test_smoke.py").write_text(
        "def test_passes():\n    assert True\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


@pytest.fixture
def fake_claude_dir(tmp_path_factory) -> Path:
    """A directory containing a fake `claude` script that emits EXIT_SIGNAL."""
    d = tmp_path_factory.mktemp("fake-claude-bin")
    script = d / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return d


def test_bob_run_against_tiny_project(
    project_root: Path, fake_claude_dir: Path, monkeypatch
):
    """`bob run --inputs spec.md` runs the full pipeline and merges one feature."""
    spec_path = project_root / "spec.md"
    spec_path.write_text(dedent("""\
        # CLI smoke
        ## Motivation
        Make sure bob run actually works end-to-end through the CLI.
        ## Features
        ### F1: passing-tests
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: Already implemented; the loop should exit on iteration 1.
    """))

    # Put fake claude on PATH; the wiring uses claude_cmd="claude" by default.
    env = os.environ.copy()
    env["PATH"] = str(fake_claude_dir) + os.pathsep + env["PATH"]

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(project_root),
         "--inputs", str(spec_path),
         "--max-iterations", "3",
         "--no-gate", "post_duplo"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, (
        f"bob run failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # State was created.
    bob_dir = project_root / ".bob"
    assert bob_dir.exists()
    assert (bob_dir / "spec.md").exists()
    assert (bob_dir / "cursor.json").exists()
    feature_dirs = list((bob_dir / "features").iterdir())
    assert len(feature_dirs) == 1

    # Feature reached merged status.
    import json
    state = json.loads((feature_dirs[0] / "state.json").read_text())
    assert state["status"] == "merged", f"expected merged, got {state['status']}"

    # Worktree was cleaned up after merge.
    worktrees = list((bob_dir / "worktrees").iterdir()) if (bob_dir / "worktrees").exists() else []
    assert worktrees == [], f"expected no worktrees after merge, got {worktrees}"

    # Lock file should be gone.
    assert not (bob_dir / ".bob.lock").exists()
