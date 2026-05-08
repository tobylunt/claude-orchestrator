# Bob M2a: CLI End-to-End Wiring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `bob run --inputs spec.md` actually run the M1 pipeline end-to-end (today it's a `print` stub). Address the polish items the M1 final reviewer flagged: wire the CLI to the Coordinator with real worktree creation and cleanup, add `is_shutdown_requested()` polling between phases for graceful shutdown, fix the `Pyhton's` typo in the verifier protocol, and ship an integration test that exercises `bob run` against a tiny project.

**Architecture:** Extends M1 without altering its interfaces. CLI's `_cmd_run` becomes the wiring point: it parses the markdown spec, registers verifiers, builds a worktree-managing `mcloop_callable`, builds a `bypass-judge` `OrchestraStub` (M2 proper replaces with AutoGen), acquires the process lock, installs signal handlers, and runs the Coordinator. Coordinator gains worktree create/remove around per-feature work and shutdown polling. Phase contracts are unchanged.

**Tech Stack:** Same as M1 (Python 3.10, pydantic v2, pytest, subprocess, git worktree).

**Spec:** `docs/superpowers/specs/2026-05-06-bob-design.md`. **M1 plan it builds on:** `docs/superpowers/plans/2026-05-07-bob-m1-thin-slice.md`.

---

## File structure (M2a)

**Modified:**
- `claude_orchestrator/bob/coordinator.py` — add worktree create/remove; add `is_shutdown_requested()` polls; thread the Coordinator's `project_root` into the worktree paths
- `claude_orchestrator/bob/cli.py` — replace stub `_cmd_run` with real wiring
- `claude_orchestrator/bob/verifiers/protocol.py` — fix `Pyhton's` typo
- `tests/bob/test_coordinator.py` — extend to cover shutdown polling
- `tests/bob/test_cli.py` — extend to cover real `bob run` invocation

**Created:**
- `claude_orchestrator/bob/wiring.py` — small composition module that builds the Coordinator from a project root + spec path. Keeps `cli.py` thin and gives M3 a stable seam to extend.
- `tests/bob/test_wiring.py` — unit-level tests for the wiring composition
- `tests/bob/test_e2e_cli.py` — full CLI integration test invoking `bob run` against a tiny demo project

**Not changed (and why):**
- `bob/mcloop/runner.py` — unchanged; the runner already exposes the right signature
- `bob/orchestra/stub.py` — unchanged; the stub already takes an injected judge
- `bob/duplo/markdown_parser.py` — unchanged; already returns a `Spec`
- `bob/verifiers/python_pytest.py`, `bob/verifiers/registry.py` — unchanged; we just register python_pytest at startup

---

### Task 1: Fix the `Pyhton's` typo

**Files:**
- Modify: `claude_orchestrator/bob/verifiers/protocol.py`

- [ ] **Step 1: Make the edit**

In `claude_orchestrator/bob/verifiers/protocol.py`, find the line containing `Pyhton's runtime Protocol` and change it to `Python's runtime Protocol`.

- [ ] **Step 2: Verify tests still pass**

Run: `pytest tests/bob/test_verifier_protocol.py -v`

Expected: 5 passed (no behavior change, just a comment/string).

- [ ] **Step 3: Commit**

```bash
git add claude_orchestrator/bob/verifiers/protocol.py
git commit -m "fix(bob): typo in verifier protocol docstring (Pyhton -> Python)"
```

---

### Task 2: Coordinator polls `is_shutdown_requested()` between phases

**Files:**
- Modify: `claude_orchestrator/bob/coordinator.py`
- Modify: `tests/bob/test_coordinator.py`

- [ ] **Step 1: Write failing test**

Append to `tests/bob/test_coordinator.py`:

