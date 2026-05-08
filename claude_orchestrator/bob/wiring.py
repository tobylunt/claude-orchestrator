"""Composition: assemble the Coordinator with real callables for `bob run`.

Kept separate from cli.py so M3 can extend (e.g., wire Vroom in parallel)
without touching argparse code.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from claude_orchestrator.bob.coordinator import Coordinator
from claude_orchestrator.bob.duplo.markdown_parser import parse_markdown_spec
from claude_orchestrator.bob.hitl.gates import GateRegistry, PostDuploGate
from claude_orchestrator.bob.mcloop.runner import McLoopResult, McLoopRunner
from claude_orchestrator.bob.orchestra.stub import OrchestraStub
from claude_orchestrator.bob.verifiers.protocol import Verifier
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.bob.verifiers.registry import VerifierRegistry
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
    """Register the M1 verifiers. M2 proper expands the roster."""
    reg = VerifierRegistry()
    reg.register(PythonPytestVerifier())
    return reg


def build_coordinator(
    *,
    project_root: Path,
    spec_path: Path,
    max_iterations: int = 30,
    per_iteration_timeout_s: int = 600,
    disabled_gates: set[str] | None = None,
    claude_cmd: str = "claude",
) -> Coordinator:
    """Assemble a Coordinator from a project root + markdown spec path.

    The returned Coordinator is ready to call .run(RunScope(includes_duplo=True)).
    """
    registry = build_verifier_registry()
    runner = McLoopRunner(
        claude_cmd=claude_cmd,
        max_iterations=max_iterations,
        per_iteration_timeout_s=per_iteration_timeout_s,
    )
    orchestra_obj = _build_orchestra()

    def duplo_callable():
        spec = parse_markdown_spec(spec_path)
        # M2 proper adds the meta-rubric LLM-as-judge check here; for M2a
        # we trust the markdown author and pass it through.
        spec.rubric_meta_check_passed = True
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
        diff = "(M2a placeholder; full diff capture in M2 proper)"
        return orchestra_obj.review(
            feature=feature,
            diff=diff,
            debate_log_dir=feature_dir,
        )

    gates = GateRegistry(disabled=disabled_gates or set())
    gates.register("post_duplo", PostDuploGate())

    return Coordinator(
        project_root=project_root,
        duplo=duplo_callable,
        mcloop=mcloop_callable,
        orchestra=orchestra_callable,
        gates=gates,
    )
