# Bob M3: Vroom + Sandbox Tier 2 + YOLO Mode

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the three biggest remaining pieces of the talk's vision so unattended overnight operation is actually safe and useful:

- **Vroom (spec §6.5):** continuous post-merge audit loop. Parallel auditor pool emits SARIF; coalescer dedupes/clusters; triage gate; fix-McLoops on `vroom/<id>` branches; auto-merge clean small fixes.
- **Sandbox Tier 2 (spec §6.10):** Docker dev container per feature with mounted worktree, network allowlist, and resource caps. Required by YOLO.
- **YOLO mode (spec §6.9):** single-flag opt-in for unattended runs. Auto-approves Vroom triage at/above severity threshold, feeds Inconclusive results back into the loop (bounded), requires Tier 2+ sandbox, requires `--max-cost`.

**Deferred to M4:** remaining verifiers (`js_jest`, `js_vitest`, `go_test`, `rust_cargo`, `playwright_ui`, `ml_training`, `cli_smoke`), OpenTelemetry observability, sandbox tier 3 (Devcontainer), `.shp`/`.gpkg` support in the geospatial verifier, real ralph-wiggum plugin integration for Duplo iterative refinement.

**Spec:** `docs/superpowers/specs/2026-05-06-bob-design.md`. **Builds on** M1 (`0005c59`), M2a (`7570a9a`), M2b (`ab11a41`), M2 (`7df27cd`), M2.1 (`a78e025`).

**Tech Stack:** Python 3.10+, existing deps + `docker` Python SDK (or shell out to `docker` CLI), `sarif-om` (or hand-roll the SARIF subset).

---

## Why these three together

- Vroom alone is reactive — it surfaces findings but a human would still triage them. To make Vroom *autonomous overnight*, YOLO is required.
- YOLO alone is dangerous without OS-level isolation; a confused agent can install bad packages or hit external services. Sandbox tier 2 is the cost of YOLO.
- Sandbox tier 2 alone is useful but unforced — most users won't enable it without a reason. YOLO + Vroom create the reason.

Shipping all three together = the first time Bob delivers the talk's "go to sleep, come back to a triaged queue and merged fixes" pitch.

---

## File structure

**Created:**
```
claude_orchestrator/bob/
  vroom/
    __init__.py
    daemon.py                 # long-running process; cycle scheduler
    auditor_pool.py           # parallel subprocess runner
    coalescer.py              # SARIF dedupe + cluster + severity
    triage.py                 # HITL triage gate
    auditors/
      __init__.py
      claude_architect.py     # Claude-based architecture/design auditor
      codex_security.py       # Codex-based security/edge-case auditor
      semgrep.py              # Semgrep static analysis wrapper
      pr_agent.py             # PR-Agent (Qodo) wrapper
  sandbox/
    __init__.py
    executor.py               # SubprocessExecutor protocol + dispatcher
    host.py                   # Tier 1 (existing behavior, formalized)
    docker.py                 # Tier 2: Docker dev container per call
  yolo.py                     # YOLO config object + invariant enforcement

tests/bob/
  test_vroom_coalescer.py
  test_vroom_auditor_pool.py
  test_vroom_triage.py
  test_vroom_daemon.py
  test_sandbox_docker.py
  test_yolo_mode.py
```

