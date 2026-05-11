# Bob Architecture Roadmap After Deep Audit

> Status: proposed roadmap after the May 2026 M9/M10 hardening and external audit pass.

## Objective

Bob is trying to make LLM-driven coding less like "ask an agent and hope" and more like a bounded delivery system:

1. Duplo turns messy user inputs into a structured feature plan.
2. McLoop implements one feature at a time in an isolated git worktree.
3. Verifiers provide task-specific `ok`, `fail`, or `inconclusive` feedback.
4. Orchestra adversarially reviews the resulting diff before merge.
5. Vroom continuously audits merged work and opens isolated fix loops.
6. HITL and YOLO policy decide when autonomy is allowed.

The core design goal is not maximum autonomy. The goal is bounded autonomy with loud failure modes, inspectable state, and recovery paths that do not silently burn tokens or merge unreviewed work.

## Architectural Assessment

The current architecture is directionally correct. The strongest choices are:

- Per-feature git worktrees and branches as the isolation primitive.
- Explicit phase boundaries: Duplo, McLoop, Orchestra, Vroom.
- Verifiers as first-class contracts, especially the `inconclusive` halt-loud state.
- Plain file and git state instead of an early service/database stack.
- Dependency injection around Coordinator, McLoop, and Orchestra, which keeps most behavior testable.

The codebase is good for a fast-moving hardening-stage tool, but it is not yet boring production infrastructure. The recurring defect pattern is producer/consumer mismatch across subprocesses, containers, env vars, JSONL files, cost rows, and resume state. The abstractions exist, but too many boundary contracts are implicit.

The next phase should tighten the existing architecture rather than replace it with a heavier framework. Do not introduce LangGraph, a database, or a queue just to make the system feel more formal. First make the local contracts explicit.

## Main Risks

### 1. CLI composition is too dense

`claude_orchestrator/bob/cli.py` currently mixes argument parsing, Vroom wiring, YOLO reconstruction, auditor selection, fix-loop construction, and command execution. This makes it easy for a flag or env var to be consumed by one component but not another.

Target shape:

- CLI parses args into typed config objects.
- A wiring/application layer builds Duplo, McLoop, Orchestra, Vroom, gates, sandbox executors, and cost policy.
- Command handlers stay thin and only invoke the assembled application services.

### 2. State is pragmatic but informal

`.bob/cursor.json`, feature `state.json`, `run-log.jsonl`, `findings.jsonl`, pid files, and cost rows work as a local database, but state transitions are scattered.

Target shape:

- Define explicit transition helpers for feature and run state.
- Validate persisted JSON on read with clear recovery behavior.
- Make resume paths first-class tests for partial writes and partially completed phases.
- Keep the storage as files for now; the issue is transition discipline, not storage technology.

### 3. Cost policy is still accounting, not enforcement

The M10 audit identified that inner `claude -p` spend was absent from `costs.jsonl`. The audit PR records Claude CLI stream-json `total_cost_usd`, but budget enforcement still needs a central policy layer.

Target shape:

- One budget guard queried before and after every expensive operation.
- Direct API calls and CLI-reported spend share the same ledger.
- YOLO requires a hard budget policy, not just a configured maximum.
- Budget failures halt loud and persist enough context for resume.

### 4. Boundary contracts are implicit

The highest-density bug class is "field/path/env exists and a downstream consumer expects it, but the producer drops it at a process, container, or persistence boundary."

Target shape:

- Typed config objects for run, sandbox, Vroom, YOLO, and cost policy.
- Boundary tests for every subprocess/container/persistence handoff.
- Explicit fail-loud behavior when a required value is absent.
- Structured parsers for stream-json and JSONL, with malformed data surfaced rather than silently ignored.

## Recommended Roadmap

### M11 - Make wiring explicit

- [ ] Create typed config models for BobRunConfig, SandboxConfig, YoloConfig, VroomConfig, CostPolicy.
- [ ] Extract `build_run_app()` and `build_vroom_app()` from `cli.py`.
- [ ] Preserve the current CLI surface while moving construction logic out of command handlers.
- [ ] Add tests proving CLI flags and env vars reach the right downstream components.
- [ ] Keep behavior changes minimal; this is an architectural consolidation pass.

### M12 - State transition layer

- [ ] Introduce helpers for feature status transitions and cursor writes.
- [ ] Validate `state.json`, `cursor.json`, and key JSONL records at read time.
- [ ] Add resume tests for crash points between Duplo, worktree creation, McLoop, Orchestra, merge, and cleanup.
- [ ] Add a small state-inspection command or richer `bob status` output for partial runs.

### M13 - Budget guard

- [ ] Create a central budget guard that reads current ledger totals.
- [ ] Check budget before direct API calls, before `claude -p`, and after CLI cost rows are recorded.
- [ ] Make `--max-cost` enforce both API and CLI spend in YOLO.
- [ ] Persist budget halt events in `run-log.jsonl`.
- [ ] Update `bob costs` and `bob runs` to clearly distinguish known tracked spend from unknown spend.

### M14 - Real-mode validation campaign

- [ ] Dogfood `bob run --vroom` on a small disposable repo.
- [ ] Dogfood `bob run --yolo --sandbox docker --max-cost <small>` with real APIs.
- [ ] Dogfood `bob run --sandbox devcontainer` against a minimal devcontainer project.
- [ ] Dogfood resume: interrupt mid-run, restart, and verify correct continuation.
- [ ] Record exact commands, cost, run IDs, and outcomes in a validation note.

## Dogfooding Recommendation

Use Bob to implement these improvements, but do it in small bounded specs. Bob is exactly the right tool for M11-M13 if each run is constrained to one architectural boundary.

Recommended order:

1. First dogfood M11 in host sandbox with stub Vroom disabled, because wiring extraction should be mostly pure Python and testable.
2. Then use Bob for M12 state transition work, but seed the spec with explicit crash/resume tests.
3. Use Bob for M13 only after M11 is merged, because budget guard wiring crosses almost every expensive call path.
4. Do not start with overnight YOLO. Use short supervised dogfood runs until M13 is in place.
5. After M13, run the M14 real-mode validation campaign with conservative caps.

Suggested first Bob spec:

```markdown
# Bob M11 Wiring Extraction

## Motivation
Reduce producer/consumer mismatches by moving CLI construction logic into typed config and application wiring helpers.

## Features
### F1: Run config extraction
- task_type: library
- verifier: python_pytest
- success_criteria:
  - CLI args and env vars map into typed BobRunConfig and SandboxConfig
  - Existing bob run CLI tests still pass
  - No behavior change to command-line flags
- description: Extract run-related config parsing from cli.py into a dedicated wiring/config module with focused tests.

### F2: Vroom config extraction
- task_type: library
- verifier: python_pytest
- success_criteria:
  - Vroom daemon and vroom now construct from typed VroomConfig
  - YOLO subprocess env reconstruction is covered by tests
  - Existing vroom CLI tests still pass
- description: Extract Vroom construction from cli.py while preserving current behavior.
```

Run it supervised first:

```bash
bob run --inputs docs/superpowers/plans/2026-05-11-bob-m11-spec.md --sandbox host --max-iterations 5
```

Only move to Docker/YOLO dogfood after the wiring extraction is merged and the budget guard work is underway.

## Non-Goals For The Next Phase

- Do not replace file/git state with SQLite yet.
- Do not introduce a scheduler, queue, or service daemon beyond the current Vroom process.
- Do not broaden model/provider abstraction work unless it directly supports cost policy or wiring clarity.
- Do not expand verifier surface area until the wiring and state contracts are less brittle.

