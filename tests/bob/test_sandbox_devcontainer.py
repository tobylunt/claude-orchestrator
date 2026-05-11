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


def test_devcontainer_executor_forwards_safe_env_when_env_none(
    tmp_path: Path, monkeypatch
):
    """env=None should not mean zero API/config env in devcontainer mode."""
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text('{"image": "python:3.10-slim"}')
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("BOB_SANDBOX_TIER", "devcontainer")
    monkeypatch.setenv("HOME", "/host/home")
    monkeypatch.setenv("PATH", "/host/bin")

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        executor.run(["env"], cwd=tmp_path, env=None, timeout=30)

    exec_args = next(args for args in captured if "exec" in args)
    joined = " ".join(exec_args)
    assert "ANTHROPIC_API_KEY=sk-ant-test" in joined
    assert "BOB_SANDBOX_TIER=devcontainer" in joined
    assert "HOME=/host/home" not in joined
    assert "PATH=/host/bin" not in joined


def test_devcontainer_executor_runs_command_from_translated_cwd(tmp_path: Path):
    """The McLoop worktree cwd must be translated into the container command."""
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text(
        '{"image": "python:3.10-slim", "workspaceFolder": "/repo"}'
    )
    worktree = tmp_path / ".bob" / "worktrees" / "001-feat"
    worktree.mkdir(parents=True)

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        executor.run(["pwd"], cwd=worktree, env={}, timeout=30)

    exec_args = next(args for args in captured if "exec" in args)
    assert 'cd "$1" && shift && exec "$@"' in exec_args
    assert "/repo/.bob/worktrees/001-feat" in exec_args


def test_devcontainer_executor_raises_when_up_fails(tmp_path: Path):
    """A failed `devcontainer up` should halt before a misleading exec call."""
    devc_dir = tmp_path / ".devcontainer"
    devc_dir.mkdir()
    (devc_dir / "devcontainer.json").write_text('{"image": "python:3.10-slim"}')

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 1, stdout="", stderr="boom")

    with patch("subprocess.run", side_effect=capture):
        executor = DevcontainerExecutor(devcontainer_dir=tmp_path)
        with pytest.raises(RuntimeError, match="devcontainer up failed"):
            executor.run(["echo", "hi"], cwd=tmp_path, env={}, timeout=30)

    assert len(captured) == 1
    assert "up" in captured[0]


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
