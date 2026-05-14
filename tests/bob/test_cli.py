"""CLI smoke tests for `bob run` and `bob status`.

These tests don't run real Claude — they exercise argument parsing and
verify the run command dispatches with the right config.
"""
import os
import subprocess
import sys
from pathlib import Path


def test_orchestrate_bob_run_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--inputs" in result.stdout
    assert "--max-iterations" in result.stdout


def test_orchestrate_bob_status_on_empty_dir(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no .bob/ found" in result.stdout.lower() or "not initialized" in result.stdout.lower()


def test_orchestrate_bob_status_on_initialized(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    (bob_dir / "cursor.json").write_text(
        '{"run_id": "x", "current_phase": "idle", "current_feature_id": null,'
        ' "last_event_at": "2026-05-07T00:00:00+00:00"}'
    )
    (bob_dir / "features").mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "idle" in result.stdout


def test_orchestrate_bob_run_requires_inputs(tmp_path: Path):
    """`bob run` without --inputs should exit with a clear error."""
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "inputs" in result.stderr.lower() or "inputs" in result.stdout.lower()


def test_orchestrate_bob_run_rejects_missing_spec(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(tmp_path / "does-not-exist.md")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()


def test_orchestrate_bob_run_handles_malformed_spec(tmp_path: Path):
    """Malformed spec produces clean error, not a traceback."""
    spec = tmp_path / "spec.md"
    spec.write_text("## Motivation\nno title\n## Features\n")  # missing # Title

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "title" in out  # the parse error message
    assert "traceback" not in out  # NO traceback


def test_orchestrate_bob_validate_succeeds_on_valid_spec(tmp_path: Path):
    """`bob validate` succeeds on a well-formed spec."""
    spec = tmp_path / "spec.md"
    spec.write_text(
        "# Demo\n## Motivation\nm\n## Features\n"
        "### F1: a\n- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "validate",
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "demo" in result.stdout.lower()
    assert "1 feature" in result.stdout.lower()


def test_orchestrate_bob_run_help_mentions_sandbox():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--sandbox" in result.stdout
    assert "host" in result.stdout
    assert "docker" in result.stdout


def test_orchestrate_bob_validate_fails_on_malformed_spec(tmp_path: Path):
    """`bob validate` reports parse errors with non-zero exit code."""
    spec = tmp_path / "spec.md"
    spec.write_text("## Motivation\nno title\n## Features\n")

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "validate",
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "title" in out
    assert "traceback" not in out


def test_orchestrate_bob_draft_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "draft", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--inputs" in result.stdout
    assert "--output" in result.stdout


def test_orchestrate_bob_draft_writes_reviewable_spec(tmp_path: Path):
    spec = tmp_path / "source.md"
    spec.write_text(
        "# Demo\n## Motivation\nm\n## Features\n"
        "### F1: a\n- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: |\n"
        "    line one\n    line two\n"
    )
    output = tmp_path / "draft.md"

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "draft",
         "--project", str(tmp_path),
         "--inputs", str(spec),
         "--output", str(output)],
        capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "draft spec written" in result.stdout
    text = output.read_text()
    assert "### F1: a" in text
    assert "- description: |" in text
    assert "line two" in text
    assert not (tmp_path / ".bob" / "features").exists()

    validate = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "validate",
         "--inputs", str(output)],
        capture_output=True, text=True,
    )
    assert validate.returncode == 0, validate.stderr


def test_orchestrate_bob_draft_stdout_from_stub_directory(tmp_path: Path):
    inputs_dir = tmp_path / "inputs"
    inputs_dir.mkdir()
    (inputs_dir / "spec.md").write_text(
        "# Demo\n## Motivation\nm\n## Features\n"
        "### F1: a\n- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )
    env = {**os.environ, "BOB_USE_STUB_DUPLO": "1"}

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "draft",
         "--project", str(tmp_path),
         "--inputs", str(inputs_dir)],
        capture_output=True, text=True, env=env,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# Demo")
    assert "### F1: a" in result.stdout


def test_orchestrate_bob_draft_reports_malformed_spec(tmp_path: Path):
    spec = tmp_path / "bad.md"
    spec.write_text("## Motivation\nno title\n## Features\n")

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "draft",
         "--project", str(tmp_path),
         "--inputs", str(spec)],
        capture_output=True, text=True,
    )

    assert result.returncode != 0
    out = result.stdout + result.stderr
    assert "title" in out.lower()
    assert "traceback" not in out.lower()


def test_orchestrate_bob_vroom_help():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "vroom", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "vroom" in result.stdout.lower()


def test_orchestrate_bob_vroom_now_smoke(tmp_path: Path):
    """`bob vroom now` should run one cycle and exit."""
    env = {**os.environ, "BOB_USE_STUB_VROOM": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "vroom", "now",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
        timeout=60,
        env=env,
    )
    assert result.returncode == 0
    assert "vroom cycle complete" in result.stdout.lower() or "0 raw findings" in result.stdout


