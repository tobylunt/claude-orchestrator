"""Coordinator — the state machine that walks features through phases.

Plain Python. No event bus, no LangGraph, no SQLite. State is the
.bob/ directory plus git.

Dependency injection: Duplo, McLoop, Orchestra are callables passed in.
This makes the coordinator easy to test and lets phases evolve
(e.g., M2 swaps Orchestra stub for AutoGen) without touching the
coordinator.
"""

from __future__ import annotations

import json
import logging
import os
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
from claude_orchestrator.bob.observability import span
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
        verbose: bool = True,
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
        self.verbose = verbose

    def run(self, scope: RunScope) -> None:
        # On resume, reuse the previous run_id from cursor.json so cost rows
        # and run-log events stay grouped under the same logical run.
        # `bob runs` / `bob costs --by run` would otherwise show the same work
        # split across multiple uuids each time the user retried after a crash.
        run_id, is_resume = self._resume_or_new_run_id()
        with span("bob.run", attrs={"run_id": run_id, "resumed": is_resume}):
            self._run_inner(scope, run_id, is_resume=is_resume)

    def _resume_or_new_run_id(self) -> tuple[str, bool]:
        cursor_path = self.bob_dir / "cursor.json"
        if cursor_path.exists():
            try:
                cur = json.loads(cursor_path.read_text())
                phase = cur.get("current_phase")
                prior = cur.get("run_id")
                if isinstance(prior, str) and prior and phase not in (None, "idle"):
                    return prior, True
            except (OSError, json.JSONDecodeError):
                pass
        return str(uuid.uuid4()), False

    def _run_inner(self, scope: RunScope, run_id: str, *, is_resume: bool = False) -> None:
        from claude_orchestrator.bob.cost_tracker import set_run_context
        set_run_context(run_id=run_id, bob_dir=self.bob_dir)
        # Stash run_id on self so _log_event auto-injects it into every event.
        self._current_run_id = run_id

        self._set_cursor("starting", None, run_id)
        started_details: dict = {"run_id": run_id, "resumed": is_resume}
        otel_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
        if otel_endpoint:
            started_details["otel_endpoint"] = otel_endpoint
        self._log_event("run_started", started_details)

        # ---- Duplo phase ----
        # On resume, the spec was already materialized in the prior partial
        # run; re-running Duplo would re-elicit the spec (non-deterministic on
        # the multimodal path) and burn API budget. Skip if the master spec
        # exists and features have already been laid out.
        should_run_duplo = scope.includes_duplo and not (
            is_resume
            and (self.bob_dir / "spec.md").exists()
            and (self.bob_dir / "features").exists()
        )
        if should_run_duplo:
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
        from claude_orchestrator.bob.cost_tracker import set_feature_context
        # Bind feature_id for cost rows so orchestra/mcloop calls inside this
        # block roll up per-feature. Cleared after the feature finishes so the
        # next feature's calls aren't mis-attributed.
        set_feature_context(feature.id)
        try:
            with span("bob.feature", attrs={
                "feature_id": feature.id,
                "feature_name": feature.name,
                "task_type": str(feature.task_type),
            }):
                self._run_feature_inner(feature, feature_dir, run_id)
        except Exception as e:
            # Catch-all: unexpected exceptions (network errors, validation
            # failures, etc.) would otherwise leave the feature stuck
            # IN_PROGRESS with no `feature_failed` event in the run log.
            # Persist FAILED state and a diagnostic so resume + `bob runs`
            # show the failure honestly.
            try:
                feature.status = FeatureStatus.FAILED
                feature.last_error = f"{type(e).__name__}: {e}"
                feature.updated_at = datetime.now(UTC)
                self._save_feature(feature, feature_dir)
            except Exception:
                pass  # don't let cleanup crash mask the real exception
            self._log_event("feature_failed", {
                "feature_id": feature.id,
                "reason": f"{type(e).__name__}: {e}",
            })
            raise
        finally:
            set_feature_context(None)

    def _run_feature_inner(self, feature: Feature, feature_dir: Path, run_id: str) -> None:
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
            # A bare directory at the worktree path is not the same as a real
            # worktree — git always writes a `.git` file/dir on success. If the
            # prior run crashed between `mkdir` and `git worktree add`, the
            # path exists but is unusable; re-add to recover.
            worktree_is_complete = (worktree / ".git").exists()
            if not worktree_is_complete:
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
            # Real git merge before declaring success.
            merged, err = self._merge_to_main(branch_name)
            if not merged:
                feature.status = FeatureStatus.FAILED
                feature.last_error = f"merge conflict on branch {branch_name}: {err[:500]}"
                feature.updated_at = datetime.now(UTC)
                self._save_feature(feature, feature_dir)
                self._log_event("feature_merge_failed", {
                    "feature_id": feature.id,
                    "branch": branch_name,
                    "reason": err[:500],
                })
                # Worktree and branch are LEFT in place for inspection.
                return

            feature.status = FeatureStatus.MERGED
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)
            self._log_event("feature_merged", {"feature_id": feature.id})

            # Remove the worktree.
            try:
                remove_worktree(self.project_root, worktree)
                self._log_event("worktree_removed", {"feature_id": feature.id})
            except WorktreeError as e:
                self._log_event("worktree_remove_failed", {
                    "feature_id": feature.id, "reason": str(e),
                })

            # Delete the branch (it's been merged into main).
            import subprocess
            subprocess.run(
                ["git", "branch", "-d", branch_name],
                cwd=str(self.project_root),
                capture_output=True, text=True,
            )
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

    def _merge_to_main(self, branch_name: str) -> tuple[bool, str]:
        """Merge `branch_name` into main. Returns (success, error_message)."""
        import subprocess

        # Try fast-forward first (history was linear).
        ff_result = subprocess.run(
            ["git", "merge", "--ff-only", branch_name],
            cwd=str(self.project_root),
            capture_output=True, text=True,
        )
        if ff_result.returncode == 0:
            return True, ""

        # FF failed (probably history diverged). Try a no-ff merge.
        nf_result = subprocess.run(
            ["git", "merge", "--no-ff", "-m", f"Merge {branch_name}", branch_name],
            cwd=str(self.project_root),
            capture_output=True, text=True,
        )
        if nf_result.returncode == 0:
            return True, ""

        # Real conflict. Abort the in-progress merge so main is clean.
        subprocess.run(
            ["git", "merge", "--abort"],
            cwd=str(self.project_root),
            capture_output=True, text=True,
        )
        msg = (nf_result.stderr or nf_result.stdout or "merge failed").strip()
        return False, msg

    def _save_feature(self, f: Feature, feature_dir: Path) -> None:
        write_json_atomic(feature_dir / "state.json", f.model_dump(mode="json"))

    def _set_cursor(self, phase: str, feature_id: int | None, run_id: str) -> None:
        write_json_atomic(self.bob_dir / "cursor.json", {
            "run_id": run_id,
            "current_phase": phase,
            "current_feature_id": feature_id,
            "last_event_at": datetime.now(UTC).isoformat(),
        })

    def _format_progress(self, event: str, details: dict) -> str | None:
        """Map a structured event to a one-line human-readable progress message.

        Returns None for events we don't surface (e.g., worktree_removed).
        """
        fid = details.get("feature_id")
        name = details.get("name")
        if event == "run_started":
            rid = details.get("run_id", "")
            return f"-> Bob run starting (run_id: {rid[:8]}...)"
        if event == "post_duplo_gate":
            return f"-> Duplo gate: {details.get('decision')}"
        if event == "feature_started":
            return f"-> Feature {fid} [{name}]: starting"
        if event == "worktree_created":
            return f"-> Feature {fid}: worktree at {details.get('path')}"
        if event == "mcloop_finished":
            return f"-> Feature {fid}: McLoop {details.get('outcome')} in {details.get('iterations')} iter(s)"
        if event == "orchestra_verdict":
            conf = details.get("confidence", 0.0)
            return f"-> Feature {fid}: Orchestra {details.get('decision')} (confidence {conf:.2f})"
        if event == "feature_merged":
            return f"✓ Feature {fid}: merged"
        if event == "feature_merge_failed":
            return f"✗ Feature {fid}: merge to main failed — {details.get('reason', '')[:100]}"
        if event == "feature_failed":
            return f"✗ Feature {fid}: failed — {details.get('reason', '')}"
        if event == "feature_rejected":
            return f"✗ Feature {fid}: rejected by Orchestra — {details.get('reason', '')}"
        if event == "feature_paused_pre_orchestra":
            return f"⏸ Feature {fid}: paused before Orchestra (shutdown)"
        if event == "feature_resumed_at_orchestra":
            return f"↻ Feature {fid}: resuming at Orchestra"
        if event == "worktree_remove_failed":
            return f"! Feature {fid}: worktree cleanup failed — {details.get('reason', '')}"
        if event == "run_aborted":
            return f"⏹ Bob run aborted: {details.get('reason', '')}"
        if event == "run_finished":
            return "-> Bob run finished"
        return None  # unknown / not surfaced

    _EVENT_FIELD_LIMIT = 1500  # bytes per string field

    def _log_event(self, event: str, details: dict) -> None:
        # Auto-inject run_id from the current run if not already in details.
        # Lets `bob runs` group every event by its parent run.
        full_details = dict(details)
        if "run_id" not in full_details and getattr(self, "_current_run_id", None):
            full_details["run_id"] = self._current_run_id
        # Defensively truncate long string fields. A McLoop verifier reason
        # (pytest traceback) can easily exceed the 4096-byte atomic-append
        # safe limit; without this guard append_jsonl raises ValueError mid-
        # feature, the unhandled exception bubbles out of _run_feature_inner,
        # and the feature is left stuck IN_PROGRESS with no error logged.
        for k, v in list(full_details.items()):
            if isinstance(v, str) and len(v) > self._EVENT_FIELD_LIMIT:
                full_details[k] = v[:self._EVENT_FIELD_LIMIT] + "… [truncated]"
        append_jsonl(self.bob_dir / "run-log.jsonl", {
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            **full_details,
        })
        if self.verbose:
            line = self._format_progress(event, full_details)
            if line is not None:
                print(line)
