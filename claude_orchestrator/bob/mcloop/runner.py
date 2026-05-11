"""McLoop runner — fresh `claude -p` subprocess per iteration (bash-loop pattern).

Each iteration:
  1. Render the prompt template with feature context.
  2. Spawn `claude -p` as a subprocess. Wait for exit (with timeout).
  3. Run the verifier on the workspace.
  4. Append the verifier result to verifier-results.jsonl.
  5. Decide what to do next based on the verifier result and the agent's
     stdout (looking for <promise>EXIT_SIGNAL</promise> or
     <promise>HALT_INCONCLUSIVE</promise>).

This is the M1 implementation. M2 wraps in sandbox tier 2 (Docker).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from claude_orchestrator.bob.yolo import YoloConfig

if sys.version_info >= (3, 11):
    from datetime import UTC
else:
    from datetime import timezone
    UTC = timezone.utc

from datetime import datetime

from claude_orchestrator.bob.observability import span
from claude_orchestrator.bob.sandbox.executor import SubprocessExecutor
from claude_orchestrator.bob.sandbox.host import HostExecutor
from claude_orchestrator.bob.signals import is_shutdown_requested
from claude_orchestrator.bob.state_io import append_jsonl
from claude_orchestrator.bob.verifiers.protocol import VerifyResult
from claude_orchestrator.models import Feature

_EXIT_PROMISE_RE = re.compile(r"<promise>EXIT_SIGNAL</promise>")
_HALT_PROMISE_RE = re.compile(r"<promise>HALT_INCONCLUSIVE</promise>")


class _Verifier(Protocol):
    """Local structural type — matches verifiers.protocol.Verifier."""
    id: str
    def verify(self, workspace: Path, feature: Feature) -> VerifyResult: ...


@dataclass(frozen=True)
class McLoopResult:
    outcome: Literal["exit_signal", "halted_inconclusive", "max_iterations", "error"]
    iterations: int
    last_reason: str
    last_status: str | None  # final verifier status, if any


def _read_prompt_template() -> str:
    here = Path(__file__).parent / "prompts" / "iteration.md"
    return here.read_text()


def _render_prompt(
    feature: Feature,
    master_spec: Path,
    feature_dir: Path,
    executor=None,
) -> str:
    """Render the iteration prompt. If `executor` is provided (Docker etc.),
    file paths are translated through `executor.translate_path()` so they
    point to mounted in-container paths instead of host paths the container
    can't see.
    """
    template = _read_prompt_template()
    success_block = "\n".join(
        f"- {c}" for c in feature.verification_plan.success_criteria
    ) or "- (no explicit criteria — see feature description)"

    def _tr(p: Path) -> str:
        if executor is not None and hasattr(executor, "translate_path"):
            return executor.translate_path(p)
        return str(p)

    return template.format(
        master_spec_path=_tr(master_spec),
        feature_spec_path=_tr(feature_dir / "spec.md"),
        activity_path=_tr(feature_dir / "activity.md"),
        failed_attempts_path=_tr(feature_dir / "failed_attempts.md"),
        feature_id=feature.id,
        feature_name=feature.name,
        task_type=str(feature.task_type),
        verifier_id=feature.verification_plan.verifier_id,
        success_criteria_block=success_block,
    )


_AUTOCOMMIT_EXCLUDES = (
    # glob magic matches the pattern at any depth from the worktree root
    # (default magic requires a directory level, so `**/*.pyc` misses a
    # top-level `mod.pyc`).
    ":(exclude,glob)**/__pycache__/**",
    ":(exclude,glob)**/__pycache__",
    ":(exclude,glob)**/*.pyc",
    ":(exclude,glob)**/*.pyo",
    ":(exclude,glob)*.pyc",
    ":(exclude,glob)*.pyo",
    ":(exclude,glob)**/.pytest_cache/**",
    ":(exclude,glob)**/.mypy_cache/**",
    ":(exclude,glob)**/.ruff_cache/**",
    ":(exclude,glob)**/node_modules/**",
    ":(exclude,glob)**/.DS_Store",
)


def _autocommit_iteration(workspace: Path, *, iteration: int) -> None:
    """Stage + commit any uncommitted worktree changes after a green
    verifier result. No-op if the index is clean. Necessary under
    `--sandbox docker` because the inner claude can't reach the host's
    `.git` directory and so can't commit its own work.

    Excludes common build-cache patterns even when the project lacks a
    .gitignore — committing __pycache__/*.pyc into source-control would
    correctly draw an Orchestra rejection on hygiene grounds.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
        )
        if status.returncode != 0 or not status.stdout.strip():
            return  # nothing to commit
        subprocess.run(
            ["git", "add", "--", ".", *_AUTOCOMMIT_EXCLUDES],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
        )
        subprocess.run(
            ["git", "-c", "user.email=bob@local",
             "-c", "user.name=Bob (autocommit)",
             "commit", "-m", f"mcloop iter {iteration}: verifier green"],
            cwd=str(workspace), capture_output=True, text=True, timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        # Don't let an autocommit failure mask a successful iteration; the
        # verifier already returned ok, so the work is real. Orchestra will
        # see the empty diff and reject, surfacing the issue to the user.
        pass


class McLoopRunner:
    def __init__(
        self,
        claude_cmd: str = "claude",
        max_iterations: int = 30,
        per_iteration_timeout_s: int = 600,
        executor: SubprocessExecutor | None = None,
        yolo: "YoloConfig | None" = None,  # NEW
    ) -> None:
        self.claude_cmd = claude_cmd
        self.max_iterations = max_iterations
        self.per_iteration_timeout_s = per_iteration_timeout_s
        self.executor = executor or HostExecutor()
        self.yolo = yolo

    def run(
        self,
        *,
        feature: Feature,
        workspace: Path,
        master_spec: Path,
        feature_dir: Path,
        verifier: _Verifier,
    ) -> McLoopResult:
        # Preflight the verifier BEFORE spawning any claude -p. If the verifier
        # can't operate (e.g., pytest is missing in the sandbox), iterating
        # would burn API budget on noise.
        preflight = verifier.preflight(workspace)
        if preflight is not None and not preflight.ok:
            missing = ", ".join(preflight.missing_tools) or "(no missing_tools listed)"
            reason = (
                f"verifier preflight failed: missing tools: {missing}"
                + (f" -- {preflight.notes}" if preflight.notes else "")
            )
            return McLoopResult(
                outcome="halted_inconclusive",
                iterations=0,
                last_reason=reason,
                last_status=None,
            )

        # Register .bob/ as an additional volume mount so the prompt's
        # references to master_spec.md, activity.md, etc. resolve inside
        # the container. master_spec lives at <project>/.bob/spec.md; its
        # parent is the .bob/ dir. HostExecutor.add_volume is a no-op.
        bob_dir = master_spec.parent.resolve()
        if hasattr(self.executor, "add_volume"):
            self.executor.add_volume(bob_dir, "/bob-state")

        prompt = _render_prompt(feature, master_spec, feature_dir, executor=self.executor)
        verifier_log = feature_dir / "verifier-results.jsonl"
        consecutive_inconclusive = 0  # NEW

        for i in range(1, self.max_iterations + 1):
            # Poll shutdown before each iteration so Ctrl-C is responsive.
            # Without this, after the first SIGINT McLoop continues iterating
            # (each `claude -p` ignores propagated SIGINT) until max_iterations
            # — a frustrating "I pressed Ctrl-C but it kept spending money".
            if is_shutdown_requested():
                return McLoopResult(
                    outcome="error",
                    iterations=i - 1,
                    last_reason="shutdown requested between iterations",
                    last_status=None,
                )
            with span("bob.mcloop.iter", attrs={
                "feature_id": feature.id,
                "iteration": i,
            }):
                try:
                    proc = self.executor.run(
                        [self.claude_cmd, "-p", prompt,
                         "--permission-mode", "bypassPermissions",
                         "--output-format", "stream-json",
                         "--include-partial-messages",
                         "--verbose"],
                        cwd=workspace,
                        env=None,
                        timeout=self.per_iteration_timeout_s,
                    )
                except subprocess.TimeoutExpired:
                    return McLoopResult(
                        outcome="error",
                        iterations=i,
                        last_reason=f"claude -p timed out at iteration {i}",
                        last_status=None,
                    )

                stdout = proc.stdout

                # Persist stdout + stderr for debugging.
                log_path = feature_dir / f"iter-{i}.log"
                log_content_parts = []
                if proc.stdout:
                    log_content_parts.append("=== STDOUT ===")
                    log_content_parts.append(proc.stdout)
                if proc.stderr:
                    log_content_parts.append("\n=== STDERR ===")
                    log_content_parts.append(proc.stderr)
                if log_content_parts:
                    log_path.write_text("\n".join(log_content_parts))

                verify_result = verifier.verify(workspace, feature)
                append_jsonl(verifier_log, {
                    "iteration": i,
                    "status": verify_result.status,
                    "reason": verify_result.reason[:1000],
                    "ts": datetime.now(UTC).isoformat(),
                })

                # If the verifier passed, auto-commit any uncommitted changes
                # on the host. Under --sandbox docker the inner claude can't
                # commit (the worktree's .git pointer references a host path
                # not mounted in the container) — without this, Orchestra's
                # diff capture comes back empty and the feature is rejected.
                # Harmless under --sandbox host since claude would already
                # have committed; `git commit` on an empty index is a no-op
                # we explicitly check for.
                if verify_result.status == "ok":
                    _autocommit_iteration(workspace, iteration=i)

                if verify_result.status == "inconclusive":
                    consecutive_inconclusive += 1
                    yolo_active = self.yolo is not None and self.yolo.enabled
                    if not yolo_active:
                        # Default mode: halt loud on first Inconclusive.
                        return McLoopResult(
                            outcome="halted_inconclusive",
                            iterations=i,
                            last_reason=verify_result.reason,
                            last_status=verify_result.status,
                        )
                    # YOLO mode: bounded feedback. Halt only if we've hit the consecutive cap.
                    if consecutive_inconclusive >= self.yolo.max_inconclusive:
                        return McLoopResult(
                            outcome="halted_inconclusive",
                            iterations=i,
                            last_reason=(
                                f"YOLO halted after {consecutive_inconclusive} consecutive "
                                f"Inconclusives (max={self.yolo.max_inconclusive}): "
                                f"{verify_result.reason}"
                            ),
                            last_status=verify_result.status,
                        )
                    # Else: continue to next iteration. The verifier's reason becomes
                    # context for the agent's next pass via the iter log.
                else:
                    # Reset the counter on any non-inconclusive verifier result.
                    consecutive_inconclusive = 0

                if _HALT_PROMISE_RE.search(stdout):
                    return McLoopResult(
                        outcome="halted_inconclusive",
                        iterations=i,
                        last_reason="agent emitted HALT_INCONCLUSIVE",
                        last_status=verify_result.status,
                    )

                if _EXIT_PROMISE_RE.search(stdout) and verify_result.status == "ok":
                    return McLoopResult(
                        outcome="exit_signal",
                        iterations=i,
                        last_reason="agent emitted EXIT_SIGNAL with verifier ok",
                        last_status="ok",
                    )
                # Otherwise, continue to next iteration.

        return McLoopResult(
            outcome="max_iterations",
            iterations=self.max_iterations,
            last_reason=f"reached max_iterations={self.max_iterations}",
            last_status=None,
        )