```python
def test_coordinator_aborts_on_shutdown_request(project_root: Path, monkeypatch):
    """Setting the shutdown flag between features stops the loop."""
    from claude_orchestrator.bob import signals
    spec = _spec_with_features("a", "b")

    duplo = MagicMock(return_value=spec)
    # mcloop sets the shutdown flag during the FIRST feature; the second feature
    # must not run.
    def mcloop_setting_shutdown(*, feature, workspace, master_spec, feature_dir):
        # Simulate Ctrl-C right after the first feature's mcloop returns.
        signals._shutdown_requested = True
        return McLoopResult(
            outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
        )
    mcloop = MagicMock(side_effect=mcloop_setting_shutdown)
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    # Reset the global shutdown flag at the start of the test
    signals._shutdown_requested = False

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    try:
        coord.run(RunScope(includes_duplo=True))
    finally:
        signals._shutdown_requested = False  # leave clean for other tests

    # Only the first feature ran:
    assert mcloop.call_count == 1
    # Run-log records the shutdown:
    events = [e["event"] for e in read_jsonl(project_root / ".bob" / "run-log.jsonl")]
    assert "run_aborted" in events
```

You'll also need to add an import: `from claude_orchestrator.bob.coordinator import ...` is already there. Add `from claude_orchestrator.bob import signals` at the top.

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_coordinator.py::test_coordinator_aborts_on_shutdown_request -v`

Expected: FAIL — both features run because Coordinator doesn't poll.

- [ ] **Step 3: Add the polling**

In `claude_orchestrator/bob/coordinator.py`, add an import at the top:

```python
from claude_orchestrator.bob.signals import is_shutdown_requested
```

Inside the `run()` method, change the per-feature loop to check the flag:

```python
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
            if feature.status in (
                FeatureStatus.MERGED, FeatureStatus.SKIPPED, FeatureStatus.FAILED
            ):
                continue
            self._run_feature(feature, feature_dir, run_id)
```

Also add a poll inside `_run_feature` between McLoop and Orchestra:

```python
        feature.status = FeatureStatus.MCLOOP_DONE
        feature.updated_at = datetime.now(UTC)
        self._save_feature(feature, feature_dir)

        if is_shutdown_requested():
            # Leave feature in MCLOOP_DONE so we can resume from Orchestra later.
            self._log_event("feature_paused_pre_orchestra", {"feature_id": feature.id})
            return

        # ---- Orchestra ----
```

- [ ] **Step 4: Run the new test**

Run: `pytest tests/bob/test_coordinator.py::test_coordinator_aborts_on_shutdown_request -v`

Expected: PASS — only one mcloop call, `run_aborted` event recorded.

- [ ] **Step 5: Run full coordinator test file + full suite**

Run:
```bash
pytest tests/bob/test_coordinator.py -v
pytest -q
```

Expected: 5 coordinator tests pass; full suite stays green at 172.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/coordinator.py tests/bob/test_coordinator.py
git commit -m "feat(bob): Coordinator polls is_shutdown_requested between phases"
```

---

### Task 3: Coordinator creates and removes worktrees per feature

**Files:**
- Modify: `claude_orchestrator/bob/coordinator.py`
- Modify: `tests/bob/test_coordinator.py`

- [ ] **Step 1: Write failing test**

Append to `tests/bob/test_coordinator.py`:

```python
def test_coordinator_creates_and_removes_worktree(project_root: Path):
    """When merge succeeds, Coordinator creates the worktree before McLoop and removes it after merge."""
    import subprocess as sp
    # Initialize a git repo so worktree commands work.
    sp.run(["git", "init", "-b", "main", str(project_root)], check=True)
    (project_root / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(project_root), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(project_root), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )

    spec = _spec_with_features("a")
    duplo = MagicMock(return_value=spec)

    def mcloop_callable(*, feature, workspace, master_spec, feature_dir):
        # The workspace must exist when McLoop is called.
        assert workspace.exists(), f"worktree not created: {workspace}"
        return McLoopResult(
            outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
        )

    orchestra_callable = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop_callable,
        orchestra=orchestra_callable, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    # After merge, the worktree should be removed.
    worktree_path = project_root / ".bob" / "worktrees" / "001-a"
    assert not worktree_path.exists(), \
        f"worktree should have been removed after merge: {worktree_path}"
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_coordinator.py::test_coordinator_creates_and_removes_worktree -v`

