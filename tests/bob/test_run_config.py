"""Tests for resolved `bob run` config."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from claude_orchestrator.bob.run_config import RunConfig, resolve_sandbox_tier
from claude_orchestrator.bob.yolo import YoloInvariantError


def _make_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    defaults = dict(
        project=str(tmp_path),
        inputs=str(tmp_path / "spec.md"),
        max_iterations=30,
        max_cost=None,
        no_gate=[],
        sandbox=None,
        vroom=False,
        yolo=False,
        otel_endpoint=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_resolve_sandbox_tier_default_is_host():
    assert resolve_sandbox_tier(cli_value=None, env={}) == "host"


def test_resolve_sandbox_tier_env_var_used_when_no_cli():
    assert (
        resolve_sandbox_tier(cli_value=None, env={"BOB_SANDBOX_TIER": "docker"})
        == "docker"
    )


def test_resolve_sandbox_tier_cli_overrides_env():
    assert (
        resolve_sandbox_tier(
            cli_value="devcontainer",
            env={"BOB_SANDBOX_TIER": "docker"},
        )
        == "devcontainer"
    )


def test_resolve_sandbox_tier_rejects_unknown():
    with pytest.raises(ValueError, match="unknown sandbox tier"):
        resolve_sandbox_tier(cli_value="bogus", env={})


def test_run_config_from_args_resolves_paths(tmp_path: Path):
    args = _make_args(tmp_path)
    cfg = RunConfig.from_args(args, env={})
    assert cfg.project_root == tmp_path.resolve()
    assert cfg.spec_path == (tmp_path / "spec.md").resolve()


def test_run_config_from_args_requires_inputs(tmp_path: Path):
    with pytest.raises(ValueError, match="inputs"):
        RunConfig.from_args(_make_args(tmp_path, inputs=None), env={})


def test_run_config_from_args_sandbox_from_env(tmp_path: Path):
    cfg = RunConfig.from_args(
        _make_args(tmp_path),
        env={"BOB_SANDBOX_TIER": "docker"},
    )
    assert cfg.sandbox_tier == "docker"


def test_run_config_from_args_sandbox_cli_overrides_env(tmp_path: Path):
    cfg = RunConfig.from_args(
        _make_args(tmp_path, sandbox="devcontainer"),
        env={"BOB_SANDBOX_TIER": "docker"},
    )
    assert cfg.sandbox_tier == "devcontainer"


def test_run_config_from_args_disabled_gates_are_frozen(tmp_path: Path):
    cfg = RunConfig.from_args(
        _make_args(tmp_path, no_gate=["post_duplo", "orchestra_disagree"]),
        env={},
    )
    assert cfg.disabled_gates == frozenset({"post_duplo", "orchestra_disagree"})


def test_run_config_from_args_yolo_carries_env_overrides(tmp_path: Path):
    cfg = RunConfig.from_args(
        _make_args(tmp_path, yolo=True, sandbox="docker", max_cost=10.0),
        env={
            "BOB_YOLO_MAX_INCONCLUSIVE": "7",
            "BOB_YOLO_VROOM_SEVERITY": "critical",
            "BOB_YOLO_NOTIFY": "slack:#bob",
        },
    )
    assert cfg.yolo.enabled is True
    assert cfg.yolo.sandbox_tier == "docker"
    assert cfg.yolo.max_cost == 10.0
    assert cfg.yolo.max_inconclusive == 7
    assert cfg.yolo.vroom_severity == "critical"
    assert cfg.yolo.notify_channel == "slack:#bob"


def test_run_config_from_args_yolo_invariants_propagate(tmp_path: Path):
    with pytest.raises(YoloInvariantError):
        RunConfig.from_args(
            _make_args(tmp_path, yolo=True, max_cost=10.0),
            env={},
        )


def test_run_config_from_args_passes_through_operational_flags(tmp_path: Path):
    cfg = RunConfig.from_args(
        _make_args(
            tmp_path,
            max_iterations=42,
            vroom=True,
            otel_endpoint="http://localhost:6006/v1/traces",
        ),
        env={},
    )
    assert cfg.max_iterations == 42
    assert cfg.vroom is True
    assert cfg.otel_endpoint == "http://localhost:6006/v1/traces"


def test_run_config_is_frozen(tmp_path: Path):
    cfg = RunConfig.from_args(_make_args(tmp_path), env={})
    with pytest.raises(Exception):
        cfg.sandbox_tier = "docker"  # type: ignore[misc]
