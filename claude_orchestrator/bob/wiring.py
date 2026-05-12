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
from claude_orchestrator.bob.run_config import RunConfig
from claude_orchestrator.bob.state_io import append_jsonl
from claude_orchestrator.bob.hitl.gates import GateRegistry, PostDuploGate
from claude_orchestrator.bob.mcloop.runner import McLoopResult, McLoopRunner
from claude_orchestrator.bob.orchestra.stub import OrchestraStub
from claude_orchestrator.bob.verifiers.protocol import Verifier
from claude_orchestrator.bob.verifiers.registry import VerifierRegistry
from claude_orchestrator.bob.vroom_config import VroomConfig
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
    from claude_orchestrator.bob.openai_config import (
        resolve_openai_reasoning_effort,
    )
    from claude_orchestrator.bob.review_policy import ReviewPolicy

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
    fast_judge_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_FAST_JUDGE_MODEL", "claude-sonnet-4-6"),
        system='You synthesize the debate. Reply JSON: {"content": "...", "decision": "approve|reject|abstain", "confidence": 0.0..1.0}',
        role="judge",
    )
    premium_codex_agent = OpenAIDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_PREMIUM_CODEX_MODEL", "gpt-5.5"),
        system='You are a premium adversarial reviewer. Find subtle bugs, security issues, architectural risk, and hidden edge cases. Reply JSON: {"content": "...", "decision": "approve|reject|abstain"}',
        role="premium_codex",
        reasoning_effort=resolve_openai_reasoning_effort(
            env_var="BOB_ORCHESTRA_PREMIUM_CODEX_EFFORT",
            default="xhigh",
        ),
    )
    premium_judge_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_JUDGE_MODEL", "claude-opus-4-7"),
        system='You are the premium final judge. Synthesize baseline and premium reviews. Reply JSON: {"content": "...", "decision": "approve|reject|abstain", "confidence": 0.0..1.0}',
        role="premium_judge",
    )
    return RealOrchestra(
        claude_agent=claude_agent,
        codex_agent=codex_agent,
        judge_agent=fast_judge_agent,
        premium_codex_agent=premium_codex_agent,
        premium_judge_agent=premium_judge_agent,
        review_policy=ReviewPolicy.from_env(),
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
                "malformed": j.malformed,
                # Keep the raw judge response on disk so an unexplained
                # 'inadequate' verdict is debuggable post-hoc.
                "raw_response": (j.raw_response or "")[:2000],
            })
            if not j.adequate:
                all_adequate = False
                if j.malformed:
                    print(
                        f"warning: MALFORMED rubric verdict for feature "
                        f"{feature.id} ({feature.name}) — judge said "
                        f"'inadequate' with no missing-criteria or reasoning. "
                        f"This is treated as a block, but the explanation is "
                        f"absent; inspect .bob/rubric-judgments.jsonl "
                        f"'raw_response' before approving manually.",
                        file=sys.stderr,
                    )
                else:
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


def build_coordinator_from_run_config(config: RunConfig) -> Coordinator:
    """Assemble the run Coordinator from resolved CLI config."""
    return build_coordinator(
        project_root=config.project_root,
        spec_path=config.spec_path,
        max_iterations=config.max_iterations,
        disabled_gates=set(config.disabled_gates),
        sandbox_tier=config.sandbox_tier,
        yolo=config.yolo,
    )


