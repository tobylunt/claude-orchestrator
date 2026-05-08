"""Tests for DockerExecutor (sandbox tier 2).

Real Docker execution gated behind BOB_DOCKER_TESTS=1 env var so CI
without a Docker daemon doesn't fail. The default tests use a fake
subprocess.run to verify the docker CLI is invoked with the right args.
"""
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_orchestrator.bob.sandbox.docker import DockerExecutor


def test_docker_executor_invokes_docker_run(tmp_path: Path):
    """DockerExecutor.run should call `docker run` with the right args."""
    captured = []
    real_run = subprocess.run

    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="ok\n", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim")
        executor.run(
            ["echo", "hello"],
            cwd=tmp_path,
            env=None,
            timeout=30,
        )

    assert len(captured) == 1
    args = captured[0]
    assert args[0] == "docker"
    assert "run" in args
    assert "--rm" in args
    # Should mount cwd at /workspace
    assert any("--volume" == a or "-v" == a for a in args)
    # Should specify the image
    assert "python:3.10-slim" in args
    # Should pass the actual command after image
    image_idx = args.index("python:3.10-slim")
    assert args[image_idx + 1:] == ["echo", "hello"] or "echo" in args[image_idx + 1:]


def test_docker_executor_passes_env_vars(tmp_path: Path):
    """Env vars should be passed via -e flags."""
    captured = []
    real_run = subprocess.run

    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim")
        executor.run(
            ["env"],
            cwd=tmp_path,
            env={"FOO": "bar", "BAZ": "qux"},
            timeout=30,
        )

    args = captured[0]
    # Each env var should be prefixed by -e
    assert "-e" in args or "--env" in args
    # FOO=bar and BAZ=qux should appear
    assert any("FOO=bar" in a for a in args)
    assert any("BAZ=qux" in a for a in args)


def test_docker_executor_applies_resource_caps(tmp_path: Path):
    """Default resource caps should be applied."""
    captured = []

    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim")
        executor.run(["true"], cwd=tmp_path, env=None, timeout=30)

    args = captured[0]
    # cpus + memory caps present
    cpu_arg = next((a for a in args if a.startswith("--cpus")), None)
    mem_arg = next((a for a in args if a.startswith("--memory")), None)
    assert cpu_arg is not None or "--cpus" in args
    assert mem_arg is not None or "--memory" in args


@pytest.mark.skipif(
    os.environ.get("BOB_DOCKER_TESTS", "0") != "1",
    reason="Real Docker test (set BOB_DOCKER_TESTS=1 to enable)",
)
def test_docker_executor_real_smoke(tmp_path: Path):
    """Smoke test: actually run hello-world in Docker."""
    executor = DockerExecutor(image="hello-world")
    result = executor.run(["hello-world"], cwd=tmp_path, env=None, timeout=60)
    # hello-world exits 0 and prints a banner.
    assert result.returncode == 0
    assert "hello" in result.stdout.lower() or "hello" in result.stderr.lower()


def test_docker_executor_uses_bob_dockerfile_when_present(tmp_path: Path):
    """If <project>/bob.dockerfile exists, DockerExecutor should build from it."""
    dockerfile = tmp_path / "bob.dockerfile"
    dockerfile.write_text("FROM python:3.10-slim\nRUN echo hi\n")

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        # Simulate `docker build` returning the new image hash, then `docker run`.
        return CompletedProcess(args, 0, stdout="sha256:abc123\n", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim", dockerfile=dockerfile)
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    # First call should be `docker build`, second should be `docker run` with the built image.
    assert len(captured) >= 2
    build_call = captured[0]
    assert "docker" in build_call and "build" in build_call
    # Path to dockerfile should be passed
    dockerfile_args = [str(a) for a in build_call]
    assert "-f" in dockerfile_args or "--file" in dockerfile_args
    assert any(str(dockerfile) in a for a in dockerfile_args)


def test_docker_executor_passes_network_arg_when_configured(tmp_path: Path):
    """If network is configured, --network flag should be passed."""
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim", network="bob-allowlist")
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    args = captured[0]
    assert "--network" in args
    idx = args.index("--network")
    assert args[idx + 1] == "bob-allowlist"


def test_docker_executor_adds_default_allowlist_hosts(tmp_path: Path):
    """When no custom network is provided but allowlist=True, Docker uses --add-host
    entries for the default allowlist (Anthropic, OpenAI, GitHub, npm, PyPI).
    """
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(
            image="python:3.10-slim",
            apply_default_allowlist=True,
        )
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    args = captured[0]
    args_str = " ".join(args)
    # Default allowlist should include Anthropic and GitHub at minimum.
    # We don't pin exact IPs (they change); we test the flag count is >= 2.
    add_host_count = sum(1 for a in args if a == "--add-host")
    assert add_host_count >= 2, \
        f"expected >=2 --add-host entries; got args: {args}"


def test_docker_executor_default_no_allowlist(tmp_path: Path):
    """Default behavior: no allowlist applied, no --add-host entries."""
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim")
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    args = captured[0]
    assert "--add-host" not in args
