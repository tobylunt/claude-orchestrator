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


def test_docker_executor_emits_explicit_add_hosts(tmp_path: Path):
    """Users can pin DNS resolution via add_hosts={hostname: ip}. The previous
    'default allowlist' that wrote 0.0.0.0 for every host was removed (it made
    the named hosts UNREACHABLE rather than allowlisted — opposite of intent)."""
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(
            image="python:3.10-slim",
            add_hosts={"api.anthropic.com": "1.2.3.4", "api.openai.com": "5.6.7.8"},
        )
        executor.run(["echo", "hi"], cwd=tmp_path, env=None, timeout=30)

    args = captured[0]
    add_host_count = sum(1 for a in args if a == "--add-host")
    assert add_host_count == 2
    args_str = " ".join(args)
    assert "api.anthropic.com:1.2.3.4" in args_str
    assert "api.openai.com:5.6.7.8" in args_str


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


def test_docker_executor_forwards_host_env_when_env_is_none(tmp_path: Path, monkeypatch):
    """env=None must NOT mean 'no env in container' — that would strip API keys.
    Forward a whitelist from os.environ so the inner subprocess can authenticate."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthro")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("BOB_CUSTOM", "custom-value")  # BOB_* prefix should propagate

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        DockerExecutor(image="python:3.10-slim").run(
            ["echo", "hi"], cwd=tmp_path, env=None, timeout=30,
        )

    args = " ".join(captured[0])
    assert "ANTHROPIC_API_KEY=sk-test-anthro" in args
    assert "OPENAI_API_KEY=sk-test-openai" in args
    assert "BOB_CUSTOM=custom-value" in args


def test_docker_executor_explicit_env_overrides_forwarding(tmp_path: Path, monkeypatch):
    """When the caller passes an explicit env dict, use that — don't merge."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-leak")

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        DockerExecutor(image="python:3.10-slim").run(
            ["echo", "hi"], cwd=tmp_path,
            env={"FOO": "bar"}, timeout=30,
        )

    args = " ".join(captured[0])
    assert "FOO=bar" in args
    assert "should-not-leak" not in args


def test_docker_executor_add_volume_and_translate_path(tmp_path: Path):
    """add_volume registers a mount; translate_path rewrites host paths into
    container paths so the McLoop prompt can reference files the container
    can actually see."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    executor = DockerExecutor(image="python:3.10-slim")
    executor.add_volume(bob_dir, "/bob-state")

    assert executor.translate_path(bob_dir / "spec.md") == "/bob-state/spec.md"
    assert executor.translate_path(bob_dir / "features" / "001-x" / "activity.md") == "/bob-state/features/001-x/activity.md"
    # Path outside the mount returns unchanged.
    other = tmp_path / "elsewhere.txt"
    assert executor.translate_path(other) == str(other.resolve())


def test_docker_executor_mounts_registered_volumes(tmp_path: Path):
    """add_volume mounts must show up as -v flags in `docker run`."""
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        executor = DockerExecutor(image="python:3.10-slim")
        executor.add_volume(bob_dir, "/bob-state")
        executor.run(["echo", "hi"], cwd=tmp_path, env={}, timeout=30)

    args = " ".join(captured[0])
    assert f"{bob_dir.resolve()}:/bob-state" in args


def test_docker_executor_raises_on_build_failure(tmp_path: Path):
    """A failed docker build must not silently fall back to the default image;
    otherwise the runner uses a stripped image with no claude/codex CLI and
    burns max_iterations producing nothing."""
    dockerfile = tmp_path / "bob.dockerfile"
    dockerfile.write_text("FROM nonexistent:bad\n")

    def fail(args, **kwargs):
        from subprocess import CompletedProcess
        return CompletedProcess(args, 1, stdout="", stderr="manifest unknown")

    with patch("subprocess.run", side_effect=fail):
        executor = DockerExecutor(image="python:3.10-slim", dockerfile=dockerfile)
        with pytest.raises(RuntimeError, match="docker build failed"):
            executor.run(["echo", "hi"], cwd=tmp_path, env={}, timeout=30)


def test_docker_executor_does_not_forward_host_HOME_or_PATH(tmp_path: Path, monkeypatch):
    """The host's HOME (e.g., /Users/tobiaslunt on macOS) does not exist
    inside the container; forwarding it makes `claude` hang trying to write
    ~/.claude/... Same for PATH — the host PATH references binaries that
    aren't in the container's filesystem. Container's image-provided HOME
    (default '/tmp' here) and PATH should win.
    """
    monkeypatch.setenv("HOME", "/Users/me")
    monkeypatch.setenv("PATH", "/host/bin:/usr/bin")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        DockerExecutor(image="python:3.10-slim").run(
            ["echo", "hi"], cwd=tmp_path, env=None, timeout=30,
        )

    args = captured[0]
    # No -e HOME=/Users/me  and  no -e PATH=/host/bin/...
    assert not any(a == "HOME=/Users/me" for a in args)
    assert not any(a.startswith("PATH=/host/bin") for a in args)
    # But HOME=/tmp must be set (writable container fallback)
    assert any(a == "HOME=/tmp" for a in args), \
        f"expected -e HOME=/tmp in docker args; got {args}"


def test_docker_executor_caller_HOME_wins_over_default(tmp_path: Path):
    """If the caller passes env={'HOME': '/custom'}, that wins over /tmp."""
    captured = []
    def capture(args, **kwargs):
        captured.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=capture):
        DockerExecutor(image="python:3.10-slim").run(
            ["echo", "hi"], cwd=tmp_path,
            env={"HOME": "/custom"}, timeout=30,
        )

    args = captured[0]
    assert any(a == "HOME=/custom" for a in args)
    assert not any(a == "HOME=/tmp" for a in args)