def build_vroom_subprocess_invocation(
    config: RunConfig,
    *,
    base_env: dict[str, str] | None = None,
    python_executable: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return the command and environment for the optional Vroom subprocess."""
    if base_env is None:
        base_env = os.environ
    cmd = [
        python_executable or sys.executable,
        "-m",
        "claude_orchestrator.bob.cli",
        "vroom",
        "--project",
        str(config.project_root),
        "--interval",
        "1800",
        "--sandbox",
        config.sandbox_tier,
    ]
    child_env = dict(base_env)
    if config.yolo.enabled:
        child_env["BOB_VROOM_YOLO_ENABLED"] = "1"
        child_env["BOB_VROOM_YOLO_SEVERITY"] = config.yolo.vroom_severity
        child_env["BOB_VROOM_YOLO_MAX_COST"] = str(config.yolo.max_cost)
        child_env["BOB_YOLO_MAX_INCONCLUSIVE"] = str(config.yolo.max_inconclusive)
    if config.otel_endpoint:
        child_env["OTEL_EXPORTER_OTLP_ENDPOINT"] = config.otel_endpoint
    return cmd, child_env


class _NoFindingClaudeAuditor:
    id = "claude_architect"

    def triggers_on(self, changed_files):
        return True

    def audit(self, workspace, changed_files):
        return []


def build_vroom_auditor_pool(config: VroomConfig):
    """Build the Vroom auditor pool from resolved config."""
    from claude_orchestrator.bob.vroom.auditor_pool import AuditorPool
    from claude_orchestrator.bob.vroom.auditors.semgrep import SemgrepAuditor

    if config.use_stub:
        from claude_orchestrator.bob.vroom.auditors.llm_stubs import (
            CodexSecurityAuditorStub,
        )
        claude_aud = _NoFindingClaudeAuditor()
        codex_aud = CodexSecurityAuditorStub()
    else:
        from claude_orchestrator.bob.vroom.auditors.claude_architect import (
            ClaudeArchitectAuditor,
        )
        from claude_orchestrator.bob.vroom.auditors.codex_security import (
            CodexSecurityAuditor,
        )
        claude_aud = ClaudeArchitectAuditor()
        codex_aud = CodexSecurityAuditor()

    return AuditorPool([SemgrepAuditor(), claude_aud, codex_aud])


def build_vroom_fix_driver(config: VroomConfig):
    """Build the Vroom fix-loop driver, including its McLoop runner."""
    from claude_orchestrator.bob.mcloop.runner import McLoopRunner
    from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
    from claude_orchestrator.bob.vroom.fix_loop import (
        FixLoopDriver,
        render_finding_spec,
    )

    executor = _build_executor(config.sandbox_tier, project_root=config.project_root)
    runner = McLoopRunner(
        claude_cmd="claude",
        max_iterations=10,
        executor=executor,
        yolo=config.yolo,
    )
    verifier = PythonPytestVerifier()

    def run_mcloop_for_finding(*, branch_name: str, workspace: Path, finding) -> bool:
        from claude_orchestrator.models import (
            FeatureStatus,
            TaskType,
            VerificationPlan,
        )

        feature = Feature(
            id=0,
            name=f"fix-{finding.rule_id}",
            description=f"Fix: {finding.message}",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["all tests pass"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )
        vroom_feature_dir = (
            config.project_root
            / ".bob"
            / "vroom-features"
            / branch_name.replace("/", "-")
        )
        vroom_feature_dir.mkdir(parents=True, exist_ok=True)
        (vroom_feature_dir / "spec.md").write_text(render_finding_spec(finding))
        for filename in ("activity.md", "failed_attempts.md", "verifier-results.jsonl"):
            (vroom_feature_dir / filename).write_text("")

        master_spec = config.project_root / ".bob" / "spec.md"
        if not master_spec.exists():
            master_spec.write_text("# (vroom)\n")

        result = runner.run(
            feature=feature,
            workspace=workspace,
            master_spec=master_spec,
            feature_dir=vroom_feature_dir,
            verifier=verifier,
        )
        return result.outcome == "exit_signal"

    return FixLoopDriver(
        repo=config.project_root,
        run_mcloop=run_mcloop_for_finding,
    )


def build_vroom_audit_cycle(
    config: VroomConfig,
    *,
    include_fix_driver: bool,
):
    """Build one full Vroom audit cycle from resolved config."""
    from claude_orchestrator.bob.vroom.audit_cycle import VroomAuditCycle
    from claude_orchestrator.bob.vroom.triage import VroomTriageGate

    fix_driver = build_vroom_fix_driver(config) if include_fix_driver else None
    return VroomAuditCycle(
        project_root=config.project_root,
        auditor_pool=build_vroom_auditor_pool(config),
        triage_gate=VroomTriageGate(yolo=config.yolo),
        fix_driver=fix_driver,
    )


def build_vroom_daemon(config: VroomConfig):
    """Build the long-running Vroom daemon from resolved config."""
    from claude_orchestrator.bob.vroom.daemon import VroomDaemon

    cycle = build_vroom_audit_cycle(config, include_fix_driver=True)
    return VroomDaemon(
        project_root=config.project_root,
        audit_cycle=cycle.run,
        timer_interval_s=config.timer_interval_s,
        watch_main_ref=config.watch_main_ref,
    )
