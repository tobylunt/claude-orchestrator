"""End-to-end smoke test for `bob run --yolo --sandbox docker --max-cost`.

Doesn't run real Docker — uses BOB_USE_STUB_ORCHESTRA=1 and a fake claude
on PATH. The point is to verify that YOLO config + sandbox flag + max-cost
make it through the CLI → wiring → coordinator chain without crashing,
and the YOLO invariants are enforced at the right layer.
"""
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
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
    """A directory with a fake claude that emits EXIT_SIGNAL on stdout."""
    d = tmp_path_factory.mktemp("fake-claude-bin")
    script = d / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return d


def test_yolo_invariants_block_invalid_run(project_root: Path):
    """`bob run --yolo` without --sandbox docker exits with the invariant error."""
    spec = project_root / "spec.md"
    spec.write_text(dedent("""\
        # YOLO invariant test
        ## Motivation
        Verify YOLO without docker is rejected.
        ## Features
        ### F1: noop
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: noop
    """))

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(project_root),
         "--inputs", str(spec),
         "--yolo",
         "--max-cost", "10",
         "--no-gate", "post_duplo",
         # NO --sandbox docker — invariant violation
         ],
        capture_output=True, text=True,
        env={**os.environ, "BOB_USE_STUB_ORCHESTRA": "1"},
    )
    assert result.returncode != 0
    err = (result.stdout + result.stderr).lower()
    assert "sandbox" in err or "docker" in err


def test_yolo_smoke_with_valid_invariants(
    project_root: Path, fake_claude_dir: Path
):
    """`bob run --yolo --sandbox docker --max-cost 10` with stubs passes invariant
    validation. We don't actually run docker (subprocess would fail without daemon),
    but the CLI accepts the flags and the YoloConfig validates.

    To avoid actually spawning DockerExecutor, we use --no-gate to skip and
    BOB_SANDBOX_TIER=host to override (but invariants might block this).

    Simplification: just confirm the yolo banner appears in stdout when config is valid.
    The actual McLoop won't run because the docker executor will fail, but the
    YoloConfig validation BEFORE that point should print the banner.
    """
    spec = project_root / "spec.md"
    spec.write_text(dedent("""\
        # YOLO smoke
        ## Motivation
        Verify the YOLO banner prints when config is valid.
        ## Features
        ### F1: noop
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: noop
    """))

    env = {
        **os.environ,
        "PATH": str(fake_claude_dir) + os.pathsep + os.environ.get("PATH", ""),
        "BOB_USE_STUB_ORCHESTRA": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(project_root),
         "--inputs", str(spec),
         "--yolo",
         "--sandbox", "docker",
         "--max-cost", "10",
         "--max-iterations", "1",
         "--no-gate", "post_duplo"],
        capture_output=True, text=True,
        env=env,
        timeout=60,
    )
    # The YOLO banner should appear in stdout regardless of what happens after.
    out = result.stdout + result.stderr
    assert "YOLO mode enabled" in out, \
        f"expected YOLO banner in output; got:\n{out[:1000]}"
    # The actual docker run will likely fail (no daemon), but that's fine —
    # we're testing that the CLI accepts the flags and validates the invariants.
