"""Tests for YOLO mode config + invariant enforcement."""
import pytest

from claude_orchestrator.bob.yolo import (
    YoloConfig,
    YoloInvariantError,
)


def test_yolo_config_default_disabled():
    cfg = YoloConfig()
    assert cfg.enabled is False


def test_yolo_config_enabled_requires_max_cost():
    """Enabled YOLO must have max_cost set."""
    with pytest.raises(YoloInvariantError, match="max_cost"):
        YoloConfig(enabled=True, sandbox_tier="docker", max_cost=None)


def test_yolo_config_enabled_requires_docker_sandbox():
    """Enabled YOLO must run in tier 2 sandbox (docker), not tier 1 (host)."""
    with pytest.raises(YoloInvariantError, match="sandbox"):
        YoloConfig(enabled=True, sandbox_tier="host", max_cost=10.0)


def test_yolo_config_enabled_with_valid_invariants():
    cfg = YoloConfig(enabled=True, sandbox_tier="docker", max_cost=10.0)
    assert cfg.enabled is True
    assert cfg.sandbox_tier == "docker"
    assert cfg.max_cost == 10.0
    assert cfg.max_inconclusive == 3  # default
    assert cfg.vroom_severity == "high"  # default


def test_yolo_config_disabled_skips_invariants():
    """When disabled, invariants don't apply."""
    cfg = YoloConfig(enabled=False, sandbox_tier="host", max_cost=None)
    assert cfg.enabled is False  # OK


def test_yolo_config_accepts_devcontainer_sandbox():
    cfg = YoloConfig(enabled=True, sandbox_tier="devcontainer", max_cost=10.0)
    assert cfg.sandbox_tier == "devcontainer"


def test_yolo_config_overrides_via_env(monkeypatch):
    """YoloConfig.from_env reads BOB_YOLO_* env vars."""
    monkeypatch.setenv("BOB_YOLO_MAX_INCONCLUSIVE", "5")
    monkeypatch.setenv("BOB_YOLO_VROOM_SEVERITY", "medium")
    cfg = YoloConfig.from_env(enabled=False)  # disabled but env still applies
    assert cfg.max_inconclusive == 5
    assert cfg.vroom_severity == "medium"


def test_vroom_yolo_from_env_preserves_parent_bounds(monkeypatch):
    """The vroom subprocess must consume the YOLO values its parent produced."""
    from claude_orchestrator.bob.vroom_config import yolo_from_subprocess_env

    monkeypatch.setenv("BOB_VROOM_YOLO_ENABLED", "1")
    monkeypatch.setenv("BOB_VROOM_YOLO_SEVERITY", "critical")
    monkeypatch.setenv("BOB_VROOM_YOLO_MAX_COST", "12.5")
    monkeypatch.setenv("BOB_YOLO_MAX_INCONCLUSIVE", "7")

    cfg = yolo_from_subprocess_env(sandbox_tier="devcontainer")
    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.sandbox_tier == "devcontainer"
    assert cfg.max_cost == 12.5
    assert cfg.max_inconclusive == 7
    assert cfg.vroom_severity == "critical"
