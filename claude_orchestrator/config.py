"""Configuration loading: defaults → orchestrator.toml → CLI flags."""

from __future__ import annotations

import sys

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


class OrchestratorConfig(BaseModel):
    """All orchestrator settings. Loaded from defaults, then orchestrator.toml, then CLI flags."""

    # Project paths
    project_dir: Path = Field(default_factory=lambda: Path.cwd())
    spec_file: Path | None = None
    features_file: Path = Path("features.json")
    progress_file: Path = Path("progress.txt")
    init_script: Path | None = None
    claude_md: Path | None = None

    # Execution
    model: str = "sonnet"
    planning_model: str = "opus"
    max_retries: int = 3
    retry_backoff_base: float = 2.0
    retry_backoff_max: float = 60.0
    max_turns_per_feature: int = 200
    max_budget_per_feature_usd: float | None = None
    stall_timeout_seconds: float = 300.0
    human_input_timeout_seconds: float = 120.0
    start_from_feature: int | None = None
    stop_after_feature: int | None = None
    dry_run: bool = False

    # Permission and security
    permission_mode: Literal["default", "acceptEdits", "bypassPermissions"] = "acceptEdits"
    allowed_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebFetch", "WebSearch", "AskUserQuestion", "Task",
    ])
    disallowed_tools: list[str] = Field(default_factory=list)

    # MCP servers
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Logging
    log_level: str = "INFO"
    log_dir: Path = Path(".orchestrator/logs")
    structured_log: bool = True

    # Git
    auto_commit: bool = True
    commit_prefix: str = ""


def load_config(cli_args: dict[str, Any]) -> OrchestratorConfig:
    """Load config from defaults → orchestrator.toml → CLI args."""
    project_dir = Path(cli_args.get("project", ".")).resolve()
    toml_path = project_dir / "orchestrator.toml"

    # Start with defaults
    config_data: dict[str, Any] = {"project_dir": project_dir}

    # Layer in TOML if present
    if toml_path.exists():
        with open(toml_path, "rb") as f:
            toml_data = tomllib.load(f)
        config_data.update(toml_data)

    # Layer in CLI overrides (only non-None values)
    for key, value in cli_args.items():
        if value is not None and key != "project":
            config_data[key] = value

    # Ensure project_dir is always set
    config_data["project_dir"] = project_dir

    return OrchestratorConfig(**config_data)
