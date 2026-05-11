"""Composition: assemble the Coordinator with real callables for `bob run`.

Kept separate from cli.py so M3 can extend (e.g., wire Vroom in parallel)
without touching argparse code.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from claude_orchestrator.bob.coordinator import Coordinator
from claude_orchestrator.bob.duplo.markdown_parser import parse_markdown_spec
from claude_orchestrator.bob.state_io import append_jsonl
from claude_orchestrator.bob.hitl.gates import GateRegistry, PostDuploGate
from claude_orchestrator.bob.mcloop.runner import McLoopResult, McLoopRunner
from claude_orchestrator.bob.orchestra.stub import OrchestraStub
from claude_orchestrator.bob.verifiers.protocol import Verifier
from claude_orchestrator.bob.verifiers.registry import VerifierRegistry
from claude_orchestrator.bob.yolo import YoloConfig
from claude_orchestrator.models import Feature, Verdict


class AutoApproveJudge:
    """M2a stub judge for OrchestraStub.

    M2 proper replaces this with AutoGen GroupChat + KS-stability over
    Claude/Codex/Opus. For M2a we auto-approve so `bob run` produces
    deliverable merges; the user inspects the McLoop output and rejects
    via the post-Duplo HITL gate or by manually reverting commits.
    """

    def judge_diff(self, feature: Feature, diff: str) -> dict[str, Any]:
        return {
            "decision": "approve",
            "confidence": 1.0,
            "reasoning": "M2a auto-approve stub (M2 proper wires real Orchestra debate)",
        }


def _build_orchestra():
    """Return an Orchestra instance based on environment configuration.

    Uses OrchestraStub when BOB_USE_STUB_ORCHESTRA=1 (for tests / offline
    mode). Otherwise builds a RealOrchestra with production debate agents.
    """
    if os.environ.get("BOB_USE_STUB_ORCHESTRA", "0") == "1":
        return OrchestraStub(judge=AutoApproveJudge())

    from claude_orchestrator.bob.orchestra.real import RealOrchestra
    from claude_orchestrator.bob.orchestra.agents import (
        AnthropicDebateAgent,
        OpenAIDebateAgent,
    )

    claude_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_CLAUDE_MODEL", "claude-sonnet-4-6"),
        system='You are a thoughtful implementer defending the diff. Reply JSON: {"content": "...", "decision": "approve|reject|abstain"}',
        role="claude",
    )
    codex_agent = OpenAIDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_CODEX_MODEL", "gpt-5.4"),
        system='You are an adversarial reviewer. Find bugs, edge cases, security issues. Reply JSON: {"content": "...", "decision": "approve|reject|abstain"}',
        role="codex",
    )
    judge_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_JUDGE_MODEL", "claude-opus-4-7"),
        system='You synthesize the debate. Reply JSON: {"content": "...", "decision": "approve|reject|abstain", "confidence": 0.0..1.0}',
        role="judge",
    )
    return RealOrchestra(
        claude_agent=claude_agent,
        codex_agent=codex_agent,
        judge_agent=judge_agent,
        max_rounds=int(os.environ.get("BOB_ORCHESTRA_MAX_ROUNDS", "5")),
    )


def build_verifier_registry() -> VerifierRegistry:
    """Register the M2 verifier roster."""
    from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
    from claude_orchestrator.bob.verifiers.lint_universal import LintUniversalVerifier
    from claude_orchestrator.bob.verifiers.data_analysis import DataAnalysisVerifier
    from claude_orchestrator.bob.verifiers.geospatial import GeospatialVerifier

    reg = VerifierRegistry()
    reg.register(PythonPytestVerifier())
    reg.register(LintUniversalVerifier())
    reg.register(DataAnalysisVerifier())
    reg.register(GeospatialVerifier())
    return reg


def _build_executor(tier: str, project_root: Path | None = None):
    """Construct the right SubprocessExecutor for the given sandbox tier."""
    if tier == "host":
        from claude_orchestrator.bob.sandbox.host import HostExecutor
        return HostExecutor()
    if tier == "docker":
        from claude_orchestrator.bob.sandbox.docker import DockerExecutor
        image = os.environ.get("BOB_DOCKER_IMAGE", "python:3.10-slim")
        cpus = float(os.environ.get("BOB_DOCKER_CPUS", "4"))
        memory = os.environ.get("BOB_DOCKER_MEMORY", "8g")
        network = os.environ.get("BOB_DOCKER_NETWORK")  # None means default
        # BOB_DOCKER_EXTRA_ARGS is a shell-style string (e.g. for extra -v mounts).
        # Previously ignored — the dockerfile example documents using it to
        # mount ~/.claude into the container, but _build_executor never read it.
        extra_args_raw = os.environ.get("BOB_DOCKER_EXTRA_ARGS", "")
        import shlex
        extra_args = shlex.split(extra_args_raw) if extra_args_raw else []

        # Auto-detect bob.dockerfile in project root.
        dockerfile = None
        if project_root is not None:
            candidate = project_root / "bob.dockerfile"
            if candidate.exists():
                dockerfile = candidate

        return DockerExecutor(
            image=image, cpus=cpus, memory=memory,
            network=network,
            dockerfile=dockerfile,
            extra_args=extra_args,
        )
    if tier == "devcontainer":
        from claude_orchestrator.bob.sandbox.devcontainer import DevcontainerExecutor
        if project_root is None:
            raise ValueError("devcontainer sandbox requires project_root")
        return DevcontainerExecutor(devcontainer_dir=project_root)
    raise ValueError(f"unknown sandbox tier: {tier!r} (must be host|docker|devcontainer)")


def build_coordinator(
    *,
    project_root: Path,
    spec_path: Path,
    max_iterations: int = 30,
    per_iteration_timeout_s: int = 600,
    disabled_gates: set[str] | None = None,
    claude_cmd: str = "claude",
    sandbox_tier: str = "host",  # "host" | "docker"
    yolo: YoloConfig | None = None,  # NEW
) -> Coordinator:
    """Assemble a Coordinator from a project root + markdown spec path.

    The returned Coordinator is ready to call .run(RunScope(includes_duplo=True)).
    """
    registry = build_verifier_registry()
    executor = _build_executor(sandbox_tier, project_root=project_root)
    runner = McLoopRunner(
        claude_cmd=claude_cmd,
        max_iterations=max_iterations,
        per_iteration_timeout_s=per_iteration_timeout_s,
        executor=executor,
        yolo=yolo,
    )
    orchestra_obj = _build_orchestra()

    def duplo_callable():
        if spec_path.is_dir():
            # Multimodal path (M2): use real Duplo with Anthropic vision.
            if os.environ.get("BOB_USE_STUB_DUPLO", "0") == "1":
                # Offline mode: parse the markdown spec inside the directory if present.
                md_in_dir = spec_path / "spec.md"
                if md_in_dir.exists():
                    spec = parse_markdown_spec(md_in_dir)
                else:
                    raise RuntimeError(
                        f"BOB_USE_STUB_DUPLO=1 but no {md_in_dir} found"
                    )
            else:
                from claude_orchestrator.bob.duplo.real import RealDuplo
                from claude_orchestrator.bob.duplo.multimodal import AnthropicMultimodalClient
                duplo = RealDuplo(multimodal=AnthropicMultimodalClient())
                spec = duplo.elicit_from_directory(spec_path)
        else:
            # Single-file markdown path (M2a behavior preserved).
            spec = parse_markdown_spec(spec_path)
        # Meta-rubric coverage check (spec §6.6): ask an LLM judge whether the
        # assigned verifier actually verifies each feature's success criteria.
        # The YOLO PostDuploGate auto-approve requires this to be True; in
        # default mode the user still sees the gate and can override.
        from claude_orchestrator.bob.duplo.judge_anthropic import (
            AnthropicJudge, StubJudge,
        )
        from claude_orchestrator.bob.duplo.meta_rubric import MetaRubricChecker

        use_stub = os.environ.get("BOB_USE_STUB_DUPLO", "0") == "1"
        judge = StubJudge() if use_stub else AnthropicJudge()
        checker = MetaRubricChecker(judge)

        judgments_path = (project_root / ".bob" / "rubric-judgments.jsonl")
        judgments_path.parent.mkdir(parents=True, exist_ok=True)
        all_adequate = True
        for feature in spec.features:
            j = checker.check(feature)
            append_jsonl(judgments_path, {
                "feature_id": feature.id,
                "feature_name": feature.name,
                "verifier_id": feature.verification_plan.verifier_id,
                "adequate": j.adequate,
                "missing": j.missing,
                "reasoning": j.reasoning,
            })
            if not j.adequate:
                all_adequate = False
                print(
                    f"warning: rubric coverage inadequate for feature "
                    f"{feature.id} ({feature.name}): {j}",
                    file=sys.stderr,
                )
        spec.rubric_meta_check_passed = all_adequate
        return spec

    def mcloop_callable(*, feature: Feature, workspace: Path,
                        master_spec: Path, feature_dir: Path) -> McLoopResult:
        verifier: Verifier = registry.resolve_for_feature(feature)
        return runner.run(
            feature=feature,
            workspace=workspace,
            master_spec=master_spec,
            feature_dir=feature_dir,
            verifier=verifier,
        )

    def orchestra_callable(*, feature: Feature, workspace: Path,
                           feature_dir: Path) -> Verdict:
        # Capture the actual diff from the feature's branch against main so
        # the Orchestra agents can review real code, not a placeholder string.
        import subprocess
        from claude_orchestrator.bob.coordinator import _feature_dirname
        branch = f"bob/{_feature_dirname(feature)}"
        diff_proc = subprocess.run(
            ["git", "diff", f"main..{branch}"],
            cwd=str(project_root),
            capture_output=True, text=True,
        )
        if diff_proc.returncode == 0 and diff_proc.stdout.strip():
            diff = diff_proc.stdout
        else:
            # Fall back to diff against the worktree's HEAD vs main, in case
            # branch naming differs.
            fallback = subprocess.run(
                ["git", "diff", "main..HEAD"],
                cwd=str(workspace),
                capture_output=True, text=True,
            )
            diff = fallback.stdout or "(no diff captured — empty branch?)"
        return orchestra_obj.review(
            feature=feature,
            diff=diff,
            debate_log_dir=feature_dir,
        )

    gates = GateRegistry(disabled=disabled_gates or set())
    gates.register("post_duplo", PostDuploGate(yolo=yolo))

    return Coordinator(
        project_root=project_root,
        duplo=duplo_callable,
        mcloop=mcloop_callable,
        orchestra=orchestra_callable,
        gates=gates,
    )