Expected: FAIL — worktree wasn't created.

- [ ] **Step 3: Wire worktree create/remove into `_run_feature`**

In `claude_orchestrator/bob/coordinator.py`, add an import:

```python
from claude_orchestrator.bob.worktree import (
    WorktreeError,
    add_worktree,
    remove_worktree,
)
```

Modify `_run_feature` to create the worktree before McLoop and remove it on the merge path. The implementation:

```python
    def _run_feature(self, feature: Feature, feature_dir: Path, run_id: str) -> None:
        self._set_cursor("mcloop", feature.id, run_id)
        self._log_event("feature_started", {"feature_id": feature.id, "name": feature.name})
        feature.status = FeatureStatus.IN_PROGRESS
        feature.updated_at = datetime.now(UTC)
        self._save_feature(feature, feature_dir)

        worktree = self.bob_dir / "worktrees" / _feature_dirname(feature)
        branch_name = f"bob/{_feature_dirname(feature)}"

        # Create worktree if not already present (idempotent for retries).
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
                # Non-fatal — log and continue; user can clean up manually.
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
```

- [ ] **Step 4: Run the new test**

Run: `pytest tests/bob/test_coordinator.py::test_coordinator_creates_and_removes_worktree -v`

Expected: PASS — worktree exists during McLoop, removed after merge.

- [ ] **Step 5: Run full coordinator test file**

Run: `pytest tests/bob/test_coordinator.py -v`

Expected: All previously-passing tests STILL pass. The earlier `test_coordinator_walks_features_in_order` and friends used MagicMock for mcloop and orchestra; they still work because the mocks satisfy the new code path. (If any test now expects a worktree to exist or not exist, adjust the test fixture by `git init`-ing the project root and accept that worktrees get created.)

If a previous test fails because it doesn't `git init` the project_root, update the relevant fixture / test:

```python
# In any test that exercises the merge path, ensure the project_root is a git repo:
@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    import subprocess as sp
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path
```

Replace the existing `project_root` fixture in `tests/bob/test_coordinator.py` with this richer version.

- [ ] **Step 6: Run full suite**

Run: `pytest -q`

Expected: All tests pass (175 if we added 2 new tests in this task plus task 2; verify exact number).

- [ ] **Step 7: Commit**

```bash
git add claude_orchestrator/bob/coordinator.py tests/bob/test_coordinator.py
git commit -m "feat(bob): Coordinator creates per-feature worktrees and removes on merge"
```

---

### Task 4: Wiring composition module

**Files:**
- Create: `claude_orchestrator/bob/wiring.py`
- Create: `tests/bob/test_wiring.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_wiring.py`:

```python
"""Unit tests for the bob/wiring.py composition module."""
import subprocess as sp
from pathlib import Path

import pytest

from claude_orchestrator.bob.wiring import (
    AutoApproveJudge,
    build_coordinator,
    build_verifier_registry,
)
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def test_build_verifier_registry_includes_python_pytest():
    reg = build_verifier_registry()
    v = reg.get("python_pytest")
    assert isinstance(v, PythonPytestVerifier)


def test_auto_approve_judge_returns_approve():
    judge = AutoApproveJudge()
    feature = Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["x"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.MCLOOP_DONE,
    )
    result = judge.judge_diff(feature, diff="(stub)")
    assert result["decision"] == "approve"
    assert result["confidence"] == 1.0


def test_build_coordinator_returns_callable_coordinator(tmp_path: Path):
    """Smoke test: build_coordinator returns a Coordinator that has duplo/mcloop/orchestra wired."""
    sp.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    sp.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    sp.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(
        "# T\n## Motivation\nm\n## Features\n### F1: a\n"
        "- task_type: library\n- verifier: python_pytest\n"
        "- success_criteria:\n  - x\n- description: a\n"
    )

    coord = build_coordinator(
        project_root=tmp_path,
        spec_path=spec_path,
        max_iterations=1,
        disabled_gates={"post_duplo"},
        claude_cmd="echo",  # placeholder for tests
    )
    # The coordinator's duplo callable should return a Spec when called.
    spec = coord.duplo()
    assert spec.title == "T"
    assert len(spec.features) == 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_wiring.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `wiring.py`**

`claude_orchestrator/bob/wiring.py`:

```python
"""Composition: assemble the Coordinator with real callables for `bob run`.

Kept separate from cli.py so M3 can extend (e.g., wire Vroom in parallel)
without touching argparse code.
"""

