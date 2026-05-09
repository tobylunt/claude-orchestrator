"""Tests for .env auto-loading."""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_orchestrator.bob.dotenv_loader import load_env_files


def test_load_project_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BOB_TEST_KEY", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("BOB_TEST_KEY=from_project\n")

    load_env_files(project_root=project, cwd=tmp_path)
    assert os.environ.get("BOB_TEST_KEY") == "from_project"


def test_load_cwd_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BOB_TEST_CWD_KEY", raising=False)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / ".env").write_text("BOB_TEST_CWD_KEY=from_cwd\n")

    load_env_files(project_root=None, cwd=cwd)
    assert os.environ.get("BOB_TEST_CWD_KEY") == "from_cwd"


def test_process_env_overrides_file(tmp_path: Path, monkeypatch):
    """Process env vars take priority over .env file values."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("BOB_TEST_PRIORITY=from_file\n")
    monkeypatch.setenv("BOB_TEST_PRIORITY", "from_process")

    load_env_files(project_root=project, cwd=tmp_path)
    assert os.environ["BOB_TEST_PRIORITY"] == "from_process"


def test_load_handles_missing_files(tmp_path: Path):
    """No .env files anywhere shouldn't raise."""
    load_env_files(project_root=tmp_path, cwd=tmp_path)


def test_load_supports_quoted_values(tmp_path: Path, monkeypatch):
    """python-dotenv handles quoted values; verify."""
    monkeypatch.delenv("BOB_TEST_QUOTED", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text('BOB_TEST_QUOTED="with spaces"\n')

    load_env_files(project_root=project, cwd=tmp_path)
    assert os.environ.get("BOB_TEST_QUOTED") == "with spaces"


def test_project_env_does_not_override_cwd_or_process(tmp_path: Path, monkeypatch):
    """If both project/.env and cwd/.env set the same key, process wins; otherwise project wins."""
    monkeypatch.delenv("BOB_TEST_BOTH", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (project / ".env").write_text("BOB_TEST_BOTH=project\n")
    (cwd / ".env").write_text("BOB_TEST_BOTH=cwd\n")

    load_env_files(project_root=project, cwd=cwd)
    # Project wins over cwd (project is more specific to the orchestration target).
    assert os.environ.get("BOB_TEST_BOTH") == "project"


def test_load_user_level_env(tmp_path: Path, monkeypatch):
    """A ~/.bob/.env (overridden via BOB_USER_ENV for tests) should be loaded."""
    monkeypatch.delenv("BOB_TEST_USER_KEY", raising=False)
    user_env = tmp_path / "user-env"
    user_env.write_text("BOB_TEST_USER_KEY=from_user\n")
    monkeypatch.setenv("BOB_USER_ENV", str(user_env))

    load_env_files(project_root=None, cwd=None)
    assert os.environ.get("BOB_TEST_USER_KEY") == "from_user"


def test_project_env_overrides_user_env(tmp_path: Path, monkeypatch):
    """Project .env should beat user-level .env when both set the same key."""
    monkeypatch.delenv("BOB_TEST_PRECEDENCE", raising=False)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("BOB_TEST_PRECEDENCE=from_project\n")

    user_env = tmp_path / "user-env"
    user_env.write_text("BOB_TEST_PRECEDENCE=from_user\n")
    monkeypatch.setenv("BOB_USER_ENV", str(user_env))

    load_env_files(project_root=project, cwd=None)
    assert os.environ.get("BOB_TEST_PRECEDENCE") == "from_project"


def test_user_env_supplies_keys_when_no_project_env(tmp_path: Path, monkeypatch):
    """If only the user-level .env is present, its keys still get loaded."""
    monkeypatch.delenv("BOB_TEST_API_KEY", raising=False)
    user_env = tmp_path / "user-env"
    user_env.write_text("BOB_TEST_API_KEY=sk-user-only\n")
    monkeypatch.setenv("BOB_USER_ENV", str(user_env))

    project = tmp_path / "project"
    project.mkdir()  # exists but no .env

    load_env_files(project_root=project, cwd=tmp_path / "cwd-that-does-not-exist")
    assert os.environ.get("BOB_TEST_API_KEY") == "sk-user-only"


def test_process_env_overrides_user_env(tmp_path: Path, monkeypatch):
    """Process env still wins, even over the user-level .env."""
    user_env = tmp_path / "user-env"
    user_env.write_text("BOB_TEST_PROCESS_WINS=from_user\n")
    monkeypatch.setenv("BOB_USER_ENV", str(user_env))
    monkeypatch.setenv("BOB_TEST_PROCESS_WINS", "from_process")

    load_env_files(project_root=None, cwd=None)
    assert os.environ["BOB_TEST_PROCESS_WINS"] == "from_process"