**Modified:**
- `claude_orchestrator/bob/coordinator.py` — start/stop Vroom thread; pass YOLO config through
- `claude_orchestrator/bob/wiring.py` — wire Vroom + sandbox tier + YOLO
- `claude_orchestrator/bob/cli.py` — add `bob vroom`, `bob vroom stop`; add `--vroom`, `--yolo`, `--sandbox docker` flags to `bob run`
- `claude_orchestrator/bob/mcloop/runner.py` — accept a `SubprocessExecutor` so runs can route through Docker
- `claude_orchestrator/bob/verifiers/protocol.py` — extend `Verifier` with optional `applies_to_path(path: Path) -> bool` for Vroom auditor file-pattern triggers (M2's auditor allocation strategy)
- `claude_orchestrator/bob/hitl/gates.py` — add `VroomTriageGate` (third HITL gate; spec §6.8)
- `claude_orchestrator/models.py` — extend `Finding.status` with `"merging"` and `"merged"`; add `RunScope.includes_vroom` field
- `pyproject.toml` — add deps: `docker>=7`, `semgrep` (CLI), `sarif-om>=1.0.4` (or omit and hand-roll)

---

## Phase A — Sandbox Tier 2 (must ship before YOLO)

### Task 1: Sandbox executor protocol

**Files:**
- Create: `claude_orchestrator/bob/sandbox/__init__.py`
- Create: `claude_orchestrator/bob/sandbox/executor.py`
- Create: `claude_orchestrator/bob/sandbox/host.py`
- Create: `tests/bob/test_sandbox_executor.py`

Define a small `SubprocessExecutor` protocol with one method `run(cmd, *, cwd, env, timeout) -> CompletedProcess`. Implement `HostExecutor` (Tier 1) that just calls `subprocess.run`. McLoop runner threads through this so Tier 2 (Docker) can replace it.

Tasks:
- [ ] Failing test for `HostExecutor.run`
- [ ] Implement `executor.py` with the protocol
- [ ] Implement `host.py` as the default
- [ ] Wire McLoop runner to accept an executor (default: HostExecutor)
- [ ] Update `tests/bob/test_mcloop_runner.py` to assert default is HostExecutor; existing tests still pass
- [ ] Commit: `feat(bob): SubprocessExecutor protocol and HostExecutor (tier 1)`

### Task 2: DockerExecutor (sandbox tier 2)

**Files:**
- Create: `claude_orchestrator/bob/sandbox/docker.py`
- Create: `tests/bob/test_sandbox_docker.py`

Implementation per spec §6.10:
- Per-call ephemeral container (cold start ~2-5s, acceptable)
- Worktree mounted at `/workspace`
- Read-only credential drop at `/secrets` (Anthropic key + GitHub token; opt-in others)
- Outbound network allowlist (Anthropic, OpenAI, GitHub, npm, PyPI; configurable)
- CPU + memory caps
- Default image per language detected; user-supplied `bob.dockerfile` override

Tests use Docker's `docker run --rm hello-world` style smoke tests; gate behind `BOB_DOCKER_TESTS=1` env var so CI doesn't require Docker daemon.

Tasks:
- [ ] Failing test (smoke run; gated by env var)
- [ ] Implement DockerExecutor (shell to `docker` CLI; minimal `subprocess` wrapper)
- [ ] Default image detection (`bob.dockerfile` > pyproject.toml-detected Python > Node 20 > Go 1.23 > Rust)
- [ ] Network allowlist via `--network=bob-allowlist` (named user-defined network)
- [ ] Resource caps: `--cpus=4 --memory=8g` defaults; configurable via `bob.toml`
- [ ] Tests pass (smoke gated)
- [ ] Commit: `feat(bob): DockerExecutor (sandbox tier 2)`

### Task 3: Wire sandbox tier into CLI + Coordinator

**Files:**
- Modify: `claude_orchestrator/bob/wiring.py`
- Modify: `claude_orchestrator/bob/cli.py`
- Modify: `tests/bob/test_wiring.py`

- [ ] Add `--sandbox {host,docker}` flag to `bob run`
- [ ] Add `BOB_SANDBOX_TIER` env var (precedence: flag > toml > env > default `host`)
- [ ] `wiring.build_coordinator` builds the appropriate executor
- [ ] McLoop runner uses the executor for the `claude -p` call
- [ ] Add wiring test asserting executor selection
- [ ] Commit: `feat(bob): wire --sandbox docker through CLI to McLoop runner`

---

## Phase B — Vroom

### Task 4: Finding type + `Finding.status` extension

**Files:**
- Modify: `claude_orchestrator/models.py`
- Modify: `tests/bob/test_models.py`

- [ ] Extend `Finding.status` Literal to include `"merging"` and `"merged"` (in addition to existing `open|in_progress|resolved|wontfix`)
- [ ] Test round-trip
- [ ] Commit: `feat(bob): extend Finding.status with merging/merged for Vroom`

### Task 5: SARIF coalescer

**Files:**
- Create: `claude_orchestrator/bob/vroom/coalescer.py`
- Create: `tests/bob/test_vroom_coalescer.py`

Coalescer takes a stream of `Finding` objects from N auditors and:
- Dedupes by `(rule_id, location.uri, location.start_line)` fingerprint
- Clusters related findings (same file + nearby lines)
- Assigns severity based on max severity in cluster
- Emits a deduped, severity-sorted list

Tasks:
- [ ] Failing tests (dedup, cluster, severity)
- [ ] Implement coalescer (~100 LOC)
- [ ] Commit: `feat(bob): SARIF coalescer (dedup + cluster + severity)`

### Task 6: Auditor pool runner

**Files:**
- Create: `claude_orchestrator/bob/vroom/auditor_pool.py`
- Create: `claude_orchestrator/bob/vroom/auditors/{__init__.py,claude_architect.py,codex_security.py,semgrep.py}`
- Create: `tests/bob/test_vroom_auditor_pool.py`

Auditor pool runs N auditors in parallel (concurrent subprocesses or asyncio). Each auditor takes a workspace path + a list of changed files, returns SARIF.

Tasks:
- [ ] Failing tests (with stub auditors)
- [ ] AuditorPool.run() with `concurrent.futures.ProcessPoolExecutor`
- [ ] Auditor protocol: `id`, `triggers_on(changed_files: list[Path]) -> bool`, `audit(workspace) -> list[Finding]`
- [ ] Implement Semgrep auditor (subprocess wrapper around `semgrep --config auto`)
- [ ] Implement Claude architect auditor (Anthropic call with structured output)
- [ ] Implement Codex security auditor (OpenAI call with structured output)
- [ ] Commit: `feat(bob): Vroom auditor pool with Semgrep + Claude + Codex auditors`

### Task 7: Vroom triage gate

**Files:**
- Modify: `claude_orchestrator/bob/hitl/gates.py`
- Create: `tests/bob/test_vroom_triage.py`

Third HITL gate. User reviews findings, picks which to fix-attempt. Default: shown only when ≥2 auditors agree (consensus rule from spec §6.5).

Tasks:
- [ ] Add `VroomTriageGate` to gates.py
- [ ] User-facing format: per-cluster summary with severity, file, message, "approve / skip / wontfix"
- [ ] Test with monkeypatched stdin
- [ ] Commit: `feat(bob): VroomTriageGate (third HITL gate)`

### Task 8: Fix-McLoop on vroom/<id> branches

**Files:**
- Create: `claude_orchestrator/bob/vroom/fix_loop.py`
- Create: `tests/bob/test_vroom_fix_loop.py`

For each approved finding, spawn an isolated McLoop on a `vroom/<finding-id>` branch with the finding as the spec. Verifier gate: original feature's verifier + new finding's regression check. Auto-merge if clean and small (≤100 lines, ≤5 files).

Tasks:
- [ ] Failing test
- [ ] Implement fix-loop driver (reuses McLoopRunner)
- [ ] Auto-merge logic with diff-size guard
- [ ] PR creation via `gh` CLI as fallback
- [ ] Commit: `feat(bob): Vroom fix-loop with auto-merge guards`

### Task 9: Vroom daemon + cycle scheduler

**Files:**
- Create: `claude_orchestrator/bob/vroom/daemon.py`
- Create: `tests/bob/test_vroom_daemon.py`

Long-running daemon. Cycle triggers (spec §6.5):
- Post-receive hook on `main` (file watcher)
- Configurable timer (default: every 30 minutes when no recent activity)
- On-demand via `bob vroom now`

Manages own PID file + signal handlers. Started as child of Coordinator (via `bob run --vroom`) or standalone (via `bob vroom`).

Tasks:
- [ ] Failing tests (with mocked auditor pool + clock)
- [ ] Daemon main loop
- [ ] PID file management + clean shutdown
- [ ] `bob vroom now`, `bob vroom stop` subcommands
- [ ] Commit: `feat(bob): Vroom daemon with timer + post-merge + on-demand triggers`

### Task 10: Wire Vroom into Coordinator + CLI

**Files:**
- Modify: `claude_orchestrator/bob/coordinator.py`
- Modify: `claude_orchestrator/bob/wiring.py`
- Modify: `claude_orchestrator/bob/cli.py`

- [ ] Add `RunScope.includes_vroom`
- [ ] When `--vroom`, Coordinator spawns Vroom daemon as subprocess (`start_new_session=True` for clean shutdown)
- [ ] `bob vroom` standalone subcommand
- [ ] `bob vroom stop` subcommand
- [ ] Coordinator's signal handlers propagate to Vroom (SIGTERM the process group)
- [ ] Commit: `feat(bob): wire Vroom into bob run --vroom and bob vroom standalone`

---

## Phase C — YOLO mode

### Task 11: YOLO config + invariant enforcement

**Files:**
- Create: `claude_orchestrator/bob/yolo.py`
- Create: `tests/bob/test_yolo_mode.py`

Per spec §6.9. Single `--yolo` flag with documented downstream effects:
- Post-Duplo HITL: auto-approve if meta-rubric passed
- Orchestra disagreement: auto-take judge's tentative verdict; abandon to vroom-style finding on abstain
- Vroom triage: auto-approve at/above `BOB_YOLO_VROOM_SEVERITY` (default `high`)
- McLoop Inconclusive: feed back into loop as context (bounded by `BOB_YOLO_MAX_INCONCLUSIVE`, default 3)
- Sandbox: required tier 2 (refuse if tier 1)
- `--max-cost`: required (refuse if absent)

Tasks:
- [ ] `YoloConfig` dataclass + invariant validation
- [ ] Plumb through Coordinator/wiring
- [ ] Tests for each invariant
- [ ] Commit: `feat(bob): YOLO mode (single-flag autonomy with documented downstream effects)`

### Task 12: YOLO end-to-end smoke test

**Files:**
- Create: `tests/bob/test_yolo_e2e.py`

Stub-mode E2E: `bob run --yolo --sandbox docker --max-cost 10 --inputs spec.md`. Verifies the YOLO config is honored (gates auto-approve, sandbox tier 2 invoked, etc.). Doesn't require real Docker — uses a faked DockerExecutor for the test.

Tasks:
- [ ] Failing test
- [ ] Pass
- [ ] Commit: `test(bob): end-to-end YOLO smoke test`

---

## Phase D — Demo (optional but high value)

### Task 13: Demo — overnight YOLO on a tiny project

**Files:**
- None (manual smoke)

A documented procedure for the user to run themselves:

```bash
mkdir /tmp/bob-yolo-demo
cd /tmp/bob-yolo-demo
git init -b main
# Write a small spec
cat > spec.md <<EOF
# Demo
## Motivation
Test YOLO end-to-end overnight.
## Features
### F1: validate-csv
- task_type: data_analysis
- verifier: data_analysis
- success_criteria:
  - reads input.csv
  - asserts column 'value' is numeric
- description: Read input.csv, validate column types, write report.csv
EOF

# Set API keys, ensure docker is running
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...

# Run YOLO with --vroom for continuous audit
bob run --yolo --sandbox docker --max-cost 25 \
        --inputs spec.md --vroom
```

Don't run real Anthropic+OpenAI APIs in the test suite. This is a procedure to be run by the user when ready.

---

## Self-review

1. **Spec coverage:**
   - §6.5 Vroom → Phase B (Tasks 4-10)
   - §6.9 YOLO → Phase C (Tasks 11-12)
   - §6.10 sandbox tier 2 → Phase A (Tasks 1-3)
   - Deferred from spec: tier 3 (Devcontainer), §10 OpenTelemetry, remaining §6.6 verifiers, ralph-wiggum plugin Duplo, `.shp`/`.gpkg` geospatial → all M4

2. **Placeholder scan:** None.

3. **Type consistency:**
   - `Finding.status` extended; existing tests adjusted in Task 4
   - `Verifier` protocol extended in Task 6 with optional method (backward-compatible)
   - `RunScope.includes_vroom` added in Task 10 (default False; existing callers unchanged)

4. **Ambiguity check:**
   - Auditor pool concurrency: process pool vs asyncio. Default: process pool because each auditor is subprocess-heavy (Semgrep, Claude, Codex). Documented in Task 6.
   - Vroom auto-merge threshold: 100 lines / 5 files. Configurable. Documented in spec §6.5.
   - YOLO mandatory `--max-cost`: refuses to start without one. Documented in Task 11.

---

## Estimated cost

- ~13 tasks (12 implementation + 1 demo procedure)
- Comparable to M2's scope (10 tasks); somewhat heavier due to Vroom's surface area
- Budget: ~3-4 hours of subagent execution + ~1 hour of merge/dogfood polish

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-05-08-bob-m3-vroom-sandbox-yolo.md`. Execute via `superpowers:subagent-driven-development` whenever you're ready.
