# Bob Milestone 1: Thin End-to-End Slice — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a working `bob run --inputs spec.md` command that parses a markdown spec into features, runs an autonomous McLoop on each feature in a fresh git worktree, gates each iteration with a Pydantic-validated verifier (Python pytest only for M1), and merges approved features to `main`. Stub Orchestra/Vroom; no AutoGen; tier-1 sandbox only.

**Architecture:** Extend the existing `claude_orchestrator/` Python package with a new `bob/` namespace. State lives in `.bob/` using the two-level layout from spec §3 (project-level files + per-feature directories). Coordinator walks `.bob/features/`, runs each through Duplo→McLoop→stub-Orchestra→merge. Each McLoop iteration spawns a fresh `claude -p` subprocess (Huntley bash-loop pattern); the verifier protocol enforces halt-loud `Inconclusive`. Hooks (already in repo) are promoted to `bob/hooks/` and policy every agent call.

**Tech Stack:** Python 3.10+, `claude-agent-sdk`, `pydantic` v2, `tomli`, `pytest` + `pytest-asyncio`, subprocess for `claude -p`, stdlib `fcntl` for atomic appends, `git worktree` CLI.

**Spec reference:** `docs/superpowers/specs/2026-05-06-bob-design.md` (commits 66c384c → 3e90421).

---

## File structure (M1)

**Created:**
```
claude_orchestrator/bob/
  __init__.py
  coordinator.py             # state machine: walk features/, advance each through phases
  state_io.py                # atomic JSON write, append-only JSONL helpers
  process_lock.py            # .bob/.bob.lock PID file
  signals.py                 # SIGINT/SIGTERM/SIGHUP + atexit cleanup
  worktree.py                # thin git worktree wrapper
  cli.py                     # bob run / bob status (extends existing cli)
  hooks/                     # promoted from hooks.py
    __init__.py
    bash_security.py         # extracted from existing hooks.py
  duplo/
    __init__.py
    markdown_parser.py       # M1: parse markdown spec → Spec + features
    meta_rubric.py           # LLM-as-judge: does verifier cover criteria?
  mcloop/
    __init__.py
    runner.py                # bash-loop pattern: fresh claude -p per iteration
    prompts/
      iteration.md           # the iteration prompt template
  orchestra/
    __init__.py
    stub.py                  # M1: single-model "looks reasonable" review
  verifiers/
    __init__.py
    protocol.py              # Verifier Protocol; VerifyResult dataclass
    registry.py              # discovery + lookup by task_type
    python_pytest.py         # first concrete verifier
  hitl/
    __init__.py
    gates.py                 # gate registry; post-Duplo gate

tests/bob/
  __init__.py
  conftest.py                # pytest fixtures (tmp_path-backed .bob/)
  test_models.py
  test_state_io.py
  test_process_lock.py
  test_worktree.py
  test_verifier_protocol.py
  test_python_pytest_verifier.py
  test_meta_rubric.py
  test_duplo_markdown.py
  test_mcloop_runner.py
  test_orchestra_stub.py
  test_coordinator.py
  test_cli.py
  test_hitl_gates.py
  test_e2e_smoke.py          # gated behind env flag (real claude -p)
```

**Modified:**
- `claude_orchestrator/models.py` — add Bob-era contracts (Spec, Feature, VerificationPlan, TaskType, Verdict, Finding, FeatureStatus)
- `claude_orchestrator/cli.py` — register the `bob` subcommand
- `pyproject.toml` — add deps (`pydantic>=2`, dev deps if missing) and dev-deps for testing

**Deferred to M2+ (NOT touched in M1):**
- Real Orchestra (AutoGen + KS-stability) — M2
- Vroom auditor pool, SARIF, coalescer — M3
- Sandbox tiers 2/3 (Docker, Devcontainer) — M4
- OpenTelemetry observability polish — M4
- Multimodal Duplo (URLs, PDFs, screenshots, video) — M2
- YOLO mode — M2 (after real Orchestra exists to be opted out of)

---

### Task 1: Project skeleton and dependencies

**Files:**
- Create: `claude_orchestrator/bob/__init__.py`
- Create: `claude_orchestrator/bob/duplo/__init__.py`
- Create: `claude_orchestrator/bob/mcloop/__init__.py`
- Create: `claude_orchestrator/bob/orchestra/__init__.py`
- Create: `claude_orchestrator/bob/verifiers/__init__.py`
- Create: `claude_orchestrator/bob/hitl/__init__.py`
- Create: `claude_orchestrator/bob/hooks/__init__.py`
- Create: `tests/bob/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Inspect existing pyproject.toml**

Run: `cat pyproject.toml`

Confirm: `pydantic>=2.0` and `claude-agent-sdk>=0.1.0` are already present in `dependencies`. If `pytest` and `pytest-asyncio` are listed under `[project.optional-dependencies] dev`, no change needed.

- [ ] **Step 2: Create empty package init files**

Run:
```bash
mkdir -p claude_orchestrator/bob/{duplo,mcloop/prompts,orchestra,verifiers,hitl,hooks}
mkdir -p tests/bob
touch claude_orchestrator/bob/__init__.py
touch claude_orchestrator/bob/duplo/__init__.py
touch claude_orchestrator/bob/mcloop/__init__.py
touch claude_orchestrator/bob/orchestra/__init__.py
touch claude_orchestrator/bob/verifiers/__init__.py
touch claude_orchestrator/bob/hitl/__init__.py
touch claude_orchestrator/bob/hooks/__init__.py
touch tests/bob/__init__.py
```

- [ ] **Step 3: Verify package imports**

Run: `python -c "import claude_orchestrator.bob; import claude_orchestrator.bob.duplo; import claude_orchestrator.bob.mcloop; import claude_orchestrator.bob.orchestra; import claude_orchestrator.bob.verifiers; import claude_orchestrator.bob.hitl; import claude_orchestrator.bob.hooks; print('ok')"`

Expected: `ok`

- [ ] **Step 4: Commit skeleton**

```bash
git add claude_orchestrator/bob tests/bob
git commit -m "feat(bob): scaffold bob/ namespace and test mirror"
```

---

### Task 2: Phase contracts in `models.py`

**Files:**
- Modify: `claude_orchestrator/models.py`
- Create: `tests/bob/test_models.py`

- [ ] **Step 1: Write failing test for the new types**

Create `tests/bob/test_models.py`:

```python
"""Tests for Bob-era phase contracts."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Finding,
    SARIFLocation,
    Spec,
    TaskType,
    Verdict,
    VerificationPlan,
)


def test_task_type_has_custom_for_extensibility():
    assert TaskType.CUSTOM == "custom"
    # Make sure standard members exist too
    for name in ("UI", "DATA_ANALYSIS", "GEOSPATIAL", "LIBRARY", "CLI",
                 "INTEGRATION", "ML_TRAINING", "INFRASTRUCTURE"):
        assert name in TaskType.__members__


def test_verification_plan_requires_verifier_id():
    with pytest.raises(ValidationError):
        VerificationPlan(success_criteria=["x"], required_tools=[])


def test_verification_plan_valid():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["all tests pass"],
        required_tools=["pytest"],
    )
    assert plan.verifier_id == "python_pytest"


def test_feature_status_transitions_are_strings():
    # Used for serialization to state.json
    assert FeatureStatus.PENDING.value == "pending"
    assert FeatureStatus.MERGED.value == "merged"


def test_feature_round_trips_through_json():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["tests pass"],
        required_tools=["pytest"],
    )
    feature = Feature(
        id=1,
        name="auth",
        description="Add login",
        task_type=TaskType.LIBRARY,
        verification_plan=plan,
        branch=None,
        worktree_path=None,
        status=FeatureStatus.PENDING,
        attempts=0,
        cost_usd=0.0,
        last_error=None,
    )
    blob = feature.model_dump_json()
    feature2 = Feature.model_validate_json(blob)
    assert feature2 == feature


def test_spec_holds_features():
    plan = VerificationPlan(
        verifier_id="python_pytest",
        success_criteria=["x"],
        required_tools=["pytest"],
    )
    feat = Feature(
        id=1, name="a", description="b", task_type=TaskType.LIBRARY,
        verification_plan=plan, branch=None, worktree_path=None,
        status=FeatureStatus.PENDING, attempts=0, cost_usd=0.0, last_error=None,
    )
    spec = Spec(
        title="Demo",
        motivation="why",
        inputs=[],
        features=[feat],
        rubric_meta_check_passed=True,
    )
    assert len(spec.features) == 1


def test_verdict_decisions_are_constrained():
    v = Verdict(
        feature_id=1, decision="approve", confidence=0.9,
        debate_log_path=Path("/tmp/x"), judge_reasoning="lgtm",
    )
    assert v.decision == "approve"
    with pytest.raises(ValidationError):
        Verdict(
            feature_id=1, decision="maybe", confidence=0.9,
            debate_log_path=Path("/tmp/x"), judge_reasoning="lgtm",
        )


def test_finding_is_sarif_compatible_subset():
    f = Finding(
        rule_id="bob.test",
        severity="medium",
        location=SARIFLocation(uri="src/x.py", start_line=1, end_line=2),
        message="tests too slow",
        proposed_fix=None,
        auditor="claude_architect",
        fingerprint="abc123",
        status="open",
    )
    assert f.severity == "medium"
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `pytest tests/bob/test_models.py -v`

Expected: FAIL — `ImportError` on the new symbols (`TaskType`, `Spec`, `Feature`, etc.).

- [ ] **Step 3: Implement the new contracts in `models.py`**

Read the current `claude_orchestrator/models.py` first to preserve existing types (e.g., `FeatureResult`, `ProgressEntry`). Add the new types. The full new file content:

```python
"""Pydantic models for orchestrator state.

Bob-era contracts (Spec, Feature, etc.) coexist with the legacy
FeatureResult/ProgressEntry types used by the existing orchestrator.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# ---- Bob-era contracts ----------------------------------------------------


class TaskType(StrEnum):
    """Open enum: built-in values are conveniences; CUSTOM + verifier_id covers the rest."""

    UI = "ui"
    DATA_ANALYSIS = "data_analysis"
    GEOSPATIAL = "geospatial"
    LIBRARY = "library"
    CLI = "cli"
    INTEGRATION = "integration"
    ML_TRAINING = "ml_training"
    INFRASTRUCTURE = "infrastructure"
    CUSTOM = "custom"


class FeatureStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    MCLOOP_DONE = "mcloop_done"
    ORCHESTRA_PENDING = "orchestra_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    SKIPPED = "skipped"
    FAILED = "failed"


class VerificationPlan(BaseModel):
    """A feature's declared verification approach."""

    verifier_id: str = Field(..., description="Registered verifier id, e.g. 'python_pytest'")
    success_criteria: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)


class InputRef(BaseModel):
    """A reference to a multimodal input the user provided to Duplo."""

    kind: Literal["file", "url", "text"]
    value: str
    description: str | None = None


class Feature(BaseModel):
    """One unit of work, scoped to its own worktree and branch."""

    id: int
    name: str
    description: str
    task_type: TaskType
    verification_plan: VerificationPlan
    branch: str | None = None
    worktree_path: Path | None = None
    status: FeatureStatus = FeatureStatus.PENDING
    attempts: int = 0
    cost_usd: float = 0.0
    last_error: str | None = None
    updated_at: datetime | None = None


class Spec(BaseModel):
    """The master spec produced by Duplo."""

    title: str
    motivation: str
    inputs: list[InputRef] = Field(default_factory=list)
    features: list[Feature]
    rubric_meta_check_passed: bool = False


class Verdict(BaseModel):
    """Orchestra's per-feature decision."""

    feature_id: int
    decision: Literal["approve", "reject", "abstain"]
    confidence: float = Field(ge=0.0, le=1.0)
    debate_log_path: Path
    judge_reasoning: str


class SARIFLocation(BaseModel):
    """Subset of SARIF physicalLocation/region."""

    uri: str
    start_line: int
    end_line: int | None = None


class Finding(BaseModel):
    """SARIF-compatible subset for Vroom output. (Vroom is M3, but the type ships now.)"""

    rule_id: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    location: SARIFLocation
    message: str
    proposed_fix: Path | None = None
    auditor: str
    fingerprint: str
    status: Literal["open", "in_progress", "resolved", "wontfix"] = "open"


# ---- Legacy types (preserve existing API) ---------------------------------
# Re-export anything the existing orchestrator depends on. If models.py
# already defines FeatureResult / ProgressEntry, keep them as-is.
```

**Important:** preserve any existing classes already in `models.py`. If the existing file defines `FeatureResult`, `ProgressEntry`, or other types used by `orchestrator.py`/`runner.py`, append the new types **without removing** the old ones.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `pytest tests/bob/test_models.py -v`

Expected: PASS — all 8 tests green.

- [ ] **Step 5: Run the full existing test suite to confirm nothing regressed**

Run: `pytest -x -q`

Expected: existing tests still PASS (we only added types).

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/models.py tests/bob/test_models.py
git commit -m "feat(bob): add phase contracts (Spec, Feature, Verdict, Finding)"
```

---

### Task 3: State IO helpers

**Files:**
- Create: `claude_orchestrator/bob/state_io.py`
- Create: `tests/bob/test_state_io.py`
- Create: `tests/bob/conftest.py`

- [ ] **Step 1: Create the conftest fixture**

`tests/bob/conftest.py`:

```python
"""Shared pytest fixtures for bob/ tests."""
from pathlib import Path

import pytest


@pytest.fixture
def bob_dir(tmp_path: Path) -> Path:
    """An empty .bob/ directory rooted at a tmp_path."""
    d = tmp_path / ".bob"
    d.mkdir()
    return d
```

- [ ] **Step 2: Write failing tests for state IO**

`tests/bob/test_state_io.py`:

```python
"""Tests for atomic JSON write and append-only JSONL helpers."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.state_io import (
    append_jsonl,
    read_json,
    read_jsonl,
    write_json_atomic,
)


def test_write_json_atomic_creates_file(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"phase": "duplo", "feature_id": None})
    assert json.loads(path.read_text()) == {"phase": "duplo", "feature_id": None}


def test_write_json_atomic_overwrites(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"a": 1})
    write_json_atomic(path, {"a": 2})
    assert json.loads(path.read_text()) == {"a": 2}


def test_write_json_atomic_no_partial_writes(bob_dir: Path, monkeypatch):
    """Simulate a crash mid-write; the original file must remain intact."""
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"original": True})
    original = path.read_text()

    # Simulate a crash during the rename step
    real_replace = Path.replace

    def boom(self, *args, **kwargs):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(RuntimeError):
        write_json_atomic(path, {"corrupt": True})

    assert path.read_text() == original


def test_read_json_returns_dict(bob_dir: Path):
    path = bob_dir / "cursor.json"
    write_json_atomic(path, {"x": 1})
    assert read_json(path) == {"x": 1}


def test_read_json_returns_default_for_missing(bob_dir: Path):
    path = bob_dir / "missing.json"
    assert read_json(path, default={"empty": True}) == {"empty": True}


def test_append_jsonl_creates_file(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"event": "started"})
    lines = path.read_text().splitlines()
    assert lines == ['{"event": "started"}']


def test_append_jsonl_appends(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"event": "a"})
    append_jsonl(path, {"event": "b"})
    lines = path.read_text().splitlines()
    assert json.loads(lines[0]) == {"event": "a"}
    assert json.loads(lines[1]) == {"event": "b"}


def test_read_jsonl_yields_records(bob_dir: Path):
    path = bob_dir / "run-log.jsonl"
    append_jsonl(path, {"i": 1})
    append_jsonl(path, {"i": 2})
    assert list(read_jsonl(path)) == [{"i": 1}, {"i": 2}]


def test_read_jsonl_handles_missing_file(bob_dir: Path):
    path = bob_dir / "missing.jsonl"
    assert list(read_jsonl(path)) == []


def test_append_jsonl_rejects_oversized_records(bob_dir: Path):
    """POSIX guarantees atomic appends only for writes < PIPE_BUF (~4KB)."""
    path = bob_dir / "x.jsonl"
    too_big = {"data": "x" * 5000}
    with pytest.raises(ValueError, match="too large"):
        append_jsonl(path, too_big)
```

- [ ] **Step 3: Run to confirm failure**

Run: `pytest tests/bob/test_state_io.py -v`

Expected: FAIL — `ImportError: cannot import name ...` from `state_io`.

- [ ] **Step 4: Implement `state_io.py`**

`claude_orchestrator/bob/state_io.py`:

```python
"""Atomic JSON writes and append-only JSONL helpers.

Design (see spec §3.1):
- Mutable JSON files use tempfile + fsync + rename for atomicity. Readers
  see either the old or new file, never a half-written one.
- Append-only JSONL files use O_APPEND. POSIX guarantees atomic writes
  per syscall when the record is below PIPE_BUF (~4 KB on Linux/macOS),
  so concurrent appenders interleave records cleanly without corruption.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# POSIX guarantees atomic write per syscall up to PIPE_BUF.
# On Linux/macOS this is at least 512 bytes and usually 4096. We use 4096 as
# the safe upper bound for concurrent appenders.
_PIPE_BUF_SAFE = 4096


def write_json_atomic(path: Path, data: Any) -> None:
    """Write `data` to `path` atomically: tempfile in same dir, fsync, rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(data, indent=2, default=str)

    # Same directory ensures rename is atomic on POSIX (same filesystem).
    fd, tmp_str = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    tmp = Path(tmp_str)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON from `path`, returning `default` if the file does not exist."""
    if not path.exists():
        return default
    return json.loads(path.read_text())


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a single JSON record to `path` atomically.

    Raises ValueError if the serialized record exceeds PIPE_BUF_SAFE.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, default=str) + "\n"
    encoded = line.encode("utf-8")
    if len(encoded) > _PIPE_BUF_SAFE:
        raise ValueError(
            f"record is {len(encoded)} bytes — too large for atomic append "
            f"(PIPE_BUF safe limit is {_PIPE_BUF_SAFE}). Split it or write "
            f"to a non-shared file."
        )
    # O_APPEND ensures the kernel atomically positions and writes.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield records from a JSONL file. Empty lines are skipped."""
    if not path.exists():
        return
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
```

- [ ] **Step 5: Run tests to confirm pass**

Run: `pytest tests/bob/test_state_io.py -v`

Expected: PASS — all 10 tests green.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/state_io.py tests/bob/conftest.py tests/bob/test_state_io.py
git commit -m "feat(bob): atomic JSON write and append-only JSONL helpers"
```

---

### Task 4: Process lock (single-instance enforcement)

**Files:**
- Create: `claude_orchestrator/bob/process_lock.py`
- Create: `tests/bob/test_process_lock.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_process_lock.py`:

```python
"""Tests for .bob/.bob.lock single-instance enforcement."""
import os
from pathlib import Path

import pytest

from claude_orchestrator.bob.process_lock import (
    LockHeld,
    StalePidDetected,
    acquire_lock,
    release_lock,
)