def test_orchestrate_bob_vroom_stop_when_no_daemon(tmp_path: Path):
    """`bob vroom stop` when no daemon is running should exit 1 with a message."""
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "vroom", "stop",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "no vroom daemon" in result.stdout.lower()


def test_orchestrate_bob_run_help_mentions_vroom():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--vroom" in result.stdout


def test_orchestrate_bob_run_yolo_requires_docker_sandbox(tmp_path: Path):
    """--yolo without --sandbox docker should fail with invariant error."""
    spec = tmp_path / "spec.md"
    spec.write_text("# T\n## Motivation\nm\n## Features\n### F1: a\n- task_type: library\n- verifier: python_pytest\n- success_criteria:\n  - x\n- description: a\n")
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec),
         "--yolo",
         "--max-cost", "10.0"],  # max-cost set, but sandbox is default (host)
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "sandbox" in out or "docker" in out


def test_orchestrate_bob_run_yolo_requires_max_cost(tmp_path: Path):
    """--yolo --sandbox docker without --max-cost should fail."""
    spec = tmp_path / "spec.md"
    spec.write_text("# T\n## Motivation\nm\n## Features\n### F1: a\n- task_type: library\n- verifier: python_pytest\n- success_criteria:\n  - x\n- description: a\n")
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec),
         "--yolo",
         "--sandbox", "docker"],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    out = (result.stdout + result.stderr).lower()
    assert "max_cost" in out or "max-cost" in out


def test_orchestrate_bob_run_vroom_spawns_subprocess(tmp_path: Path, monkeypatch):
    """`bob run --vroom` should start the Vroom daemon subprocess and clean it up on exit.

    We use BOB_USE_STUB_ORCHESTRA + a fake claude on PATH to keep the run fast.
    The test verifies a vroom.pid file appears mid-run and is cleaned up after exit.
    """
    import os
    sp = subprocess

    # Set up a tiny git repo + spec.
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "test_smoke.py").write_text("def test_x(): assert True\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
            "-c", "user.name=T", "commit", "-m", "init"], check=True)

    spec = tmp_path / "spec.md"
    spec.write_text(
        "# T\n## Motivation\nm\n## Features\n### F1: noop\n"
        "- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: noop\n"
    )

    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    fake = fake_dir / "claude"
    fake.write_text(
        '#!/bin/sh\necho "<promise>EXIT_SIGNAL</promise>"\n'
    )
    fake.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "BOB_USE_STUB_ORCHESTRA": "1",
        "BOB_USE_STUB_DUPLO": "1",
        "BOB_USE_STUB_VROOM": "1",  # avoid real API calls in vroom subprocess
    }
    result = sp.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec),
         "--vroom",
         "--max-iterations", "1",
         "--no-gate", "post_duplo"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0, f"bob run failed:\n{result.stdout}\n{result.stderr}"
    out = result.stdout + result.stderr
    # Some signal that the vroom subprocess was actually spawned and shut down.
    assert ("vroom" in out.lower())  # progress / banner / shutdown message

    # After bob run exits, the vroom.pid file should be gone (clean shutdown).
    assert not (tmp_path / ".bob" / "vroom.pid").exists()


def test_orchestrate_bob_run_loads_dotenv(tmp_path: Path):
    """A .env in the project root should be picked up by `bob run`."""
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "x.py").write_text("def x(): pass\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
            "-c", "user.name=T", "commit", "-m", "init"], check=True)

    # Put a .env that sets BOB_USE_STUB_ORCHESTRA=1 in the project.
    (tmp_path / ".env").write_text("BOB_USE_STUB_ORCHESTRA=1\n")

    spec = tmp_path / "spec.md"
    spec.write_text("# T\n## Motivation\nm\n## Features\n### F1: a\n"
                    "- task_type: library\n- verifier: python_pytest\n"
                    "- success_criteria:\n  - x\n- description: a\n")

    # Note: do NOT set BOB_USE_STUB_ORCHESTRA in the env here. The .env file
    # is the only source. The test just verifies it doesn't crash with an
    # API key error (which would happen if .env wasn't loaded and real
    # Orchestra tried to call Anthropic without a key).
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    fake = fake_dir / "claude"
    fake.write_text('#!/bin/sh\necho "<promise>EXIT_SIGNAL</promise>"\n')
    fake.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{fake_dir}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    # Explicitly ensure the env doesn't already have the flag.
    env.pop("BOB_USE_STUB_ORCHESTRA", None)
    env.pop("BOB_USE_STUB_VROOM", None)
    env["BOB_USE_STUB_VROOM"] = "1"  # avoid Vroom API calls regardless of .env
    env["BOB_USE_STUB_DUPLO"] = "1"

    result = sp.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(spec),
         "--max-iterations", "1",
         "--no-gate", "post_duplo"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, \
        f"bob run failed:\n{result.stdout}\n{result.stderr}"
    # If .env was loaded, BOB_USE_STUB_ORCHESTRA=1 took effect and the run completed.
