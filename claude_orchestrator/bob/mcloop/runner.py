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
from typing import Literal, Protocol

if sys.version_info >= (3, 11):
    from datetime import UTC
else:
    from datetime import timezone
    UTC = timezone.utc

from datetime import datetime

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
) -> str:
    template = _read_prompt_template()
    success_block = "\n".join(
        f"- {c}" for c in feature.verification_plan.success_criteria
    ) or "- (no explicit criteria — see feature description)"
    return template.format(
        master_spec_path=str(master_spec),
        feature_spec_path=str(feature_dir / "spec.md"),
        activity_path=str(feature_dir / "activity.md"),
        failed_attempts_path=str(feature_dir / "failed_attempts.md"),
        feature_id=feature.id,
        feature_name=feature.name,
        task_type=str(feature.task_type),
        verifier_id=feature.verification_plan.verifier_id,
        success_criteria_block=success_block,
    )


class McLoopRunner:
    def __init__(
        self,
        claude_cmd: str = "claude",
        max_iterations: int = 30,
        per_iteration_timeout_s: int = 600,
    ) -> None:
        self.claude_cmd = claude_cmd
        self.max_iterations = max_iterations
        self.per_iteration_timeout_s = per_iteration_timeout_s

    def run(
        self,
        *,
        feature: Feature,
        workspace: Path,
        master_spec: Path,
        feature_dir: Path,
        verifier: _Verifier,
    ) -> McLoopResult:
        prompt = _render_prompt(feature, master_spec, feature_dir)
        verifier_log = feature_dir / "verifier-results.jsonl"

        for i in range(1, self.max_iterations + 1):
            try:
                proc = subprocess.run(
                    [self.claude_cmd, "-p", prompt,
                     "--permission-mode", "bypassPermissions"],
                    cwd=str(workspace),
                    capture_output=True,
                    text=True,
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

            if verify_result.status == "inconclusive":
                return McLoopResult(
                    outcome="halted_inconclusive",
                    iterations=i,
                    last_reason=verify_result.reason,
                    last_status=verify_result.status,
                )

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