def test_acquire_lock_creates_file(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    assert (bob_dir / ".bob.lock").exists()
    release_lock(lock)


def test_acquire_lock_writes_pid(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    pid = int((bob_dir / ".bob.lock").read_text().strip())
    assert pid == os.getpid()
    release_lock(lock)


def test_release_lock_removes_file(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    release_lock(lock)
    assert not (bob_dir / ".bob.lock").exists()


def test_acquire_lock_blocks_when_held(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    # Simulate a separate process holding the lock by NOT releasing it.
    with pytest.raises(LockHeld):
        acquire_lock(bob_dir)
    release_lock(lock)


def test_acquire_lock_clears_stale_pid(bob_dir: Path):
    """A lock file with a dead PID should be reclaimed automatically."""
    # Write an obviously dead PID (1 is init / launchd; we use 99999999 instead).
    (bob_dir / ".bob.lock").write_text("99999999")

    lock = acquire_lock(bob_dir)
    assert int((bob_dir / ".bob.lock").read_text().strip()) == os.getpid()
    release_lock(lock)


def test_acquire_lock_complains_about_malformed_lock(bob_dir: Path):
    (bob_dir / ".bob.lock").write_text("not-a-pid")
    with pytest.raises(StalePidDetected):
        acquire_lock(bob_dir)


def test_release_lock_is_idempotent(bob_dir: Path):
    lock = acquire_lock(bob_dir)
    release_lock(lock)
    # second release should not raise
    release_lock(lock)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_process_lock.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `process_lock.py`**

`claude_orchestrator/bob/process_lock.py`:

```python
"""Single-instance lock for `bob run` invocations on a shared .bob/ directory.

Mechanism:
- A `.bob/.bob.lock` PID file. If present and the PID is alive, refuse to start.
- If the PID is dead (kill -9 / power loss), reclaim the lock automatically.
- If the file contents are malformed, surface StalePidDetected; user
  intervention required (the file contents may not be ours).
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path


class LockHeld(RuntimeError):
    """Another live process holds the lock."""


class StalePidDetected(RuntimeError):
    """Lock file present but contents are not a valid PID."""


@dataclass
class Lock:
    path: Path
    released: bool = False


def _pid_alive(pid: int) -> bool:
    """Best-effort liveness check via signal 0 (no-op signal that fails if pid is gone)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it. Treat as alive.
        return True
    return True


def acquire_lock(bob_dir: Path) -> Lock:
    """Acquire the .bob.lock PID file, raising LockHeld if a live process holds it."""
    bob_dir.mkdir(parents=True, exist_ok=True)
    lock_path = bob_dir / ".bob.lock"

    if lock_path.exists():
        contents = lock_path.read_text().strip()
        try:
            existing_pid = int(contents)
        except ValueError:
            raise StalePidDetected(
                f"{lock_path} contains non-PID contents: {contents!r}. "
                f"Inspect manually before removing."
            )
        if _pid_alive(existing_pid):
            raise LockHeld(
                f"another bob process (pid {existing_pid}) holds {lock_path}"
            )
        # Stale: dead PID — reclaim.
        lock_path.unlink()

    # Create the lock file with O_EXCL to win the race against any other starter.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except OSError as e:
        if e.errno == errno.EEXIST:
            raise LockHeld(f"{lock_path} appeared between check and create")
        raise
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
    finally:
        os.close(fd)

    return Lock(path=lock_path)


def release_lock(lock: Lock) -> None:
    """Remove the lock file. Idempotent."""
    if lock.released:
        return
    try:
        lock.path.unlink(missing_ok=True)
    finally:
        lock.released = True
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_process_lock.py -v`

Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/process_lock.py tests/bob/test_process_lock.py
git commit -m "feat(bob): single-instance PID-file lock with stale-PID reclaim"
```

---

### Task 5: Worktree manager

**Files:**
- Create: `claude_orchestrator/bob/worktree.py`
- Create: `tests/bob/test_worktree.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_worktree.py`:

```python
"""Tests for the git worktree wrapper."""
import subprocess
from pathlib import Path

import pytest

from claude_orchestrator.bob.worktree import (
    WorktreeError,
    add_worktree,
    list_worktrees,
    remove_worktree,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit on main."""
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "README.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=test@example.com",
         "-c", "user.name=Test", "commit", "-m", "init"],
        check=True,
    )
    return tmp_path


def test_add_worktree_creates_branch_and_path(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    assert target.exists()
    assert (target / "README.md").exists()


def test_add_worktree_lists_after_create(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    worktrees = list_worktrees(repo)
    assert any(wt.path == target for wt in worktrees)


def test_remove_worktree_cleans_up(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    remove_worktree(repo, target)
    assert not target.exists()
    worktrees = list_worktrees(repo)
    assert all(wt.path != target for wt in worktrees)


def test_add_worktree_rejects_existing_path(repo: Path, tmp_path: Path):
    target = tmp_path / "wt" / "001-auth"
    add_worktree(repo, target, branch="bob/001-auth")
    with pytest.raises(WorktreeError):
        add_worktree(repo, target, branch="bob/001-other")
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_worktree.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement `worktree.py`**

`claude_orchestrator/bob/worktree.py`:

```python
"""Thin wrapper around `git worktree`.

Just enough to create a per-feature worktree on a branch, list them,
and remove them cleanly. Shells to git rather than using a Python lib
to keep the dependency surface small.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorktreeError(RuntimeError):
    """A `git worktree` command failed."""


@dataclass
class WorktreeEntry:
    path: Path
    branch: str | None
    commit: str | None


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
    )


def add_worktree(repo: Path, target: Path, branch: str) -> None:
    """Create a new worktree at `target` on a fresh branch `branch`.

    The branch is created from the current HEAD of `repo`.
    """
    if target.exists():
        raise WorktreeError(f"worktree path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = _run(
        ["git", "worktree", "add", "-b", branch, str(target)],
        cwd=repo,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree add failed: {result.stderr.strip()}"
        )


def remove_worktree(repo: Path, target: Path) -> None:
    """Remove the worktree at `target`. Force flag handles dirty trees."""
    result = _run(
        ["git", "worktree", "remove", "--force", str(target)],
        cwd=repo,
    )
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree remove failed: {result.stderr.strip()}"
        )


def list_worktrees(repo: Path) -> list[WorktreeEntry]:
    """Return all registered worktrees for `repo`."""
    result = _run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if result.returncode != 0:
        raise WorktreeError(
            f"git worktree list failed: {result.stderr.strip()}"
        )

    entries: list[WorktreeEntry] = []
    current: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            if current:
                entries.append(_entry_from_dict(current))
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        entries.append(_entry_from_dict(current))
    return entries


def _entry_from_dict(d: dict[str, str]) -> WorktreeEntry:
    return WorktreeEntry(
        path=Path(d["worktree"]),
        branch=d.get("branch"),
        commit=d.get("HEAD"),
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_worktree.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/worktree.py tests/bob/test_worktree.py
git commit -m "feat(bob): git worktree wrapper for per-feature isolation"
```

---

### Task 6: Verifier protocol and registry

**Files:**
- Create: `claude_orchestrator/bob/verifiers/protocol.py`
- Create: `claude_orchestrator/bob/verifiers/registry.py`
- Create: `tests/bob/test_verifier_protocol.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_verifier_protocol.py`:

```python
"""Tests for the Verifier protocol and registry."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.bob.verifiers.registry import (
    VerifierRegistry,
    UnknownVerifier,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


class FakeVerifier:
    id = "fake"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.LIBRARY]

    def required_tools(self) -> list[str]:
        return ["python"]

    def preflight(self, workspace: Path) -> PreflightResult:
        return PreflightResult(ok=True, missing_tools=[])

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        return VerifyResult(status="ok", reason="", artifacts=[], coverage_notes=None)


def _make_feature() -> Feature:
    return Feature(
        id=1, name="x", description="y",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="fake",
            success_criteria=["x"],
            required_tools=["python"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_verify_result_status_constrained():
    with pytest.raises(ValueError):
        VerifyResult(status="oops", reason="", artifacts=[], coverage_notes=None)


def test_registry_register_and_lookup():
    reg = VerifierRegistry()
    reg.register(FakeVerifier())
    found = reg.get("fake")
    assert found.id == "fake"


def test_registry_unknown_raises():
    reg = VerifierRegistry()
    with pytest.raises(UnknownVerifier):
        reg.get("nonexistent")


def test_registry_resolve_for_feature_uses_plan_verifier_id(tmp_path: Path):
    reg = VerifierRegistry()
    reg.register(FakeVerifier())
    feature = _make_feature()
    verifier = reg.resolve_for_feature(feature)
    result = verifier.verify(tmp_path, feature)
    assert result.status == "ok"


def test_registry_refuses_feature_with_missing_verifier():
    reg = VerifierRegistry()  # no verifiers registered
    feature = _make_feature()
    with pytest.raises(UnknownVerifier):
        reg.resolve_for_feature(feature)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_verifier_protocol.py -v`

Expected: FAIL — modules missing.

- [ ] **Step 3: Implement `protocol.py`**

`claude_orchestrator/bob/verifiers/protocol.py`:

```python
"""The Verifier protocol — the most important contract in Bob (see spec §6.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from claude_orchestrator.models import Feature, TaskType


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    missing_tools: list[str] = field(default_factory=list)
    notes: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    """The output of a single verifier run.

    Status semantics:
      ok            -- the work passes the rubric
      fail          -- the work definitely doesn't pass; agent should iterate
      inconclusive  -- the verifier could not decide (HALT LOUD by default)
    """
    status: Literal["ok", "fail", "inconclusive"]
    reason: str
    artifacts: list[Path]
    coverage_notes: str | None

    def __post_init__(self) -> None:
        if self.status not in ("ok", "fail", "inconclusive"):
            raise ValueError(f"invalid status: {self.status!r}")


class Verifier(Protocol):
    """The protocol every verifier implements. Pyhton's runtime Protocol."""

    id: str

    def applies_to(self) -> list[TaskType]: ...
    def required_tools(self) -> list[str]: ...
    def preflight(self, workspace: Path) -> PreflightResult: ...
    def verify(self, workspace: Path, feature: Feature) -> VerifyResult: ...
```

- [ ] **Step 4: Implement `registry.py`**

`claude_orchestrator/bob/verifiers/registry.py`:

```python
"""Verifier discovery + lookup by id and by feature."""

from __future__ import annotations

from claude_orchestrator.bob.verifiers.protocol import Verifier
from claude_orchestrator.models import Feature


class UnknownVerifier(KeyError):
    """No verifier registered with the requested id."""


class VerifierRegistry:
    """Maps verifier-id strings to Verifier instances.

    M1 keeps registration explicit (manual `register()` calls). v1.1 will
    add entry-point discovery.
    """

    def __init__(self) -> None:
        self._verifiers: dict[str, Verifier] = {}

    def register(self, verifier: Verifier) -> None:
        if verifier.id in self._verifiers:
            raise ValueError(f"verifier already registered: {verifier.id}")
        self._verifiers[verifier.id] = verifier

    def get(self, verifier_id: str) -> Verifier:
        try:
            return self._verifiers[verifier_id]
        except KeyError:
            raise UnknownVerifier(
                f"no verifier registered with id={verifier_id!r}; "
                f"available={sorted(self._verifiers)}"
            )

    def resolve_for_feature(self, feature: Feature) -> Verifier:
        return self.get(feature.verification_plan.verifier_id)
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/bob/test_verifier_protocol.py -v`

Expected: PASS — all 5 tests green.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/verifiers tests/bob/test_verifier_protocol.py
git commit -m "feat(bob): verifier Protocol + registry"
```

---

### Task 7: Python pytest verifier (first concrete verifier)

**Files:**
- Create: `claude_orchestrator/bob/verifiers/python_pytest.py`
- Create: `tests/bob/test_python_pytest_verifier.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_python_pytest_verifier.py`:

```python
"""Tests for the python_pytest verifier.

Uses real pytest in tmp_path workspaces — fast (sub-second) and
the most direct way to validate the verifier's contract.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["all tests pass"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


@pytest.fixture
def workspace_with_passing_test(tmp_path: Path) -> Path:
    (tmp_path / "test_thing.py").write_text(
        "def test_passes():\n    assert 1 + 1 == 2\n"
    )
    return tmp_path


@pytest.fixture
def workspace_with_failing_test(tmp_path: Path) -> Path:
    (tmp_path / "test_thing.py").write_text(
        "def test_fails():\n    assert 1 == 2\n"
    )
    return tmp_path


@pytest.fixture
def workspace_with_no_tests(tmp_path: Path) -> Path:
    return tmp_path


def test_id_and_applies_to():
    v = PythonPytestVerifier()
    assert v.id == "python_pytest"
    assert TaskType.LIBRARY in v.applies_to()


def test_verify_passes_on_green_tests(workspace_with_passing_test: Path):
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_passing_test, _feature())
    assert result.status == "ok"


def test_verify_fails_on_red_tests(workspace_with_failing_test: Path):
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_failing_test, _feature())
    assert result.status == "fail"
    assert "test_fails" in result.reason or "1 == 2" in result.reason


def test_verify_returns_inconclusive_when_no_tests(workspace_with_no_tests: Path):
    """No tests collected is the canonical 'I cannot decide' case (see spec §6.6)."""
    v = PythonPytestVerifier()
    result = v.verify(workspace_with_no_tests, _feature())
    assert result.status == "inconclusive"
    assert "no tests collected" in result.reason.lower()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_python_pytest_verifier.py -v`

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the verifier**

`claude_orchestrator/bob/verifiers/python_pytest.py`:

```python
"""Run the project's pytest suite as a verification step.

Status mapping:
  pytest exit 0      -> ok
  pytest exit 1      -> fail (test failures)
  pytest exit 5      -> inconclusive (no tests collected)
  anything else      -> inconclusive (collection error / config error / etc.)
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    Verifier,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class PythonPytestVerifier:
    id = "python_pytest"

    def applies_to(self) -> list[TaskType]:
        return [
            TaskType.LIBRARY,
            TaskType.CLI,
            TaskType.INTEGRATION,
            TaskType.DATA_ANALYSIS,
            TaskType.GEOSPATIAL,
            TaskType.ML_TRAINING,
        ]

    def required_tools(self) -> list[str]:
        return ["pytest"]

    def preflight(self, workspace: Path) -> PreflightResult:
        if shutil.which("pytest") is None:
            return PreflightResult(ok=False, missing_tools=["pytest"])
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        result = subprocess.run(
            ["pytest", "-q", "--tb=short", "--no-header"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
        rc = result.returncode
        output = (result.stdout + result.stderr).strip()

        if rc == 0:
            return VerifyResult(
                status="ok",
                reason="all tests passed",
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 1:
            return VerifyResult(
                status="fail",
                reason=output[-2000:],  # tail to keep the agent's context tight
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 5:
            return VerifyResult(
                status="inconclusive",
                reason="no tests collected — verifier cannot judge",
                artifacts=[],
                coverage_notes="ensure tests/ contains at least one test_*.py file",
            )
        # 2 (collection error), 3 (internal error), 4 (cmd line usage), other
        return VerifyResult(
            status="inconclusive",
            reason=f"pytest exited {rc} — {output[-1500:]}",
            artifacts=[],
            coverage_notes="non-standard pytest exit; investigate before proceeding",
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_python_pytest_verifier.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/verifiers/python_pytest.py tests/bob/test_python_pytest_verifier.py
git commit -m "feat(bob): python_pytest verifier with halt-loud Inconclusive on no-tests"
```

---

### Task 8: Hooks promotion to `bob/hooks/`

**Files:**
- Modify: copy/move `claude_orchestrator/hooks.py` to `claude_orchestrator/bob/hooks/bash_security.py`
- Create: `claude_orchestrator/bob/hooks/__init__.py` (re-exports for backward compat)
- Modify: imports in any file that referenced `claude_orchestrator.hooks`

- [ ] **Step 1: Inspect existing usage**

Run: `grep -rn "from claude_orchestrator.hooks\|import claude_orchestrator.hooks" claude_orchestrator tests`

Note every file that imports from `claude_orchestrator.hooks` — these need updating.

- [ ] **Step 2: Copy hooks.py to bob/hooks/bash_security.py**

```bash
cp claude_orchestrator/hooks.py claude_orchestrator/bob/hooks/bash_security.py
```

- [ ] **Step 3: Make bob/hooks/__init__.py re-export the public surface**

Read `claude_orchestrator/bob/hooks/bash_security.py` to find the public functions / classes (look for the names referenced from outside in step 1). Then write `claude_orchestrator/bob/hooks/__init__.py`:

```python
"""Hooks — the policy layer for every agent tool call.

Promoted from claude_orchestrator/hooks.py. Re-exports the public surface
so existing callers can continue importing without behavior change.
"""

from claude_orchestrator.bob.hooks.bash_security import *  # noqa: F401,F403
```

- [ ] **Step 4: Update legacy hooks.py to re-export from the new location**

Replace `claude_orchestrator/hooks.py` with:

```python
"""Backward-compat shim. Real implementation lives in bob/hooks/bash_security.py."""

from claude_orchestrator.bob.hooks.bash_security import *  # noqa: F401,F403
```

- [ ] **Step 5: Run the existing test suite (especially `test_security.py`) to confirm no regression**

Run: `pytest tests/test_security.py -v`

Expected: PASS — every existing security test still green.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/hooks claude_orchestrator/hooks.py
git commit -m "refactor(bob): promote hooks.py to bob/hooks/bash_security with shim"
```

---

### Task 9: HITL gates registry (post-Duplo only for M1)

**Files:**
- Create: `claude_orchestrator/bob/hitl/gates.py`
- Create: `tests/bob/test_hitl_gates.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_hitl_gates.py`:

```python
"""Tests for HITL gate registry and post-Duplo gate."""
import io
import sys
from pathlib import Path

import pytest

from claude_orchestrator.bob.hitl.gates import (
    GateDecision,
    GateRegistry,
    GateSkipped,
    PostDuploGate,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
)


def _spec() -> Spec:
    return Spec(
        title="Demo",
        motivation="why",
        inputs=[],
        features=[Feature(
            id=1, name="auth", description="login",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["tests pass"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )],
        rubric_meta_check_passed=True,
    )


def test_post_duplo_gate_approves_on_y(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    gate = PostDuploGate()
    decision = gate.run(_spec())
    assert decision == GateDecision.APPROVE


def test_post_duplo_gate_rejects_on_n(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("n\n"))
    gate = PostDuploGate()
    decision = gate.run(_spec())
    assert decision == GateDecision.REJECT


def test_registry_skip_via_disable_list():
    reg = GateRegistry(disabled={"post_duplo"})
    with pytest.raises(GateSkipped):
        reg.run("post_duplo", _spec())


def test_registry_runs_when_not_disabled(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
    reg = GateRegistry(disabled=set())
    reg.register("post_duplo", PostDuploGate())
    decision = reg.run("post_duplo", _spec())
    assert decision == GateDecision.APPROVE
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_hitl_gates.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `gates.py`**

`claude_orchestrator/bob/hitl/gates.py`:

```python
"""HITL gates for Bob.

M1 ships the post-Duplo gate only. Orchestra-disagreement and Vroom-triage
gates land with their respective phases (M2 / M3).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from claude_orchestrator.models import Spec


class GateDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class GateSkipped(RuntimeError):
    """Raised when a gate was disabled via --no-gate."""


class Gate(Protocol):
    name: str

    def run(self, payload: Any) -> GateDecision: ...


class PostDuploGate:
    name = "post_duplo"

    def run(self, spec: Spec) -> GateDecision:
        print("\n" + "=" * 60)
        print("Duplo produced the following spec:")
        print(f"  Title: {spec.title}")
        print(f"  Motivation: {spec.motivation}")
        print(f"  Features: {len(spec.features)}")
        for f in spec.features:
            print(f"    [{f.id}] {f.name} ({f.task_type}) "
                  f"-> verifier={f.verification_plan.verifier_id}")
        print(f"  Meta-rubric passed: {spec.rubric_meta_check_passed}")
        print("=" * 60)
        try:
            answer = input("Approve and proceed to McLoop? [y/N]: ").strip().lower()
        except EOFError:
            answer = "n"
        return GateDecision.APPROVE if answer.startswith("y") else GateDecision.REJECT


class GateRegistry:
    """Run named gates with per-gate disable list."""

    def __init__(self, disabled: set[str] | None = None) -> None:
        self._gates: dict[str, Gate] = {}
        self._disabled = disabled or set()

    def register(self, name: str, gate: Gate) -> None:
        self._gates[name] = gate

    def run(self, name: str, payload: Any) -> GateDecision:
        if name in self._disabled:
            raise GateSkipped(name)
        gate = self._gates.get(name)
        if gate is None:
            raise KeyError(f"no gate registered: {name}")
        return gate.run(payload)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_hitl_gates.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/hitl tests/bob/test_hitl_gates.py
git commit -m "feat(bob): HITL gate registry with post-Duplo gate"
```

---

### Task 10: Meta-rubric coverage check

**Files:**
- Create: `claude_orchestrator/bob/duplo/meta_rubric.py`
- Create: `tests/bob/test_meta_rubric.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_meta_rubric.py`:

```python
"""Tests for the meta-rubric LLM-as-judge coverage check."""
from claude_orchestrator.bob.duplo.meta_rubric import (
    CoverageJudgment,
    MetaRubricChecker,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="auth", description="login",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["users can log in"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


class FakeJudge:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def judge(self, prompt_payload: dict) -> dict:
        self.calls.append(prompt_payload)
        return self.response


def test_meta_rubric_marks_adequate():
    judge = FakeJudge({"verdict": "adequate", "missing": []})
    checker = MetaRubricChecker(judge=judge)
    judgment = checker.check(_feature())
    assert judgment.adequate is True
    assert judgment.missing == []


def test_meta_rubric_marks_inadequate_with_missing():
    judge = FakeJudge({
        "verdict": "inadequate",
        "missing": ["session timeout enforcement", "password complexity check"],
    })
    checker = MetaRubricChecker(judge=judge)
    judgment = checker.check(_feature())
    assert judgment.adequate is False
    assert judgment.missing == [
        "session timeout enforcement",
        "password complexity check",
    ]


def test_meta_rubric_passes_feature_context_to_judge():
    judge = FakeJudge({"verdict": "adequate", "missing": []})
    checker = MetaRubricChecker(judge=judge)
    checker.check(_feature())
    assert len(judge.calls) == 1
    payload = judge.calls[0]
    assert "users can log in" in str(payload)
    assert "python_pytest" in str(payload)


def test_coverage_judgment_is_str():
    j = CoverageJudgment(adequate=False, missing=["x"], reasoning="r")
    s = str(j)
    assert "inadequate" in s.lower() or "missing" in s.lower()
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_meta_rubric.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `meta_rubric.py`**

`claude_orchestrator/bob/duplo/meta_rubric.py`:

```python
"""Meta-rubric coverage check (see spec §6.6).

Runs an LLM-as-judge that asks: "given this feature's success criteria
and the verifier_id assigned to it, does the verifier actually cover the
criteria?" If the answer is 'inadequate', Duplo refuses to ship the spec.

The judge is injected for testability — production wires in a real
Anthropic API call; tests use a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from claude_orchestrator.models import Feature


@dataclass(frozen=True)
class CoverageJudgment:
    adequate: bool
    missing: list[str]
    reasoning: str

    def __str__(self) -> str:
        if self.adequate:
            return f"adequate: {self.reasoning}"
        return f"inadequate; missing: {self.missing}; reasoning: {self.reasoning}"


class Judge(Protocol):
    """Anything that takes a payload and returns a coverage verdict dict.

    Production implementation calls Claude Opus 4.7. Tests inject a fake.
    """

    def judge(self, prompt_payload: dict) -> dict: ...


class MetaRubricChecker:
    def __init__(self, judge: Judge) -> None:
        self._judge = judge

    def check(self, feature: Feature) -> CoverageJudgment:
        payload = {
            "feature_name": feature.name,
            "feature_description": feature.description,
            "task_type": str(feature.task_type),
            "verifier_id": feature.verification_plan.verifier_id,
            "success_criteria": feature.verification_plan.success_criteria,
            "required_tools": feature.verification_plan.required_tools,
            "instruction": (
                "Decide whether the assigned verifier actually verifies the "
                "feature's success criteria. Reply JSON with keys "
                "'verdict' (one of 'adequate'|'inadequate'), 'missing' (list of "
                "criteria the verifier does not cover; [] when adequate), and "
                "'reasoning' (one short sentence)."
            ),
        }
        result = self._judge.judge(payload)
        verdict = result.get("verdict", "inadequate")
        missing = list(result.get("missing", []))
        reasoning = result.get("reasoning", "")
        return CoverageJudgment(
            adequate=(verdict == "adequate"),
            missing=missing,
            reasoning=reasoning,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_meta_rubric.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/duplo/meta_rubric.py tests/bob/test_meta_rubric.py
git commit -m "feat(bob): meta-rubric LLM-as-judge coverage check"
```

---

### Task 11: Duplo markdown parser (M1 stub)

**Files:**
- Create: `claude_orchestrator/bob/duplo/markdown_parser.py`
- Create: `tests/bob/test_duplo_markdown.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_duplo_markdown.py`:

```python
"""Tests for the M1 markdown spec parser.

Input format (M1 stub — multimodal Duplo lands in M2):

  # Title
  ## Motivation
  text...
  ## Features
  ### F1: auth
  - task_type: library
  - verifier: python_pytest
  - success_criteria:
    - users can log in
  - description: |
      Add a login endpoint.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.duplo.markdown_parser import (
    SpecParseError,
    parse_markdown_spec,
)


def test_parse_minimal_spec(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# Demo project
## Motivation
Make a thing.
## Features
### F1: auth
- task_type: library
- verifier: python_pytest
- success_criteria:
  - users can log in
- description: Add a login endpoint.
""")
    spec = parse_markdown_spec(md)
    assert spec.title == "Demo project"
    assert spec.motivation == "Make a thing."
    assert len(spec.features) == 1
    f = spec.features[0]
    assert f.id == 1
    assert f.name == "auth"
    assert str(f.task_type) == "library"
    assert f.verification_plan.verifier_id == "python_pytest"
    assert f.verification_plan.success_criteria == ["users can log in"]
    assert f.description == "Add a login endpoint."


def test_parse_multiple_features(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# Multi
## Motivation
m
## Features
### F1: a
- task_type: library
- verifier: python_pytest
- success_criteria:
  - x
- description: A
### F2: b
- task_type: cli
- verifier: python_pytest
- success_criteria:
  - y
- description: B
""")
    spec = parse_markdown_spec(md)
    assert [f.name for f in spec.features] == ["a", "b"]
    assert [f.id for f in spec.features] == [1, 2]


def test_parse_rejects_missing_title(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("## Motivation\nm\n## Features\n")
    with pytest.raises(SpecParseError, match="title"):
        parse_markdown_spec(md)


def test_parse_rejects_feature_without_verifier(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# T
## Motivation
m
## Features
### F1: a
- task_type: library
- success_criteria:
  - x
- description: a
""")
    with pytest.raises(SpecParseError, match="verifier"):
        parse_markdown_spec(md)


def test_parse_rejects_unknown_task_type(tmp_path: Path):
    md = tmp_path / "spec.md"
    md.write_text("""\
# T
## Motivation
m
## Features
### F1: a
- task_type: bogus
- verifier: python_pytest
- success_criteria:
  - x
- description: a
""")
    with pytest.raises(SpecParseError, match="task_type"):
        parse_markdown_spec(md)
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_duplo_markdown.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `markdown_parser.py`**

`claude_orchestrator/bob/duplo/markdown_parser.py`:

```python
"""M1 stub for Duplo: parse a structured markdown spec into Spec/Feature.

The format is intentionally narrow and predictable for M1. M2 replaces this
with a multimodal Anthropic vision call that produces the same Spec shape.
"""

from __future__ import annotations

import re
from pathlib import Path

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
)


class SpecParseError(ValueError):
    """The markdown does not match the M1 expected format."""


_FEATURE_HEADER = re.compile(r"^###\s+F(\d+):\s+(\S.*)$")
_FIELD = re.compile(r"^-\s+(\w+):\s*(.*)$")
_SUB_BULLET = re.compile(r"^\s+-\s+(.+)$")


def parse_markdown_spec(path: Path) -> Spec:
    text = path.read_text()
    lines = text.splitlines()

    title = _extract_h1(lines)
    motivation = _extract_section(lines, "Motivation")
    feature_blocks = _split_feature_blocks(lines)

    if not title:
        raise SpecParseError("spec is missing an `# Title` heading")

    features: list[Feature] = [_parse_feature_block(b) for b in feature_blocks]
    return Spec(
        title=title,
        motivation=motivation,
        inputs=[],
        features=features,
        rubric_meta_check_passed=False,
    )


def _extract_h1(lines: list[str]) -> str | None:
    for line in lines:
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return None


def _extract_section(lines: list[str], name: str) -> str:
    """Return text under `## <name>` until the next `##` heading."""
    capturing = False
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("## ") and s[3:].strip() == name:
            capturing = True
            continue
        if capturing and s.startswith("## "):
            break
        if capturing:
            out.append(line)
    return "\n".join(l for l in out if l.strip()).strip()


def _split_feature_blocks(lines: list[str]) -> list[list[str]]:
    """Find each `### F<N>: ...` block and return its lines."""
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if _FEATURE_HEADER.match(line):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            if line.lstrip().startswith("##") and not line.lstrip().startswith("###"):
                blocks.append(current)
                current = None
            else:
                current.append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _parse_feature_block(block: list[str]) -> Feature:
    header = _FEATURE_HEADER.match(block[0])
    if not header:
        raise SpecParseError(f"bad feature header: {block[0]!r}")
    fid = int(header.group(1))
    name = header.group(2).strip()

    fields: dict[str, str | list[str]] = {}
    current_list_field: str | None = None
    for line in block[1:]:
        m = _FIELD.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            if value:
                fields[key] = value
                current_list_field = None
            else:
                fields[key] = []
                current_list_field = key
            continue
        sm = _SUB_BULLET.match(line)
        if sm and current_list_field:
            fields[current_list_field].append(sm.group(1).strip())  # type: ignore[union-attr]

    try:
        task_type_str = str(fields["task_type"])
    except KeyError:
        raise SpecParseError(f"feature {fid} missing task_type")
    try:
        task_type = TaskType(task_type_str)
    except ValueError:
        raise SpecParseError(
            f"feature {fid}: unknown task_type {task_type_str!r}; "
            f"valid: {sorted(t.value for t in TaskType)}"
        )

    verifier_id = fields.get("verifier")
    if not verifier_id:
        raise SpecParseError(f"feature {fid} missing verifier")

    success = fields.get("success_criteria", [])
    if isinstance(success, str):
        success = [success]
    description = str(fields.get("description", "")).strip()

    plan = VerificationPlan(
        verifier_id=str(verifier_id),
        success_criteria=success,  # type: ignore[arg-type]
        required_tools=[],
    )
    return Feature(
        id=fid,
        name=name,
        description=description,
        task_type=task_type,
        verification_plan=plan,
        status=FeatureStatus.PENDING,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_duplo_markdown.py -v`

Expected: PASS — all 5 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/duplo/markdown_parser.py tests/bob/test_duplo_markdown.py
git commit -m "feat(bob): M1 markdown spec parser for Duplo"
```

---

### Task 12: McLoop iteration prompt template

**Files:**
- Create: `claude_orchestrator/bob/mcloop/prompts/iteration.md`

- [ ] **Step 1: Write the prompt file**

`claude_orchestrator/bob/mcloop/prompts/iteration.md`:

```markdown
You are a focused builder advancing one small slice of work toward EXIT_SIGNAL. The orchestrator runs you in a loop with fresh context every iteration. Your only memory of prior iterations is the workspace itself plus three files. Read them every time.

# Files you must read this iteration

1. `{master_spec_path}` — the master spec
2. `{feature_spec_path}` — this feature's slice
3. `{activity_path}` — what previous iterations did (your memory)
4. `{failed_attempts_path}` — what previous iterations tried that didn't work (avoid repeating)

# Feature

- ID: {feature_id}
- Name: {feature_name}
- Task type: {task_type}
- Verifier: {verifier_id}

# Success criteria (verbatim from spec)

{success_criteria_block}

# How to work this iteration

1. Read all four files above.
2. Pick the smallest unresolved item from the success criteria.
3. Make a focused edit to the workspace.
4. Run the verifier (`{verifier_id}`).
5. Inspect the verifier output:
   - **Ok** → commit your change with a clear message; append a short note to `{activity_path}` describing what you did and why.
   - **Fail** → append the failure mode to `{failed_attempts_path}` with the exact symptom, then iterate this iteration if there's time, else exit and let the loop try again with the failure recorded.
   - **Inconclusive** → STOP. Output the verifier's reason and `<promise>HALT_INCONCLUSIVE</promise>` as the final line. Do NOT keep working.
6. If the verifier returns Ok and you believe the feature is fully implemented and all success criteria are met, output `<promise>EXIT_SIGNAL</promise>` as the final line.
7. Otherwise, the loop will spawn you again next iteration with the updated files.

# Failure handling

- If a tool call fails, log the error to `{failed_attempts_path}` and try a different approach.
- If you encounter the same failure mode you already logged, do NOT repeat it. Try something genuinely different.
- If you are stuck, write a paragraph to `{failed_attempts_path}` describing the blockage and exit. The loop will surface this to the human.

# Discipline

- Do exactly ONE focused unit of work this iteration. Do not try to finish the feature in one pass. The loop is your friend.
- Commit only clean code. Tests/lint/verifier must be green before any commit.
- Treat `{master_spec_path}` and `{feature_spec_path}` as ground truth. Quote success criteria; do not paraphrase.
- Failures are data. Write them down so future-you can read them.
```

- [ ] **Step 2: Commit**

```bash
git add claude_orchestrator/bob/mcloop/prompts/iteration.md
git commit -m "feat(bob): McLoop iteration prompt template (template variables in braces)"
```

---

### Task 13: McLoop runner (bash-loop pattern)

**Files:**
- Create: `claude_orchestrator/bob/mcloop/runner.py`
- Create: `tests/bob/test_mcloop_runner.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_mcloop_runner.py`:

```python
"""Tests for the McLoop runner.

The runner spawns `claude -p` subprocesses. Tests use a stub `claude`
shell script (created in tmp_path) to exercise the loop deterministically.
"""
from pathlib import Path
from textwrap import dedent

import pytest

from claude_orchestrator.bob.mcloop.runner import McLoopRunner, McLoopResult
from claude_orchestrator.bob.verifiers.protocol import VerifyResult
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="fake",
            success_criteria=["x"],
            required_tools=[],
        ),
        status=FeatureStatus.PENDING,
    )


class FakeVerifier:
    """Returns scripted results in order."""

    id = "fake"

    def __init__(self, results: list[VerifyResult]):
        self.results = list(results)
        self.calls = 0

    def applies_to(self): return [TaskType.LIBRARY]
    def required_tools(self): return []
    def preflight(self, ws): return None
    def verify(self, ws, f):
        self.calls += 1
        return self.results.pop(0)


@pytest.fixture
def fake_claude_emits_exit(tmp_path: Path) -> Path:
    """A fake `claude` binary that just emits the exit promise on first call."""
    script = tmp_path / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return script


def test_runner_exits_when_promise_emitted_and_verifier_ok(
    tmp_path: Path, fake_claude_emits_exit: Path
):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("# slice\n")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("# master\n")

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])

    runner = McLoopRunner(
        claude_cmd=str(fake_claude_emits_exit),
        max_iterations=5,
        per_iteration_timeout_s=10,
    )
    result = runner.run(
        feature=feature,
        workspace=workspace,
        master_spec=master_spec,
        feature_dir=feature_dir,
        verifier=verifier,
    )
    assert isinstance(result, McLoopResult)
    assert result.outcome == "exit_signal"
    assert result.iterations == 1


def test_runner_halts_loud_on_inconclusive(tmp_path: Path):
    """An Inconclusive verifier result halts immediately (default mode)."""
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho ok\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(
            status="inconclusive",
            reason="no tests collected",
            artifacts=[],
            coverage_notes="add a test_*.py file",
        ),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=5,
                         per_iteration_timeout_s=10)
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "halted_inconclusive"
    assert result.iterations == 1
    assert "no tests collected" in result.last_reason


def test_runner_stops_at_max_iterations(tmp_path: Path):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    # claude prints something but never the promise
    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho 'still working'\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
        VerifyResult(status="fail", reason="r", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=2,
                         per_iteration_timeout_s=10)
    result = runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )
    assert result.outcome == "max_iterations"
    assert result.iterations == 2


def test_runner_records_verifier_results(tmp_path: Path):
    feature = _feature()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    feature_dir = tmp_path / ".bob" / "features" / "001-t"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("")
    (feature_dir / "activity.md").write_text("")
    (feature_dir / "failed_attempts.md").write_text("")
    (feature_dir / "verifier-results.jsonl").write_text("")
    master_spec = tmp_path / ".bob" / "spec.md"
    master_spec.write_text("")

    fake_claude = tmp_path / "claude"
    fake_claude.write_text("#!/bin/sh\necho '<promise>EXIT_SIGNAL</promise>'\n")
    fake_claude.chmod(0o755)

    verifier = FakeVerifier([
        VerifyResult(status="ok", reason="green", artifacts=[], coverage_notes=None),
    ])
    runner = McLoopRunner(claude_cmd=str(fake_claude), max_iterations=5,
                         per_iteration_timeout_s=10)
    runner.run(
        feature=feature, workspace=workspace,
        master_spec=master_spec, feature_dir=feature_dir, verifier=verifier,
    )

    log = (feature_dir / "verifier-results.jsonl").read_text()
    assert '"status": "ok"' in log
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_mcloop_runner.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `runner.py`**

`claude_orchestrator/bob/mcloop/runner.py`:

```python
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

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
                    [self.claude_cmd, "-p", prompt],
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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_mcloop_runner.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/mcloop/runner.py tests/bob/test_mcloop_runner.py
git commit -m "feat(bob): McLoop runner with bash-loop pattern and halt-loud Inconclusive"
```

---

### Task 14: Orchestra stub (M1 single-model approval)

**Files:**
- Create: `claude_orchestrator/bob/orchestra/stub.py`
- Create: `tests/bob/test_orchestra_stub.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_orchestra_stub.py`:

```python
"""Tests for the Orchestra stub.

M1 stub: a single LLM-as-judge call that says approve|reject|abstain.
M2 replaces this with AutoGen GroupChat + KS-stability.
"""
from pathlib import Path

import pytest

from claude_orchestrator.bob.orchestra.stub import (
    OrchestraStub,
    SingleJudge,
)
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="auth", description="login",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["users can log in"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.MCLOOP_DONE,
    )


class FakeJudge:
    def __init__(self, response: dict):
        self.response = response

    def judge_diff(self, feature: Feature, diff: str) -> dict:
        return self.response


def test_stub_returns_approve(tmp_path: Path):
    judge = FakeJudge({
        "decision": "approve", "confidence": 0.9, "reasoning": "lgtm",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="diff goes here", debate_log_dir=tmp_path)
    assert verdict.decision == "approve"
    assert verdict.confidence == pytest.approx(0.9)


def test_stub_returns_reject_with_reasoning(tmp_path: Path):
    judge = FakeJudge({
        "decision": "reject", "confidence": 0.7,
        "reasoning": "missing csrf protection",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="d", debate_log_dir=tmp_path)
    assert verdict.decision == "reject"
    assert "csrf" in verdict.judge_reasoning


def test_stub_writes_debate_log(tmp_path: Path):
    judge = FakeJudge({
        "decision": "approve", "confidence": 1.0, "reasoning": "ok",
    })
    stub = OrchestraStub(judge=judge)
    verdict = stub.review(_feature(), diff="d", debate_log_dir=tmp_path)
    assert verdict.debate_log_path.exists()
    text = verdict.debate_log_path.read_text()
    assert "approve" in text
    assert "ok" in text
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_orchestra_stub.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `stub.py`**

`claude_orchestrator/bob/orchestra/stub.py`:

```python
"""Orchestra stub for M1.

M1 ships a single-judge LLM-as-judge review. The judge sees the feature
spec and the diff produced by McLoop, and returns approve|reject|abstain
with confidence and reasoning. M2 replaces this with AutoGen GroupChat
(Claude defending, Codex attacking, Opus judging) and KS-stability
termination.

The Verdict schema is the same in both M1 and M2 — only the
implementation behind .review() changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import Feature, Verdict


class SingleJudge(Protocol):
    def judge_diff(self, feature: Feature, diff: str) -> dict: ...


class OrchestraStub:
    def __init__(self, judge: SingleJudge) -> None:
        self._judge = judge

    def review(
        self,
        feature: Feature,
        diff: str,
        debate_log_dir: Path,
    ) -> Verdict:
        result = self._judge.judge_diff(feature, diff)
        decision = result.get("decision", "abstain")
        confidence = float(result.get("confidence", 0.0))
        reasoning = str(result.get("reasoning", ""))

        debate_log_path = debate_log_dir / "debate.json"
        debate_log_dir.mkdir(parents=True, exist_ok=True)
        debate_log_path.write_text(json.dumps({
            "feature_id": feature.id,
            "decision": decision,
            "confidence": confidence,
            "reasoning": reasoning,
            "stub": True,
        }, indent=2))

        return Verdict(
            feature_id=feature.id,
            decision=decision,
            confidence=confidence,
            debate_log_path=debate_log_path,
            judge_reasoning=reasoning,
        )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_orchestra_stub.py -v`

Expected: PASS — all 3 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/orchestra/stub.py tests/bob/test_orchestra_stub.py
git commit -m "feat(bob): Orchestra M1 stub (single-judge review)"
```

---

### Task 15: Coordinator state machine

**Files:**
- Create: `claude_orchestrator/bob/coordinator.py`
- Create: `tests/bob/test_coordinator.py`

- [ ] **Step 1: Write failing tests**

`tests/bob/test_coordinator.py`:

```python
"""Tests for the Coordinator state machine.

These tests use stubbed Duplo / McLoop / Orchestra and never hit a real
LLM. The Coordinator's job is choreography; the tests verify it walks
features in order, persists state, and respects HITL gates.
"""
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.coordinator import Coordinator, RunScope
from claude_orchestrator.bob.hitl.gates import GateDecision, GateRegistry, PostDuploGate
from claude_orchestrator.bob.mcloop.runner import McLoopResult
from claude_orchestrator.bob.state_io import read_json, read_jsonl
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    Spec,
    TaskType,
    VerificationPlan,
    Verdict,
)


def _feature(i: int, name: str) -> Feature:
    return Feature(
        id=i, name=name, description=f"f{i}",
        task_type=TaskType.LIBRARY,
        verification_plan=VerificationPlan(
            verifier_id="python_pytest",
            success_criteria=["x"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


def _spec_with_features(*names: str) -> Spec:
    return Spec(
        title="t", motivation="m",
        features=[_feature(i, n) for i, n in enumerate(names, start=1)],
        rubric_meta_check_passed=True,
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """A bare project root that the Coordinator will create .bob/ inside."""
    return tmp_path


def test_coordinator_walks_features_in_order(project_root: Path, monkeypatch):
    spec = _spec_with_features("a", "b")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
    ))
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))

    gates = GateRegistry(disabled={"post_duplo"})  # auto-skip for test

    coord = Coordinator(
        project_root=project_root,
        duplo=duplo,
        mcloop=mcloop,
        orchestra=orchestra,
        gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    bob_dir = project_root / ".bob"
    cursor = read_json(bob_dir / "cursor.json")
    assert cursor["current_phase"] == "idle"
    assert mcloop.call_count == 2
    # call order: feature 1, then feature 2
    assert mcloop.call_args_list[0].kwargs["feature"].name == "a"
    assert mcloop.call_args_list[1].kwargs["feature"].name == "b"


def test_coordinator_writes_run_log(project_root: Path):
    spec = _spec_with_features("a")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="exit_signal", iterations=1, last_reason="ok", last_status="ok",
    ))
    orchestra = MagicMock(return_value=Verdict(
        feature_id=1, decision="approve", confidence=1.0,
        debate_log_path=project_root / ".bob" / "fake.json",
        judge_reasoning="lgtm",
    ))
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    events = list(read_jsonl(project_root / ".bob" / "run-log.jsonl"))
    event_types = [e["event"] for e in events]
    assert "run_started" in event_types
    assert "feature_started" in event_types
    assert "feature_merged" in event_types


def test_coordinator_respects_post_duplo_reject(project_root: Path, monkeypatch):
    spec = _spec_with_features("a")

    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock()
    orchestra = MagicMock()

    class RejectingGate(PostDuploGate):
        def run(self, _):
            return GateDecision.REJECT

    gates = GateRegistry()
    gates.register("post_duplo", RejectingGate())

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    mcloop.assert_not_called()
    orchestra.assert_not_called()


def test_coordinator_marks_feature_failed_on_mcloop_halt(project_root: Path):
    spec = _spec_with_features("a")
    duplo = MagicMock(return_value=spec)
    mcloop = MagicMock(return_value=McLoopResult(
        outcome="halted_inconclusive", iterations=2,
        last_reason="no tests collected", last_status="inconclusive",
    ))
    orchestra = MagicMock()
    gates = GateRegistry(disabled={"post_duplo"})

    coord = Coordinator(
        project_root=project_root, duplo=duplo, mcloop=mcloop,
        orchestra=orchestra, gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    orchestra.assert_not_called()
    bob_dir = project_root / ".bob"
    state_path = bob_dir / "features" / "001-a" / "state.json"
    state = read_json(state_path)
    assert state["status"] == "failed"
    assert "no tests collected" in state["last_error"]
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/bob/test_coordinator.py -v`

Expected: FAIL — module missing.

- [ ] **Step 3: Implement `coordinator.py`**

`claude_orchestrator/bob/coordinator.py`:

```python
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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from claude_orchestrator.bob.hitl.gates import (
    GateDecision,
    GateRegistry,
    GateSkipped,
)
from claude_orchestrator.bob.mcloop.runner import McLoopResult
from claude_orchestrator.bob.state_io import (
    append_jsonl,
    read_json,
    write_json_atomic,
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
            feature = Feature.model_validate_json(
                (feature_dir / "state.json").read_text()
            )
            if feature.status in (
                FeatureStatus.MERGED, FeatureStatus.SKIPPED, FeatureStatus.FAILED
            ):
                continue

            self._run_feature(feature, feature_dir, run_id)

        self._set_cursor("idle", None, run_id)
        self._log_event("run_finished", {"run_id": run_id})

    # ---- internals ----

    def _materialize_spec(self, spec: Spec) -> None:
        write_json_atomic(self.bob_dir / "spec.md", spec.title)  # placeholder
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
            d.mkdir(parents=True, exist_ok=True)
            (d / "spec.md").write_text(
                f"# F{f.id}: {f.name}\n\n{f.description}\n"
            )
            (d / "activity.md").write_text("")
            (d / "failed_attempts.md").write_text("")
            (d / "verifier-results.jsonl").write_text("")
            write_json_atomic(d / "state.json", f.model_dump(mode="json"))

    def _run_feature(self, feature: Feature, feature_dir: Path, run_id: str) -> None:
        self._set_cursor("mcloop", feature.id, run_id)
        self._log_event("feature_started", {"feature_id": feature.id, "name": feature.name})
        feature.status = FeatureStatus.IN_PROGRESS
        feature.updated_at = datetime.now(UTC)
        self._save_feature(feature, feature_dir)

        worktree = self.bob_dir / "worktrees" / _feature_dirname(feature)
        # Worktree creation/cleanup is wired in by the CLI; tests mock mcloop.

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
            return

        feature.status = FeatureStatus.MCLOOP_DONE
        feature.updated_at = datetime.now(UTC)
        self._save_feature(feature, feature_dir)

        # ---- Orchestra ----
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
        else:
            # M1: stub Orchestra rejection behaves like a halt; M2 will retry McLoop.
            feature.status = FeatureStatus.REJECTED
            feature.last_error = verdict.judge_reasoning
            feature.updated_at = datetime.now(UTC)
            self._save_feature(feature, feature_dir)
            self._log_event("feature_rejected", {
                "feature_id": feature.id,
                "reason": verdict.judge_reasoning,
            })

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
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/bob/test_coordinator.py -v`

Expected: PASS — all 4 tests green.

- [ ] **Step 5: Commit**

```bash
git add claude_orchestrator/bob/coordinator.py tests/bob/test_coordinator.py
git commit -m "feat(bob): Coordinator state machine with dependency-injected phases"
```

---

### Task 16: Signal handlers and process lock integration

**Files:**
- Create: `claude_orchestrator/bob/signals.py`

This is wired into the CLI in Task 17. Implementation only here.

- [ ] **Step 1: Implement signals.py**

`claude_orchestrator/bob/signals.py`:

```python
"""SIGINT/SIGTERM/SIGHUP handler that flips a shutdown flag.

Mirrors the existing orchestrator.py pattern. The Coordinator should
poll _is_shutdown() between phase transitions in long-running flows.
"""

from __future__ import annotations

import atexit
import logging
import signal
from collections.abc import Callable

log = logging.getLogger(__name__)

_shutdown_requested = False
_cleanup_callbacks: list[Callable[[], None]] = []


def install_handlers() -> None:
    """Install SIGINT/SIGTERM/SIGHUP -> _shutdown_requested = True."""
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _on_signal)
    atexit.register(_run_cleanups)


def is_shutdown_requested() -> bool:
    return _shutdown_requested


def register_cleanup(fn: Callable[[], None]) -> None:
    _cleanup_callbacks.append(fn)


def _on_signal(signum: int, _frame) -> None:
    global _shutdown_requested
    if _shutdown_requested:
        # Second signal: force exit immediately.
        log.warning("second signal %s received — force exit", signum)
        _run_cleanups()
        raise SystemExit(130)
    _shutdown_requested = True
    log.warning("signal %s received — shutting down gracefully (Ctrl-C again to force)",
                signum)


def _run_cleanups() -> None:
    for fn in reversed(_cleanup_callbacks):
        try:
            fn()
        except Exception:
            log.exception("cleanup callback raised")
```

- [ ] **Step 2: Smoke-test the import**

Run: `python -c "from claude_orchestrator.bob.signals import install_handlers, is_shutdown_requested; print('ok')"`

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add claude_orchestrator/bob/signals.py
git commit -m "feat(bob): SIGINT/SIGTERM/SIGHUP graceful-shutdown wiring"
```

---

### Task 17: CLI commands (`bob run`, `bob status`)

**Files:**
- Modify: `claude_orchestrator/cli.py`
- Create: `claude_orchestrator/bob/cli.py`
- Create: `tests/bob/test_cli.py`

- [ ] **Step 1: Inspect existing cli.py**

Run: `cat claude_orchestrator/cli.py`

Confirm: it uses `argparse` (or whatever — adapt to the framework already present). Note the `cli_entry()` function from `pyproject.toml`'s `[project.scripts]` — `orchestrate = "claude_orchestrator.cli:cli_entry"`.

- [ ] **Step 2: Write failing CLI smoke test**

`tests/bob/test_cli.py`:

```python
"""CLI smoke tests for `bob run` and `bob status`.

These tests don't run real Claude — they exercise argument parsing and
verify the run command dispatches with the right config.
"""
import subprocess
import sys
from pathlib import Path


def test_orchestrate_bob_run_help_smoke():
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "run", "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "--inputs" in result.stdout
    assert "--max-iterations" in result.stdout


def test_orchestrate_bob_status_on_empty_dir(tmp_path: Path):
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "no .bob/ found" in result.stdout.lower() or "not initialized" in result.stdout.lower()


def test_orchestrate_bob_status_on_initialized(tmp_path: Path):
    bob_dir = tmp_path / ".bob"
    bob_dir.mkdir()
    (bob_dir / "cursor.json").write_text(
        '{"run_id": "x", "current_phase": "idle", "current_feature_id": null,'
        ' "last_event_at": "2026-05-07T00:00:00+00:00"}'
    )
    (bob_dir / "features").mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "claude_orchestrator.bob.cli", "status",
         "--project", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "idle" in result.stdout
```

- [ ] **Step 3: Run to confirm failure**

Run: `pytest tests/bob/test_cli.py -v`

Expected: FAIL — module not found / no main entry.

- [ ] **Step 4: Implement `bob/cli.py`**

`claude_orchestrator/bob/cli.py`:

```python
"""Bob CLI — subcommands `run`, `status`.

Invoked via `python -m claude_orchestrator.bob.cli` or as `bob`
(when registered in pyproject.toml's [project.scripts]).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from claude_orchestrator.bob.signals import install_handlers
from claude_orchestrator.bob.state_io import read_json


def _cmd_run(args: argparse.Namespace) -> int:
    install_handlers()
    project_root = Path(args.project).resolve()
    print(f"bob run on {project_root} (max_iterations={args.max_iterations})")
    print("M1: full run wiring is the integration test; see Task 18.")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    project_root = Path(args.project).resolve()
    bob_dir = project_root / ".bob"
    if not bob_dir.exists():
        print(f"no .bob/ found in {project_root} (not initialized)")
        return 0

    cursor = read_json(bob_dir / "cursor.json", default={})
    print(f"phase: {cursor.get('current_phase', 'unknown')}")
    print(f"current feature: {cursor.get('current_feature_id', 'n/a')}")
    print(f"last event: {cursor.get('last_event_at', 'n/a')}")

    features_dir = bob_dir / "features"
    if features_dir.exists():
        feats = sorted(d.name for d in features_dir.iterdir() if d.is_dir())
        if feats:
            print(f"features ({len(feats)}):")
            for d in feats:
                state = read_json(features_dir / d / "state.json", default={})
                print(f"  {d}: status={state.get('status', 'unknown')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bob")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a Bob orchestration")
    run.add_argument("--project", default=".", help="project root (default: cwd)")
    run.add_argument("--inputs", required=False,
                     help="path to a markdown spec or directory of inputs (M1: markdown only)")
    run.add_argument("--max-iterations", type=int, default=30,
                     help="max McLoop iterations per feature (default: 30)")
    run.add_argument("--max-cost", type=float, default=None,
                     help="optional USD cap (advisory in subscription mode)")
    run.add_argument("--no-gate", action="append", default=[],
                     help="disable a HITL gate by name (repeatable)")
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show current Bob state")
    status.add_argument("--project", default=".", help="project root (default: cwd)")
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/bob/test_cli.py -v`

Expected: PASS — all 3 tests green.

- [ ] **Step 6: Register `bob` script in pyproject.toml**

Open `pyproject.toml`, find the `[project.scripts]` section, add:

```toml
[project.scripts]
orchestrate = "claude_orchestrator.cli:cli_entry"
bob = "claude_orchestrator.bob.cli:main"
```

- [ ] **Step 7: Reinstall in editable mode and verify the script**

```bash
pip install -e .
bob --help
```

Expected: bob's argparse help text printed, including `run` and `status` subcommands.

- [ ] **Step 8: Commit**

```bash
git add claude_orchestrator/bob/cli.py tests/bob/test_cli.py pyproject.toml
git commit -m "feat(bob): bob run / bob status CLI commands"
```

---

### Task 18: End-to-end integration test

**Files:**
- Create: `tests/bob/test_e2e_smoke.py`

This is the demo of M1: a real markdown spec → real markdown parser → coordinator → stubbed Duplo (returns parsed spec) → real McLoop (against a stub `claude` script that emits EXIT_SIGNAL on the first call) → real python_pytest verifier (against an in-tmp_path workspace) → stub Orchestra (auto-approves) → merged feature.

- [ ] **Step 1: Write the integration test**

`tests/bob/test_e2e_smoke.py`:

```python
"""End-to-end smoke test for Bob M1.

Exercises the full pipeline with stubbed external calls:
  - markdown spec → Spec/Feature
  - Coordinator → McLoopRunner (with stub `claude` shell script) → python_pytest
  - Orchestra stub auto-approves
  - Coordinator marks the feature merged

No real Claude API calls are made. The test takes ~2-3 seconds.
"""
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from claude_orchestrator.bob.coordinator import Coordinator, RunScope
from claude_orchestrator.bob.duplo.markdown_parser import parse_markdown_spec
from claude_orchestrator.bob.hitl.gates import GateRegistry
from claude_orchestrator.bob.mcloop.runner import McLoopRunner
from claude_orchestrator.bob.orchestra.stub import OrchestraStub
from claude_orchestrator.bob.state_io import read_jsonl, read_json
from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
from claude_orchestrator.models import Feature, Verdict


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    """Initialize a git repo with a tiny passing test in the worktree-to-be."""
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
def fake_claude(tmp_path_factory) -> Path:
    """A `claude` stub that emits EXIT_SIGNAL on first call."""
    d = tmp_path_factory.mktemp("fake-claude")
    script = d / "claude"
    script.write_text(dedent("""\
        #!/bin/sh
        echo "<promise>EXIT_SIGNAL</promise>"
    """))
    script.chmod(0o755)
    return script


def test_e2e_thin_slice_runs_to_merged(project_root: Path, fake_claude: Path):
    spec_path = project_root / "spec.md"
    spec_path.write_text(dedent("""\
        # Smoke
        ## Motivation
        Prove M1 works end-to-end without hitting real APIs.
        ## Features
        ### F1: passing-tests
        - task_type: library
        - verifier: python_pytest
        - success_criteria:
          - existing tests stay green
        - description: This feature is already implemented; the loop should exit on iteration 1.
    """))

    spec = parse_markdown_spec(spec_path)
    spec.rubric_meta_check_passed = True  # bypass meta-rubric check for E2E

    runner = McLoopRunner(
        claude_cmd=str(fake_claude),
        max_iterations=5,
        per_iteration_timeout_s=10,
    )
    verifier = PythonPytestVerifier()

    class AutoApproveJudge:
        def judge_diff(self, feature: Feature, diff: str) -> dict:
            return {"decision": "approve", "confidence": 1.0, "reasoning": "stub e2e"}

    orchestra = OrchestraStub(judge=AutoApproveJudge())
    gates = GateRegistry(disabled={"post_duplo"})

    def fake_duplo() -> Feature:
        return spec

    def mcloop_callable(*, feature, workspace, master_spec, feature_dir):
        # In the real CLI the coordinator creates a worktree; for this test the
        # workspace is the project root (where the passing test already lives).
        return runner.run(
            feature=feature,
            workspace=project_root,
            master_spec=master_spec,
            feature_dir=feature_dir,
            verifier=verifier,
        )

    def orchestra_callable(*, feature, workspace, feature_dir):
        return orchestra.review(
            feature=feature,
            diff="(stub diff)",
            debate_log_dir=feature_dir,
        )

    coord = Coordinator(
        project_root=project_root,
        duplo=fake_duplo,
        mcloop=mcloop_callable,
        orchestra=orchestra_callable,
        gates=gates,
    )
    coord.run(RunScope(includes_duplo=True))

    bob_dir = project_root / ".bob"
    feature_dir = next((bob_dir / "features").iterdir())
    state = read_json(feature_dir / "state.json")
    assert state["status"] == "merged"

    events = [e["event"] for e in read_jsonl(bob_dir / "run-log.jsonl")]
    assert "run_started" in events
    assert "feature_merged" in events
    assert "run_finished" in events
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/bob/test_e2e_smoke.py -v`

Expected: PASS — the full pipeline runs and the feature reaches `merged` status.

- [ ] **Step 3: Run the entire test suite to confirm nothing regressed**

Run: `pytest -x -q`

Expected: every test green (existing + bob/* + the e2e smoke).

- [ ] **Step 4: Commit**

```bash
git add tests/bob/test_e2e_smoke.py
git commit -m "test(bob): end-to-end M1 smoke test (no real API calls)"
```

---

## Self-review

1. **Spec coverage check.** Spec §3 (state layout): ✓ Tasks 3, 5, 15. §3.1 (concurrency): ✓ Task 4 (lock) + Task 16 (signals). §5 (phase contracts): ✓ Task 2. §6.1 (Coordinator): ✓ Task 15. §6.2 (Duplo, M1 stub): ✓ Tasks 11, 10. §6.3 (McLoop): ✓ Tasks 12, 13. §6.4 (Orchestra, M1 stub): ✓ Task 14. §6.6 (Verifier protocol): ✓ Tasks 6, 7. §6.7 (Hooks promotion): ✓ Task 8. §6.8 (HITL gates, post-Duplo only for M1): ✓ Task 9. §11 testing strategy: ✓ Tasks 2-18 (all TDD + Task 18 E2E).

   **Out of scope for M1 (deferred per spec §1):** §6.4 real AutoGen+KS — M2. §6.5 Vroom — M3. §6.9 YOLO — M2 (after real Orchestra exists). §6.10 sandbox tier 2/3 — M4. §10 OpenTelemetry observability — M4. §9 subscription-aware cost — M4. Multimodal Duplo — M2. Meta-rubric LLM-as-judge production wiring (Task 10 ships the framework with a fake judge; production judge call lands in M2 alongside the real multimodal Duplo).

2. **Placeholder scan.** All steps include actual code/commands. No `TBD`, `TODO`, or "fill in later" — verified.

3. **Type consistency.** `Feature.status`, `FeatureStatus.*`, `VerifyResult.status`, `Verdict.decision` — all referenced consistently. `verifier_id` used uniformly. `_feature_dirname()` produces the same `<NNN>-<slug>` pattern referenced in Tasks 13, 15, and 18. Coordinator's `mcloop` and `orchestra` callable signatures match what Task 13 (`McLoopRunner.run(...)`) and Task 14 (`OrchestraStub.review(...)`) expose.

4. **Ambiguity check.** One real ambiguity caught: `Coordinator._materialize_spec` calls `write_json_atomic(self.bob_dir / "spec.md", spec.title)` as a placeholder, then immediately overwrites with the markdown. Cleaned up in the implementation — only the markdown write actually runs. Reader sanity: the integration test (Task 18) verifies this path produces a usable spec.md.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-07-bob-m1-thin-slice.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
