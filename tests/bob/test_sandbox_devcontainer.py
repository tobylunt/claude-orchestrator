"""Tests for DevcontainerExecutor (sandbox tier 3)."""
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_orchestrator.bob.sandbox.devcontainer import DevcontainerExecutor


def test_devcontainer_executor_invokes_devcontainer_exec(tmp_path: Path):
    """The executor should run `devcontainer exec --workspace-folder <path> <cmd>`."""
    # Create a minimal .devcontainer/devcontainer.json so existence checks pass.
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text(
        '{"image": "python:3.10-slim", "name": "test"}'
    )

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="ok\n", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    # First call may be `devcontainer up` or `devcontainer exec`. The exec
    # call MUST appear at some point with the expected args.
    flat = [tuple(a) for a in captured]
    found_exec = any(
        "devcontainer" in a[0] and "exec" in a
        for a in flat
    )
    assert found_exec, f"expected devcontainer exec call in: {flat}"


def test_devcontainer_executor_errors_when_no_devcontainer_json(tmp_path: Path):
    """If no .devcontainer/devcontainer.json present and none generated, raise."""
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="devcontainer.json"):
            executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)


def test_devcontainer_executor_passes_env_vars(tmp_path: Path):
    """Env vars should be passed via devcontainer exec --remote-env."""
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text(
        '{"image": "python:3.10-slim"}'
    )

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        executor.run(
            ["env"], cwd=tmp_path, env={"FOO": "bar"}, timeout=30,
        )

    # At least one call should include FOO=bar
    found = False
    for args in captured:
        if any("FOO=bar" in str(a) for a in args):
            found = True
            break
    assert found, f"FOO=bar not found in any call: {captured}"


@pytest.mark.skipif(
    os.environ.get("BOB_DEVCONTAINER_TESTS", "0") != "1",
    reason="Real devcontainer test (set BOB_DEVCONTAINER_TESTS=1 to enable)",
)
def test_devcontainer_real_smoke(tmp_path: Path):
    """Smoke test: real devcontainer invocation."""
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text(
        '{"image": "python:3.10-slim"}'
    )
    executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
    result = executor.run(["echo", "hello"], cwd=tmp_path, env=None, timeout=300)
    assert result.returncode == 0
    assert "hello" in result.stdout
