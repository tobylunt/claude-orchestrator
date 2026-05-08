"""Tests for the sandbox executor protocol and HostExecutor."""
from pathlib import Path
import pytest

from claude_orchestrator.bob.sandbox.executor import SubprocessExecutor
from claude_orchestrator.bob.sandbox.host import HostExecutor


def test_host_executor_runs_command(tmp_path: Path):
    executor = HostExecutor()
    result = executor.run(["echo", "hello"], cwd=tmp_path, env=None, timeout=10)
    assert result.returncode == 0
    assert "hello" in result.stdout


def test_host_executor_captures_stderr(tmp_path: Path):
    executor = HostExecutor()
    result = executor.run(
        ["sh", "-c", "echo 'oops' 1>&2; exit 1"],
        cwd=tmp_path, env=None, timeout=10,
    )
    assert result.returncode == 1
    assert "oops" in result.stderr


def test_host_executor_respects_cwd(tmp_path: Path):
    executor = HostExecutor()
    result = executor.run(["pwd"], cwd=tmp_path, env=None, timeout=10)
    assert str(tmp_path) in result.stdout


def test_host_executor_respects_env(tmp_path: Path):
    executor = HostExecutor()
    result = executor.run(
        ["sh", "-c", "echo $BOB_TEST_VAR"],
        cwd=tmp_path,
        env={"BOB_TEST_VAR": "from_env", "PATH": "/usr/bin:/bin"},
        timeout=10,
    )
    assert "from_env" in result.stdout


def test_host_executor_satisfies_protocol():
    executor = HostExecutor()
    # Just check the protocol attributes exist
    assert hasattr(executor, "run")
