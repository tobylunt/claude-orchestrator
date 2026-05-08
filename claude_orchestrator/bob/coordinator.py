"""Coordinator — the state machine that walks features through phases.

Plain Python. No event bus, no LangGraph, no SQLite. State is the
.bob/ directory plus git.

Dependency injection: Duplo, McLoop, Orchestra are callables passed in.
This makes the coordinator easy to test and lets phases evolve
(e.g., M2 swaps Orchestra stub for AutoGen) without touching the
coordinator.
"""

from __future__ import annotations

import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

if sys.version_info >= (3, 11):
    from datetime import UTC
else:
    from datetime import timezone
    UTC = timezone.utc

from claude_orchestrator.bob.hitl.gates import (
    GateDecision,
    GateRegistry,
    GateSkipped,
)
from claude_orchestrator.bob.mcloop.runner import McLoopResult
from claude_orchestrator.bob.signals import is_shutdown_requested
from claude_orchestrator.bob.state_io import (
    append_jsonl,
    read_json,
    write_json_atomic,
)
from claude_orchestrator.bob.worktree import (
    WorktreeError,
    add_worktree,
    remove_worktree,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    Verdict,
)

log = logging.getLogger(__name__)


@dataclass
class RunScope:
    includes_duplo: bool = True
    # M1: no Vroom. M3 will add includes_vroom.


# Phase callable signatures. Kept narrow so M2/M3 can swap implementations.
DuploCallable = Callable[[], Spec]
McLoopCallable = Callable[..., McLoopResult]
OrchestraCallable = Callable[..., Verdict]


def _slugify(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name).strip("-").lower()


def _feature_dirname(f: Feature) -> str:
    return f"{f.id:03d}-{_slugify(f.name)}"