from __future__ import annotations

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
    orchestra_stub = OrchestraStub(judge=AutoApproveJudge())

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
        # M2a: stub Orchestra reads the worktree's HEAD diff against main.
        # M2 proper passes the actual diff to AutoGen agents.
        diff = "(M2a placeholder; full diff capture in M2 proper)"
        return orchestra_stub.review(
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_wiring.py -v`

Expected: PASS — all 3 tests green.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`

Expected: All tests pass (178 expected if 175 were green going in).

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/wiring.py tests/bob/test_wiring.py
git commit -m "feat(bob): wiring composition module for assembling the Coordinator"
```

---

### Task 5: Replace `_cmd_run` stub with real wiring

**Files:**
- Modify: `claude_orchestrator/bob/cli.py`
- Modify: `tests/bob/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/bob/test_cli.py`:

```python
def test_orchestrate_bob_run_requires_inputs(tmp_path: Path):
    """`bob run` without --inputs should exit with a clear error."""
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "inputs" in result.stderr.lower() or "inputs" in result.stdout.lower()


def test_orchestrate_bob_run_rejects_missing_spec(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(tmp_path),
         "--inputs", str(tmp_path / "does-not-exist.md")],
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "not found" in (result.stderr + result.stdout).lower()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_cli.py -v`

Expected: FAIL — the current `_cmd_run` stub always returns 0 regardless of inputs.

- [ ] **Step 3: Replace `_cmd_run` in `claude_orchestrator/bob/cli.py`**

Replace `_cmd_run` with the real implementation:

```python
def _cmd_run(args: argparse.Namespace) -> int:
    from claude_orchestrator.bob.coordinator import RunScope
    from claude_orchestrator.bob.process_lock import (
        Lock, LockHeld, StalePidDetected, acquire_lock, release_lock,
    )
    from claude_orchestrator.bob.signals import (
        install_handlers, register_cleanup,
    )
    from claude_orchestrator.bob.wiring import build_coordinator

    project_root = Path(args.project).resolve()
    if not project_root.exists():
        print(f"error: project root not found: {project_root}", file=sys.stderr)
        return 2

    if not args.inputs:
        print("error: --inputs is required (path to a markdown spec)", file=sys.stderr)
        return 2
    spec_path = Path(args.inputs).resolve()
    if not spec_path.is_file():
        print(f"error: input spec not found: {spec_path}", file=sys.stderr)
        return 2

    bob_dir = project_root / ".bob"
    install_handlers()

    # Acquire single-instance lock; release on shutdown.
    try:
        lock: Lock = acquire_lock(bob_dir)
    except LockHeld as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    except StalePidDetected as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    register_cleanup(lambda: release_lock(lock))

    # Inputs dir: copy or symlink the markdown spec into .bob/inputs/ so it's
    # captured for posterity. (M2 proper handles arbitrary multimodal inputs.)
    bob_dir.mkdir(parents=True, exist_ok=True)
    (bob_dir / "inputs").mkdir(exist_ok=True)
    captured = bob_dir / "inputs" / spec_path.name
    if captured.resolve() != spec_path.resolve():
        captured.write_bytes(spec_path.read_bytes())

    coord = build_coordinator(
        project_root=project_root,
        spec_path=spec_path,
        max_iterations=args.max_iterations,
        disabled_gates=set(args.no_gate),
    )
    coord.run(RunScope(includes_duplo=True))
    return 0
```

Make sure the existing imports at the top of the file stay (`argparse`, `sys`, `Path`, `install_handlers`, `read_json`). Add nothing at the top — the imports inside `_cmd_run` are intentional to keep `bob status` fast (no `wiring` / `coordinator` imports at module load).

- [ ] **Step 4: Run the new tests**

Run: `pytest tests/bob/test_cli.py -v`

Expected: PASS — at least the two new error-path tests, plus the 3 prior tests.

- [ ] **Step 5: Run full suite**

Run: `pytest -q`

Expected: All tests pass (180 expected).

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/cli.py tests/bob/test_cli.py
git commit -m "feat(bob): wire bob run to Coordinator (real M2a end-to-end)"
```

---

### Task 6: Full CLI integration test against a tiny demo project

**Files:**
- Create: `tests/bob/test_e2e_cli.py`

- [ ] **Step 1: Write the integration test**

`tests/bob/test_e2e_cli.py`:

```python
"""End-to-end test exercising `python -m claude_orchestrator.bob.cli run`.

This is the same shape as test_e2e_smoke.py but invoked through the CLI
boundary so we know the full subprocess path works. The fake `claude`
binary in PATH is the only stub.
"""
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Tiny project: git repo with one passing test."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "test_smoke.py").write_text(
        "def test_passes():\n    assert True\n"
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t.com",
         "-c", "user.name=T", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


@pytest.fixture
def fake_claude_dir(tmp_path_factory) -> Path:
    """A directory containing a fake `claude` script that emits EXIT_SIGNAL."""
    d = tmp_path_factory.mktemp("fake-claude-bin")
    script = d / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return d


def test_bob_run_against_tiny_project(
    project_root: Path, fake_claude_dir: Path, monkeypatch
):
    """`bob run --inputs spec.md` runs the full pipeline and merges one feature."""
    spec_path = project_root / "spec.md"
    spec_path.write_text(dedent("""\
        # CLI smoke
        ## Motivation
        Make sure bob run actually works end-to-end through the CLI.
        ## Features
        ### F1: passing-tests
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: Already implemented; the loop should exit on iteration 1.
    """))

    # Put fake claude on PATH; the wiring uses claude_cmd="claude" by default.
    env = os.environ.copy()
    env["PATH"] = str(fake_claude_dir) + os.pathsep + env["PATH"]

    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run",
         "--project", str(project_root),
         "--inputs", str(spec_path),
         "--max-iterations", "3",
         "--no-gate", "post_duplo"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert result.returncode == 0, (
        f"bob run failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # State was created.
    bob_dir = project_root / ".bob"
    assert bob_dir.exists()
    assert (bob_dir / "spec.md").exists()
    assert (bob_dir / "cursor.json").exists()
    feature_dirs = list((bob_dir / "features").iterdir())
    assert len(feature_dirs) == 1

    # Feature reached merged status.
    import json
    state = json.loads((feature_dirs[0] / "state.json").read_text())
    assert state["status"] == "merged", f"expected merged, got {state['status']}"

    # Worktree was cleaned up after merge.
    worktrees = list((bob_dir / "worktrees").iterdir()) if (bob_dir / "worktrees").exists() else []
    assert worktrees == [], f"expected no worktrees after merge, got {worktrees}"

    # Lock file should be gone.
    assert not (bob_dir / ".bob.lock").exists()
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/bob/test_e2e_cli.py -v`

Expected: PASS — full CLI path runs and the feature merges.

If the test fails because pytest in the workspace picks up the wrong tests, add a `tests/bob/test_e2e_cli_conftest.py` or constrain via `--rootdir` in the verifier (out of scope for M2a; the tmp_path workspace is already minimal).

- [ ] **Step 3: Run full suite**

Run: `pytest -q`

Expected: All tests pass (181 expected).

- [ ] **Step 4: Commit**

```bash
git add tests/bob/test_e2e_cli.py
git commit -m "test(bob): end-to-end CLI test exercising bob run subprocess path"
```

---

### Task 7: Manual smoke against this repo (optional polish)

**Files:**
- None (this is a smoke test of the dev experience, not a code change)

- [ ] **Step 1: Build a tiny throwaway markdown spec at `/tmp/bob-smoke-spec.md`**

```markdown
# Smoke test
## Motivation
Verify the bob CLI runs end-to-end against a real-ish workspace.
## Features
### F1: noop
- task_type: library
- verifier: python_pytest
- success_criteria:
  - existing tests stay green
- description: This feature is already implemented; the loop should exit on iteration 1.
```

- [ ] **Step 2: Set up a fake claude in PATH**

```bash
mkdir -p /tmp/bob-smoke-bin
cat > /tmp/bob-smoke-bin/claude <<'SH'
#!/bin/sh
echo "<promise>EXIT_SIGNAL</promise>"
SH
chmod +x /tmp/bob-smoke-bin/claude
export PATH="/tmp/bob-smoke-bin:$PATH"
```

- [ ] **Step 3: Run bob against this repo (or a copy)**

```bash
# In a SEPARATE checkout to avoid touching the real .bob/
git clone /Users/tobiaslunt/code/claude-orchestrator /tmp/bob-smoke-clone
cd /tmp/bob-smoke-clone
pip install -e .
bob run --project . --inputs /tmp/bob-smoke-spec.md --max-iterations 3 --no-gate post_duplo
bob status
```

- [ ] **Step 4: Verify and clean up**

Confirm: `bob status` shows `phase: idle` and one feature with `status=merged`. Then:

```bash
rm -rf /tmp/bob-smoke-clone /tmp/bob-smoke-spec.md /tmp/bob-smoke-bin
```

This is a manual sanity check; nothing to commit.

---

## Self-review

1. **Spec coverage:**
   - Final reviewer's #1 (Coordinator shutdown polling) → Task 2.
   - Final reviewer's #2 (CLI `_cmd_run` stub) → Task 5 (with composition in Task 4).
   - Final reviewer's #5 (`Pyhton's` typo) → Task 1.
   - Worktree create/remove (the M1 plan said "wired in by the CLI", which Task 3 makes concrete in Coordinator instead — slightly different from the plan's text but matches the spec §6.3 "Per-feature, in a fresh `git worktree`").

2. **Placeholder scan:**
   - Step 1 of Task 4 imports a fixture pattern from Task 3; if Task 3 changed the `project_root` fixture in `test_coordinator.py`, Task 4's wiring test re-creates a similar fixture inline. Acceptable duplication.
   - Task 5's `_cmd_run` uses `register_cleanup(lambda: release_lock(lock))` — the lambda captures `lock` by reference, which is correct.

3. **Type consistency:**
   - `build_coordinator` returns `Coordinator`; matches `_cmd_run` usage.
   - `AutoApproveJudge.judge_diff` returns `dict[str, Any]`; matches `SingleJudge` Protocol shape from `bob/orchestra/stub.py`.
   - `mcloop_callable` and `orchestra_callable` keyword arguments match Coordinator's expected signatures.

4. **Ambiguity check:**
   - **Worktree retention on failure/rejection:** intentional — `_run_feature` leaves the worktree alive on failure or rejection so the user can inspect. Documented inline.
   - **Lock release on Ctrl-C:** uses `register_cleanup` + `atexit`; works for normal exit and graceful shutdown. Force-exit (second SIGINT) calls `_run_cleanups` directly per signals.py, so the lock is still released. Tested implicitly by Task 6's CLI test (the lock file is cleaned up).
   - **Spec capture in `.bob/inputs/`:** uses `read_bytes`/`write_bytes` to handle non-UTF-8 inputs (e.g., when M2 adds PDFs). Idempotent (skips if same file).

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-05-07-bob-m2a-cli-wiring.md`. Same execution model as M1: subagent-driven, fresh subagent per task, two-stage review, continuous execution.
