"""Tests for configuration loading."""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.config import OrchestratorConfig, load_config


class TestDefaults:
    def test_default_values(self):
        config = OrchestratorConfig()
        assert config.model == "sonnet"
        assert config.planning_model == "opus"
        assert config.max_retries == 3
        assert config.permission_mode == "acceptEdits"
        assert config.features_file == Path("features.json")
        assert config.auto_commit is True
        assert "Read" in config.allowed_tools
        assert "Bash" in config.allowed_tools
        assert "AskUserQuestion" in config.allowed_tools


class TestLoadConfig:
    def test_loads_from_cli_args(self, tmp_path: Path):
        config = load_config({
            "project": str(tmp_path),
            "model": "opus",
            "max_retries": 5,
        })
        assert config.project_dir == tmp_path
        assert config.model == "opus"
        assert config.max_retries == 5

    def test_ignores_none_cli_args(self, tmp_path: Path):
        config = load_config({
            "project": str(tmp_path),
            "model": None,
        })
        assert config.model == "sonnet"  # default

    def test_loads_toml(self, tmp_path: Path):
        toml_content = """\
features_file = "my-features.json"
model = "haiku"
commit_prefix = "feat: "
max_retries = 10
"""
        (tmp_path / "orchestrator.toml").write_text(toml_content)

        config = load_config({"project": str(tmp_path)})
        assert config.features_file == Path("my-features.json")
        assert config.model == "haiku"
        assert config.commit_prefix == "feat: "
        assert config.max_retries == 10

    def test_cli_overrides_toml(self, tmp_path: Path):
        toml_content = 'model = "haiku"\n'
        (tmp_path / "orchestrator.toml").write_text(toml_content)

        config = load_config({
            "project": str(tmp_path),
            "model": "opus",
        })
        assert config.model == "opus"