class Coordinator:
    def __init__(
        self,
        *,
        project_root: Path,
        duplo: DuploCallable,
        mcloop: McLoopCallable,
        orchestra: OrchestraCallable,
        gates: GateRegistry,
    ) -> None:
        self.project_root = project_root
        self.bob_dir = project_root / ".bob"
        self.bob_dir.mkdir(parents=True, exist_ok=True)
        (self.bob_dir / "features").mkdir(exist_ok=True)
        (self.bob_dir / "worktrees").mkdir(exist_ok=True)

        self.duplo = duplo
        self.mcloop = mcloop
        self.orchestra = orchestra
        self.gates = gates

    def run(self, scope: RunScope) -> None:
        run_id = str(uuid.uuid4())
        self._set_cursor("starting", None, run_id)
        self._log_event("run_started", {"run_id": run_id})

        # ---- Duplo phase ----
        if scope.includes_duplo:
            self._set_cursor("duplo", None, run_id)
            spec = self.duplo()
            self._materialize_spec(spec)

            try:
                decision = self.gates.run("post_duplo", spec)
            except GateSkipped:
                decision = GateDecision.APPROVE
            self._log_event("post_duplo_gate", {"decision": str(decision)})
            if decision == GateDecision.REJECT:
                self._set_cursor("idle", None, run_id)
                self._log_event("run_aborted", {"reason": "post_duplo_rejected"})
                return

        # ---- Per-feature phases ----
        for feature_dir in sorted((self.bob_dir / "features").iterdir()):
            if not feature_dir.is_dir():
                continue
            if is_shutdown_requested():
                self._log_event("run_aborted", {"reason": "shutdown_requested"})
                self._set_cursor("idle", None, run_id)
                return
            feature = Feature.model_validate_json(
                (feature_dir / "state.json").read_text()
            )
            # REJECTED is intentionally absent — a rejected feature is retried
            # on the next run with the debate log fed back as McLoop context
            # (M2 proper wires this; for M2a/b the retry just re-enters McLoop).
            if feature.status in (
                FeatureStatus.MERGED, FeatureStatus.SKIPPED, FeatureStatus.FAILED
            ):
                continue

            self._run_feature(feature, feature_dir, run_id)

        self._set_cursor("idle", None, run_id)
        self._log_event("run_finished", {"run_id": run_id})

    # ---- internals ----

    def _materialize_spec(self, spec: Spec) -> None:
        # Master spec as markdown:
        master = ["# " + spec.title, "", "## Motivation", spec.motivation, "",
                  "## Features"]
        for f in spec.features:
            master.append(f"### F{f.id}: {f.name}")
            master.append(f"- task_type: {f.task_type}")
            master.append(f"- verifier: {f.verification_plan.verifier_id}")
            master.append("- success_criteria:")
            for c in f.verification_plan.success_criteria:
                master.append(f"  - {c}")
            master.append(f"- description: {f.description}")
        (self.bob_dir / "spec.md").write_text("\n".join(master) + "\n")

        for f in spec.features:
            d = self.bob_dir / "features" / _feature_dirname(f)
            is_new = not d.exists() or not (d / "state.json").exists()
            d.mkdir(parents=True, exist_ok=True)

            if is_new:
                (d / "spec.md").write_text(
                    f"# F{f.id}: {f.name}\n\n{f.description}\n"
                )
                (d / "activity.md").write_text("")
                (d / "failed_attempts.md").write_text("")
                (d / "verifier-results.jsonl").write_text("")
                write_json_atomic(d / "state.json", f.model_dump(mode="json"))
            # else: feature already exists — preserve its state.json, activity.md,
            # failed_attempts.md, verifier-results.jsonl, and spec.md from the prior run.
            # The skip-list check in run() will see the existing status and behave correctly
            # (MERGED/SKIPPED/FAILED features will be skipped; PENDING/IN_PROGRESS/MCLOOP_DONE
            # will be picked up).

    def _run_feature(self, feature: Feature, feature_dir: Path, run_id: str) -> None:
        self._set_cursor("mcloop", feature.id, run_id)
        self._log_event("feature_started", {"feature_id": feature.id, "name": feature.name})

        worktree = self.bob_dir / "worktrees" / _feature_dirname(feature)
        branch_name = f"bob/{_feature_dirname(feature)}"

        # Resume path: if feature is already MCLOOP_DONE, skip McLoop + worktree creation
        # and go directly to Orchestra. Worktree should still be on disk from the prior run.
        if feature.status != FeatureStatus.MCLOOP_DONE:
            feature.status = FeatureStatus.IN_PROGRESS
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)

            # Create worktree if not already present. Both path and branch are
            # handled idempotently by add_worktree (M2b), so retries after
            # crash recovery work even if the branch was created in a prior run.
            if not worktree.exists():
                try:
                    add_worktree(self.project_root, worktree, branch=branch_name)
                    self._log_event("worktree_created", {"feature_id": feature.id, "path": str(worktree)})
                except WorktreeError as e:
                    feature.status = FeatureStatus.FAILED
                    feature.last_error = f"worktree creation failed: {e}"
                    feature.updated_at = datetime.now(UTC)
                    self._save_feature(feature, feature_dir)
                    self._log_event("feature_failed", {"feature_id": feature.id, "reason": str(e)})
                    return

            result: McLoopResult = self.mcloop(
                feature=feature,
                workspace=worktree,
                master_spec=self.bob_dir / "spec.md",
                feature_dir=feature_dir,
            )
            self._log_event("mcloop_finished", {
                "feature_id": feature.id,
                "outcome": result.outcome,
                "iterations": result.iterations,
            })

            if result.outcome != "exit_signal":
                feature.status = FeatureStatus.FAILED
                feature.last_error = result.last_reason
                feature.updated_at = datetime.now(UTC)
                self._save_feature(feature, feature_dir)
                self._log_event("feature_failed", {
                    "feature_id": feature.id,
                    "reason": result.last_reason,
                })
                # Worktree intentionally LEFT in place on failure so the user can inspect.
                return

            feature.status = FeatureStatus.MCLOOP_DONE
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)

            if is_shutdown_requested():
                self._log_event("feature_paused_pre_orchestra", {"feature_id": feature.id})
                return
        else:
            self._log_event("feature_resumed_at_orchestra", {"feature_id": feature.id})

        # ---- Orchestra (always runs; either fresh post-McLoop or resumed) ----
        self._set_cursor("orchestra", feature.id, run_id)
        verdict: Verdict = self.orchestra(
            feature=feature,
            workspace=worktree,
            feature_dir=feature_dir,
        )
        self._log_event("orchestra_verdict", {
            "feature_id": feature.id,
            "decision": verdict.decision,
            "confidence": verdict.confidence,
        })

        if verdict.decision == "approve":
            feature.status = FeatureStatus.MERGED
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)
            self._log_event("feature_merged", {"feature_id": feature.id})
            # Remove the worktree after a successful merge.
            try:
                remove_worktree(self.project_root, worktree)
                self._log_event("worktree_removed", {"feature_id": feature.id})
            except WorktreeError as e:
                self._log_event("worktree_remove_failed", {
                    "feature_id": feature.id, "reason": str(e),
                })
        else:
            feature.status = FeatureStatus.REJECTED
            feature.last_error = verdict.judge_reasoning
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)
            self._log_event("feature_rejected", {
                "feature_id": feature.id,
                "reason": verdict.judge_reasoning,
            })
            # Worktree LEFT in place on rejection so user can debug.

    def _save_feature(self, f: Feature, feature_dir: Path) -> None:
        write_json_atomic(feature_dir / "state.json", f.model_dump(mode="json"))

    def _set_cursor(self, phase: str, feature_id: int | None, run_id: str) -> None:
        write_json_atomic(self.bob_dir / "cursor.json", {
            "run_id": run_id,
            "current_phase": phase,
            "current_feature_id": feature_id,
            "last_event_at": datetime.now(UTC).isoformat(),
        })

    def _log_event(self, event: str, details: dict) -> None:
        append_jsonl(self.bob_dir / "run-log.jsonl", {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **details,
        })
