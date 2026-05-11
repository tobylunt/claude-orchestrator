"""Tests for resolved Vroom config."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from claude_orchestrator.bob.vroom_config import (
    VroomConfig,
    yolo_from_subprocess_env,
)


def _daemon_args(
    tmp_path: Path,
    *,
    sandbox: str | None = None,
    interval: int = 1800,
    watch_main_ref: bool = False,
) -> argparse.Namespace:
    return argparse.Namespace(
        project=str(tmp_path),
        sandbox=sandbox,
        interval=interval,
        watch_main_ref=watch_main_ref,
    )


def _now_args(tmp_path: Path) -> argparse.Namespace:
    return argparse.Namespace(project=str(tmp_path))


def test_yolo_from_subprocess_env_returns_none_when_disabled():
    assert yolo_from_subprocess_env(sandbox_tier="docker", env={}) is None


def test_yolo_from_subprocess_env_preserves_parent_bounds():
    cfg = yolo_from_subprocess_env(
        sandbox_tier="devcontainer",
        env={
            "BOB_VROOM_YOLO_ENABLED": "1",
            "BOB_VROOM_YOLO_SEVERITY": "critical",
            "BOB_VROOM_YOLO_MAX_COST": "12.5",
            "BOB_YOLO_MAX_INCONCLUSIVE": "7",
            "BOB_YOLO_NOTIFY": "slack:#bob-alerts",
        },
    )
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.sandbox_tier == "devcontainer"
    assert cfg.max_cost == 12.5
    assert cfg.max_inconclusive == 7
    assert cfg.vroom_severity == "critical"
    assert cfg.notify_channel == "slack:#bob-alerts"


def test_yolo_from_subprocess_env_defaults_when_partial():
    cfg = yolo_from_subprocess_env(
        sandbox_tier="docker",
        env={
            "BOB_VROOM_YOLO_ENABLED": "1",
            "BOB_VROOM_YOLO_MAX_COST": "5.0",
        },
    )
    assert cfg is not None
    assert cfg.max_inconclusive == 3
    assert cfg.vroom_severity == "high"


def test_from_daemon_args_uses_host_default(tmp_path: Path):
    cfg = VroomConfig.from_daemon_args(_daemon_args(tmp_path), env={})
    assert cfg.sandbox_tier == "host"


def test_from_daemon_args_cli_flag_wins_over_env(tmp_path: Path):
    cfg = VroomConfig.from_daemon_args(
        _daemon_args(tmp_path, sandbox="docker"),
        env={"BOB_SANDBOX_TIER": "devcontainer"},
    )
    assert cfg.sandbox_tier == "docker"


def test_from_daemon_args_rejects_unknown_sandbox(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown sandbox tier"):
        VroomConfig.from_daemon_args(
            _daemon_args(tmp_path),
            env={"BOB_SANDBOX_TIER": "bogus"},
        )


def test_from_daemon_args_carries_interval_watch_and_stub(tmp_path: Path):
    cfg = VroomConfig.from_daemon_args(
        _daemon_args(tmp_path, interval=42, watch_main_ref=True),
        env={"BOB_USE_STUB_VROOM": "1"},
    )
    assert cfg.project_root == tmp_path.resolve()
    assert cfg.timer_interval_s == 42
    assert cfg.watch_main_ref is True
    assert cfg.use_stub is True


def test_from_daemon_args_yolo_reconstructed_from_env(tmp_path: Path):
    cfg = VroomConfig.from_daemon_args(
        _daemon_args(tmp_path, sandbox="docker"),
        env={
            "BOB_VROOM_YOLO_ENABLED": "1",
            "BOB_VROOM_YOLO_MAX_COST": "20.0",
            "BOB_VROOM_YOLO_SEVERITY": "medium",
            "BOB_YOLO_MAX_INCONCLUSIVE": "5",
        },
    )
    assert cfg.yolo is not None
    assert cfg.yolo.enabled is True
    assert cfg.yolo.sandbox_tier == "docker"
    assert cfg.yolo.max_cost == 20.0
    assert cfg.yolo.vroom_severity == "medium"
    assert cfg.yolo.max_inconclusive == 5


def test_from_now_args_defaults_sandbox_to_docker(tmp_path: Path):
    cfg = VroomConfig.from_now_args(_now_args(tmp_path), env={})
    assert cfg.sandbox_tier == "docker"


def test_from_now_args_respects_env_sandbox(tmp_path: Path):
    cfg = VroomConfig.from_now_args(
        _now_args(tmp_path),
        env={"BOB_SANDBOX_TIER": "host"},
    )
    assert cfg.sandbox_tier == "host"


def test_from_now_args_yolo_reconstructed(tmp_path: Path):
    cfg = VroomConfig.from_now_args(
        _now_args(tmp_path),
        env={
            "BOB_VROOM_YOLO_ENABLED": "1",
            "BOB_VROOM_YOLO_MAX_COST": "3.0",
            "BOB_SANDBOX_TIER": "devcontainer",
        },
    )
    assert cfg.yolo is not None
    assert cfg.yolo.sandbox_tier == "devcontainer"
    assert cfg.yolo.max_cost == 3.0


def test_vroom_config_is_frozen(tmp_path: Path):
    cfg = VroomConfig.from_now_args(_now_args(tmp_path), env={})
    with pytest.raises(Exception):
        cfg.sandbox_tier = "host"  # type: ignore[misc]
