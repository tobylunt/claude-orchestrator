# Bob: A Spec-Driven, Multi-Phase, Multi-Model Coding Orchestrator

**Status:** Design / brainstorming output
**Date:** 2026-05-06
**Author/owner:** Tobias Lunt (with Claude)
**Codebase:** `claude-orchestrator` (extends existing Python package)

---

## 1. Goal

Build an open-source orchestrator that puts the four roles described in the talk *"Bob: A Tour"* into a Claude Code-compatible Python tool that operates on any user's repository. The four roles:

- **Duplo** — turns multimodal inputs (URLs, PDFs, screenshots, video, prose) into a phased, verifiable spec.
- **McLoop** — autonomously builds what Duplo planned, fresh context per task, tests/lint after every change, only clean code committed.
- **Orchestra** — multi-model adversarial review: Claude and Codex argue, a judge synthesizes, no diff merges without surviving debate.
- **Vroom** — continuous post-merge auditor pool: parallel auditors find issues, coalesce findings, propose fixes on branches, gate on verification, merge survivors.

The system stands on community giants (Anthropic's `ralph-wiggum` plugin, AutoGen GroupChat, PR-Agent, Semgrep, SARIF, the Hu-2025 KS-stability detector) for solved sub-problems. It builds the integration spine and the talk's specific opinions about phase composition.

**Implementation note:** this spec describes the full system. Implementation will be staged via a separate plan (the writing-plans skill output). Expected milestones: (1) thin end-to-end slice with one verifier and stub Orchestra/Vroom; (2) full verifier roster + AutoGen-backed Orchestra; (3) Vroom auditor pool + SARIF coalescer; (4) sandbox tiers 2/3 + observability polish.

## 2. Non-goals

- Replace Claude Code or Codex CLI as primary coding tools — we orchestrate them.
- Reinvent agent execution frameworks — we wrap, we don't replace.
- Build a SaaS product or web UI for v1.
- Support every coding language with bespoke verification — we ship standard adapters and provide an extension surface.
- Take on the LangChain ecosystem maintenance burden — we deliberately do not depend on LangGraph (see §10 for the trade-off).

## 3. Architectural overview

```
                ┌──────────────────────────────────────────────────────┐
                │                .bob/  (state on disk)                 │
                │                                                       │
                │  PROJECT-LEVEL (cross-feature):                       │
                │    spec.md          findings.jsonl   cursor.json     │
                │    run-log.jsonl    inputs/                          │
                │                                                       │
                │  PER-FEATURE (one dir each, auto-discovered):         │
                │    features/001-auth/  features/002-dashboard/  ...  │
                │      └─ spec.md, state.json, activity.md,            │
                │         failed_attempts.md, debate.json,             │
                │         verifier-results.jsonl                        │
                │                                                       │
                │  WORKTREES:                                           │
                │    worktrees/001-auth/  worktrees/002-dashboard/  ...│
                └──────────────────────────────────────────────────────┘
                                     ▲
                                     │
   user inputs ────► Duplo ──► spec ──► Coordinator (Python, ~100 LOC)
                                     │
                                     ├──► McLoop  (per feature, fresh worktree, fresh ctx per iter)
                                     │      └──► verifier adapter ──► commit if Ok
                                     │
                                     ├──► Orchestra  (per feature, before merge)
                                     │      └──► AutoGen GroupChat: Claude ⇄ Codex ⇄ Judge
                                     │            └──► KS-stability termination
                                     │
                                     └──► Vroom  (long-running, parallel)
                                            └──► auditor pool ──► SARIF coalescer ──► triage ──► fix-McLoop ──► gate ──► merge

   Cross-cutting:
     - hooks/ enforces tool-use policy on every agent call (PreToolUse pattern-match)
     - sandbox tier wraps each agent invocation (hooks-only / Docker / Devcontainer)
     - HITL gates: post-Duplo / Orchestra-disagreement / Vroom-triage (default-on, opt-out)
     - OpenTelemetry traces shipped to Phoenix (or any OTLP backend)
```

State lives in git plus a `.bob/` directory using a **two-level layout**: project-level files for things that span features (master spec, cross-cutting findings, current cursor, run log, original inputs) and a per-feature subdirectory under `.bob/features/<id>-<slug>/` for everything inherently scoped to one feature (its spec section, status, activity log, failed-attempts log, debate transcript, verifier results). Features are auto-discovered by walking the directory; numeric prefixes preserve order. No manifest file — the directory listing is the source of truth, which avoids the manifest-out-of-sync failure mode. (See §3.1 for the concurrency model that makes this safe for unattended overnight runs.)

The coordinator is plain Python (~100 LOC). Vroom runs as a long-lived process alongside development. There is no event bus, no SQLite, no LangGraph — just files, git, subprocess calls, and a small state machine.

**Why two levels:** project-level files answer "where are we right now and what's cross-cutting?" Per-feature dirs answer "what's the full history of this one feature?" The split also means parallel processes (e.g., McLoop on feature 3, Vroom auditing main) write into disjoint paths, eliminating concurrent-write conflicts on shared files.

## 3.1 Concurrency model & lifecycle

Bob is designed to run unattended for hours. The state layout above plus a small set of disciplines makes overnight operation safe.

**Single-writer-per-file discipline:**

| File | Sole writer |
|---|---|
| `.bob/cursor.json` | coordinator |
| `.bob/run-log.jsonl` | coordinator (append-only) |
| `.bob/findings.jsonl` | Vroom coalescer (append-only) |
| `.bob/spec.md`, `.bob/features/*/spec.md` | Duplo (immutable post-Duplo) |
| `.bob/features/<id>/state.json` | the process working that feature |
| `.bob/features/<id>/{activity,failed_attempts}.md`, `verifier-results.jsonl` | that feature's McLoop worker (append-only where possible) |
| `.bob/features/<id>/debate.json` | Orchestra |

**Atomicity rules:**
- **Append-only JSONL files** are opened with `O_APPEND`, records kept under 4 KB. POSIX guarantees the syscall is atomic at that size, so concurrent appenders interleave records cleanly without corruption.
- **Mutable JSON files** (`cursor.json`, `state.json`) use atomic write: `tempfile + fsync + rename`. Readers see either the old or new file, never a half-written one.
- **Markdown logs** (`activity.md`, `failed_attempts.md`) are append-only with `O_APPEND`; never edited in place by Bob. Users may safely edit by hand; the next iteration reads the updated content.

**Process-level locking.** A `.bob/.bob.lock` PID file prevents two `bob run` invocations from sharing one `.bob/` directory. Stale-PID detection (PID exists but the process is dead) clears the lock automatically on startup.

**Lifecycle / shutdown:**
- The coordinator is the supervisor. It catches `SIGINT`, `SIGTERM`, and `SIGHUP`, sets a `_shutdown_requested` flag, and gracefully terminates each child process before exiting.
- Vroom is started as a child of the coordinator (via `bob run --vroom`) using `start_new_session=True` so it inherits its own process group. The coordinator sends `SIGTERM` to the entire group on shutdown; Vroom catches it, finishes the current cycle's coalesce step, persists state, and exits.
- Standalone `bob vroom` (no coordinator) writes `.bob/vroom.pid`. `bob vroom stop` reads the PID, sends SIGTERM, waits for clean exit, then removes the file. Stale-PID detection on next startup recovers from kill -9 / power loss.
- Each McLoop subprocess (`claude -p`) is started with a per-iteration timeout. If it exceeds the timeout or the parent receives a shutdown signal, the parent sends `SIGTERM` then `SIGKILL` after a configurable grace period.
- An `atexit` handler in the coordinator ensures cleanup even on uncaught Python exceptions.
- Second `Ctrl-C` during graceful shutdown forces immediate exit (existing behavior in `orchestrator.py`).

**Recovery from crash mid-iteration.** The coordinator persists its position to `cursor.json` after every state transition. On restart it reads the cursor, walks `.bob/features/`, observes which feature was in-flight (via that feature's `state.json`), and either resumes from the last clean boundary (last `Ok` in `verifier-results.jsonl`) or escalates to HITL for ambiguous cases.

**What's NOT supported in v1:**
- Two coordinators sharing a `.bob/` directory (the lock refuses).
- Distributed Vroom across machines (single-host only).
- Cross-feature concurrent McLoop (one feature at a time; concurrency lives at the auditor pool inside Vroom).

## 4. Reuse map (standing on giants)

| Concern | Plug in (external) | Build (internal) |
|---|---|---|
| Worker / coding agent | Claude Code SDK + CLI; Codex CLI | Prompt templates, worktree adapter |
| Autonomous loop primitive | **Huntley bash-loop pattern** (per-feature, fresh `claude -p` per iter) | Python coordinator wrapping the loop |
| Single-document refinement | **Anthropic `ralph-wiggum` plugin** (in-session, growing context) — used inside Duplo only | Prompt + completion-promise convention |
| Multi-agent debate | **Microsoft AutoGen** GroupChat | Agent definitions, debate prompt template |
| Debate convergence | **KS-statistic stability detection** (Hu et al. 2025, arXiv 2510.12697) | ~50 LOC implementation |
| Audit findings format | **SARIF** (OASIS standard) | Coalescer/deduper that emits SARIF |
| Auditor backends | **PR-Agent v0.32 (Qodo)**, **Semgrep**, plus LLM auditors (Claude/Codex) with structured output | Auditor pool runner, severity policy |
| PR / branch ops | `git worktree`, `gh` CLI | Thin Python wrapper |
| Permissions / sandbox | Claude Code permission modes; **existing `hooks.py`**; Docker / Devcontainer | Sandbox-tier dispatcher |
| Observability | **OpenTelemetry** → **Phoenix** (Arize, OSS) or Langfuse | Span instrumentation at phase/agent boundaries |
| Spec ingestion (multimodal) | Anthropic Python SDK (vision-enabled) | Pydantic schema for Spec; LLM-as-judge for completeness |

**Estimated new code:** ~800–1000 LOC plus prompts and adapter implementations.

## 5. Phase contracts (extends `models.py`)

```python
class TaskType(StrEnum):
    UI = "ui"
    DATA_ANALYSIS = "data_analysis"
    GEOSPATIAL = "geospatial"
    LIBRARY = "library"
    CLI = "cli"
    INTEGRATION = "integration"
    ML_TRAINING = "ml_training"
    INFRASTRUCTURE = "infrastructure"
    CUSTOM = "custom"  # requires verifier_id

class VerificationPlan(BaseModel):
    verifier_id: str           # e.g. "python_pytest", "playwright_ui", "geospatial"
    success_criteria: list[str] # human-readable, judged for rubric coverage
    required_tools: list[str]  # e.g. ["pytest", "playwright", "gdal"]

class Spec(BaseModel):
    title: str
    motivation: str
    inputs: list[InputRef]     # multimodal references (file paths, urls)
    features: list[Feature]
    rubric_meta_check_passed: bool

class Feature(BaseModel):
    id: int
    name: str
    description: str
    task_type: TaskType
    verification_plan: VerificationPlan
    branch: str | None
    worktree_path: Path | None
    status: FeatureStatus
    attempts: int
    cost_usd: float
    last_error: str | None

class Verdict(BaseModel):
    feature_id: int
    decision: Literal["approve", "reject", "abstain"]
    confidence: float
    debate_log_path: Path
    judge_reasoning: str

class Finding(BaseModel):
    """SARIF-compatible subset."""
    rule_id: str
    severity: Literal["info", "low", "medium", "high", "critical"]
    location: SARIFLocation
    message: str
    proposed_fix: Path | None
    auditor: str               # which auditor produced it
    fingerprint: str           # for dedupe across auditors
    status: Literal["open", "in_progress", "resolved", "wontfix"]
```

**Extensibility & self-healing of contracts:**
- `TaskType` is intentionally an open enum: the `CUSTOM` value plus a non-empty `verifier_id` covers any task type without code change. Built-in values are conveniences, not a closed list. Custom verifiers register themselves with their own `task_type` string at startup.
- For v1 we **do not** allow agents to mutate the contracts themselves. A "self-healing" architect agent that proposes new `TaskType` values, new fields on `Feature`, or new verifier interfaces is a v1.2+ idea: the agent would emit a *suggestion log* (`.bob/contract-suggestions.jsonl`) reviewed by the user, never auto-modify `models.py`. Self-modifying contracts in a tool that runs unattended is a foot-gun: a confused agent could break in-flight orchestrations or corrupt state files of any other Bob installation that pulls the change.
- The pragmatic v1 plan: ship the built-in `TaskType` values, watch which ones get used and which don't during initial dogfooding, prune/rename in v1.1 based on observed usage. Treat the first month of operation as the input to a manual contract audit. If a clear pattern emerges that contracts need to evolve faster than human iteration, that's evidence for v1.2's suggestion-log mechanism.

## 6. Component design

### 6.1 Coordinator (`bob/coordinator.py`, ~100 LOC)

Plain Python state machine. Walks `.bob/features/` (sorted by numeric prefix) to enumerate features, advances each through phases, dispatches Vroom in parallel. Persists `.bob/cursor.json` (`{run_id, current_phase, current_feature_id, last_event_at, total_cost_usd}`) for resumability and `.bob/run-log.jsonl` (append-only event stream) for project-level history.

```python
class Coordinator:
    def run(self, scope: RunScope):
        if scope.includes_duplo:
            self.run_duplo()
        with vroom_thread(self.config) if scope.vroom else nullcontext():
            for feature in self.features():
                if feature.status >= FeatureStatus.MERGED:
                    continue
                self.run_mcloop(feature)         # bash-loop pattern
                self.run_orchestra(feature)       # AutoGen debate
                self.merge_or_revert(feature)
        self.finalize()
```

State transitions are explicit. HITL gates are first-class methods returning user decisions. Resumability is "read cursor, skip-ahead."

### 6.2 Duplo (`bob/duplo/`)

Multimodal Anthropic API call (vision-enabled) consumes user inputs and emits a structured `Spec`. Iterative refinement uses the **official `ralph-wiggum` plugin** (in-session refinement is the right shape here — one document, growing context, fast settle).

Termination requires *all of*:
1. JSON schema valid (Pydantic).
2. Per-feature `verification_plan` populated and `task_type` declared.
3. **Meta-rubric check passes:** LLM-as-judge confirms each feature's `verification_plan` actually covers its `success_criteria`. *Failure here halts Duplo loud — never passes a feature with insufficient rubric coverage.*
4. User signs off via HITL gate (LGTM-style approval; iteration on the spec is expected here, not skipped).

Output: `.bob/spec.md` (master spec, human-readable) plus a per-feature directory `.bob/features/<NNN>-<slug>/` for each feature, containing `spec.md` (this feature's section, copied from the master for fast access), `state.json` (`{id, name, task_type, verification_plan, status, attempts, cost_usd, branch, worktree_path, last_error, updated_at}`), and empty stubs for `activity.md`, `failed_attempts.md`, `debate.json`, `verifier-results.jsonl` ready for downstream phases to populate.

### 6.3 McLoop (`bob/mcloop/`)

Per-feature, in a fresh `git worktree` under `.bob/worktrees/<NNN>-<slug>/`. Uses the **Huntley bash-loop pattern, not the in-session plugin.** Each iteration is a fresh `claude -p` subprocess with a stable prompt template that re-reads:
- `.bob/spec.md` (master spec)
- `.bob/features/<NNN>-<slug>/spec.md` (this feature's section)
- `.bob/features/<NNN>-<slug>/activity.md`
- `.bob/features/<NNN>-<slug>/failed_attempts.md`

**Prompt template principles** (synthesized from Anthropic's Claude Code best-practices docs, Geoffrey Huntley's Ralph technique, the official `ralph-wiggum` plugin README, and 2026 community practice):

1. **Length 200-400 words.** Prompts over ~2000 words cause attention bias toward early instructions; trim ruthlessly.
2. **Role definition first.** Open with identity ("You are a focused builder advancing one small slice toward EXIT_SIGNAL.") before listing rules. Identity-before-instructions produces judgment, not mechanical rule-following.
3. **Explicit completion criteria with promise tag.** End with: *"When the feature is fully implemented, the verifier returns Ok, and tests/lint pass, output `<promise>EXIT_SIGNAL</promise>` as the final line."* Mirrors the official ralph-wiggum convention.
4. **Failure-mode instructions.** *"If a tool call fails, log the error to `failed_attempts.md` and try a different approach. If the verifier returns Inconclusive, do NOT continue — exit immediately with the Inconclusive output."* Without these, agents improvise badly when tools fail.
5. **Stopping condition for the iteration.** *"Do exactly ONE focused unit of work this iteration. Do not try to finish the feature in one pass. The loop is your friend."*
6. **Self-correction structure.** Implicit TDD framing: *"Write failing test → implement → run verifier → if Fail, debug and try again or log → if Ok, commit → append to activity.md."*
7. **Failures are data.** *"If you tried something that didn't work, write the precise failure mode and why to `failed_attempts.md` before moving on. Future-you reads this file."*
8. **Specification over vagueness.** Concrete success criteria pulled directly from the feature's `spec.md` and `verification_plan`. Quote them, do not paraphrase.
9. **Spec/plan/activity files are memory.** *"Read these every iteration. They are your only memory of past iterations. Treat them as load-bearing."*

The full template lives in `bob/mcloop/prompts/iteration.md` and is reviewable per-project; users override via `bob.toml`.

Per-iteration loop body:

```
1. Spawn `claude -p` subprocess with prompt_template + feature
2. Subprocess does ONE focused pass:
     - read master spec, feature spec, activity, failed_attempts
     - pick smallest unresolved item
     - edit code in the worktree
     - run verifier adapter (see §6.7), append result to verifier-results.jsonl
     - if Ok: commit, append to activity.md
     - if Fail: append to failed_attempts.md with reason
     - if Inconclusive: HALT LOUD, exit with escalation signal
     - emit EXIT_SIGNAL when feature is complete
3. Coordinator inspects exit signal + verifier last result
4. If EXIT_SIGNAL: feature done
5. If max-iterations reached: pause, request HITL
6. If Inconclusive escalation: pause, surface verifier output to user
7. Otherwise: next iteration
```

**Sandbox tier** (chosen at run config):
- **Tier 1 (default):** `hooks.py` + `git worktree`. No new infrastructure.
- **Tier 2 (`--sandbox docker`):** worktree mounted into a Docker dev container. Real OS isolation.
- **Tier 3 (`--sandbox devcontainer`):** ephemeral Devcontainer, no host network/fs access except the mounted worktree.

**Per-iteration model:** Sonnet 4.6 by default. Configurable via `--model-mcloop`.

### 6.4 Orchestra (`bob/orchestra/`)

Per-feature, after McLoop emits EXIT_SIGNAL but before merge. Uses **AutoGen GroupChat** with three agents:

- `ClaudeAgent` (Claude Sonnet 4.6 by default) — implementation perspective, defends the diff.
- `CodexAgent` (GPT-5.4 by default) — adversarial review, attacks the diff.
- `JudgeAgent` (Claude Opus 4.7 by default) — synthesizes consensus or flags disagreement.

**Model overrides via `.env`.** All model assignments are configurable via a project-local `.env` file (or matching env vars):

```
BOB_DUPLO_MODEL=claude-opus-4-7
BOB_DUPLO_JUDGE_MODEL=claude-opus-4-7
BOB_MCLOOP_MODEL=claude-sonnet-4-6
BOB_ORCHESTRA_CLAUDE_MODEL=claude-sonnet-4-6
BOB_ORCHESTRA_CODEX_MODEL=gpt-5.4
BOB_ORCHESTRA_JUDGE_MODEL=claude-opus-4-7
BOB_VROOM_AUDITOR_MODEL=claude-haiku-4-5
BOB_VROOM_ESCALATE_MODEL=claude-sonnet-4-6
```

Defaults bias toward the strongest available judge for adversarial review and high-leverage decisions, and toward cheap models for broad-coverage scanning. Override per-phase as new models ship.

Convergence uses **KS-statistic stability detection** (Hu et al. 2025): debate terminates when judgment-distribution stabilizes for 2 consecutive rounds (KS < 0.05), or on explicit consensus, or on `max_rounds` (default 5). Output: `Verdict`, persisted to `.bob/features/<NNN>-<slug>/debate.json` (full transcript) plus a summary entry in the feature's `state.json`.

- `approve` → feature merges to main, worktree archived, status MERGED.
- `reject` → feature returns to McLoop with debate log appended to `activity.md` as additional context. Bounded retries (default: 2 reject-retry cycles per feature; 3rd rejection promotes to HITL).
- `abstain` (KS never stabilizes, no consensus, max rounds reached) → HITL gate: user reviews debate log and tie-breaks.

### 6.5 Vroom (`bob/vroom/`)

Long-running process started by `bob vroom` or `bob run --vroom`. Runs alongside development. **Cycle triggers:** (a) on every push to `main` (git post-receive hook or polling); (b) on a configurable timer (default: every 30 minutes when no recent activity); (c) on demand via `bob vroom now`. Lifecycle and shutdown semantics — including SIGINT/SIGTERM propagation, orphan prevention, and PID-file management for standalone runs — are documented in §3.1. Per cycle:

1. Snapshot `main` at current HEAD.
2. Run **auditor pool in parallel** (concurrent subprocesses, each in its own worktree). Default roster (configurable via `vroom.auditors`):
   - `pr_agent` — PR-Agent v0.32 (general code quality, multi-model).
   - `semgrep` — Semgrep (static analysis, security rules).
   - `claude_architect` — Claude Sonnet auditor for architecture/design.
   - `codex_security` — Codex auditor for security/edge cases.
   - `a11y` — accessibility audit (axe-core for web; markdown/UI heuristics elsewhere).
   - `performance` — performance auditor (looks for N+1 queries, blocking IO, hot-loop allocations).
   - `compliance` — license, secrets-leak, PII-exposure scanning (truffleHog, license-checker).
   - `seo` — SEO auditor for web projects (Lighthouse subset).
   - User-defined task-specific auditors (e.g., `geospatial_validator`, `data_schema_auditor`).

**Auditor allocation strategy (v1):** all configured auditors run every cycle, but each declares a `triggers_on` glob. For example, `a11y` runs only when changed files match `*.{html,tsx,jsx,svelte,vue,astro}`; `seo` runs only on web projects with a known SSR framework; `data_schema_auditor` only when changed files include `*.{ipynb,parquet,csv,sql}`. Cheap auditors (Haiku, Semgrep) run unconditionally; expensive ones gate on file patterns. The rules are static config, not a control-agent decision — simpler, cheaper, predictable. **Revisit after first-use:** if dynamic allocation by a planning agent proves valuable in practice, that becomes a v1.1 enhancement (the contract-suggestion mechanism in §5 is the natural place to surface this).

The a11y/performance/compliance/seo specialist taxonomy is inspired by maestro-orchestrate's specialist roster (see Appendix A); each is opt-in and skipped when irrelevant to the project type.
3. Each auditor emits **SARIF**.
4. **Coalescer:** dedupe by location + fingerprint, cluster related findings, assign severity.
5. Append to `.bob/findings.jsonl` (project-level, append-only; never deleted, marked resolved/wontfix). Findings are project-level rather than per-feature because Vroom audits cross-cutting concerns (architecture drift, accumulated tech debt) that don't always map to a single feature.
6. **HITL triage gate (default-on):** user approves which findings to attempt fixes for.
7. For approved findings: spawn an isolated McLoop worker on a `vroom/<finding-id>` branch with the finding as the spec. *YOLO mode (§6.9) auto-approves findings at or above a configurable severity threshold without HITL, for unattended overnight operation.*
8. Verification gate: must pass the original feature's verifier adapter + the new finding's regression check.
9. Auto-merge if clean and small (default threshold: ≤100 lines changed across ≤5 files; configurable via `vroom.auto_merge.max_lines` and `vroom.auto_merge.max_files`); otherwise open PR via `gh`.

**Per-auditor model:** Haiku 4.5 first pass for cheap broad coverage; flagged findings escalate to Sonnet/Opus. Configurable.

### 6.6 Verification rubric system (`bob/verifiers/`)

**The most important component for preventing silent failures.** Adapter protocol:

```python
class Verifier(Protocol):
    id: str
    applies_to: list[TaskType]

    def required_tools(self) -> list[ToolSpec]:
        """Tools that must be available in the sandbox/host."""

    def preflight(self, workspace: Path) -> PreflightResult:
        """Cheap check: are tools present, configured, project-recognized?"""

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        """Run the verifier. Returns Ok | Fail(reason) | Inconclusive(reason)."""
```

```python
@dataclass
class VerifyResult:
    status: Literal["ok", "fail", "inconclusive"]
    reason: str
    artifacts: list[Path]   # screenshots, logs, reports
    coverage_notes: str | None  # what was/wasn't checked
```

**Halt-loud rule (default mode):** McLoop treats `Inconclusive` as a stop condition that surfaces to the user. It never silently passes through. This is the one rule the loop will never flex on without explicit opt-in.

**YOLO-mode behavior:** in §6.9's YOLO mode an `Inconclusive` is fed back into the loop as additional context (the agent sees: *"the verifier could not verify because X — try addressing X"*) rather than halting. **Crucially, this is bounded:** after `BOB_YOLO_MAX_INCONCLUSIVE` consecutive Inconclusives (default 3) the loop halts even in YOLO mode. This prevents silent rubric drift while still letting unattended runs make progress through transient verifier ambiguity (e.g., a data verifier saying "sample size too small to assert" the first time).

**Built-in verifiers:**
- `python_pytest` — runs project's pytest with coverage.
- `js_jest` / `js_vitest` — runs project's tests.
- `go_test`, `rust_cargo` — language-native testing.
- `lint_universal` — runs configured linter (ruff, eslint, gofmt, etc.).
- `playwright_ui` — Playwright/browser MCP: launches dev server, screenshots routes, asserts no console errors, no failed network requests.
- `data_analysis` — runs pytest + Hypothesis property-based tests + dataset shape/schema asserts (great-expectations or pandera) + notebook regression via papermill.
- `geospatial` — spatial bounds, projection consistency (CRS), topology validation (shapely make_valid), cardinality checks at scale (sample-based).
- `ml_training` — training metric stability across seeds; basic sanity checks (loss decreases, validation tracks).
- `cli_smoke` — runs CLI against documented examples, asserts exit codes + output schema.

**Custom verifiers:** users register via Python entry point `bob.verifiers` or drop a module in `~/.bob/verifiers/`. Discovery at startup.

**Meta-rubric check** (Duplo phase):
For each `Feature`, an LLM-as-judge call (Opus 4.6) is asked: *"Given the feature's success_criteria and assigned verifier_id, does the verifier actually cover the criteria? Output `adequate | inadequate(missing: [...])`."* If `inadequate`, Duplo halts and asks the user to either add criteria the verifier covers or specify a custom verifier.

**Refusal to start:** McLoop refuses to start a feature whose `task_type` has no matching verifier. No silent fallback.

### 6.7 Hooks / safety layer (`bob/hooks/`)

Existing `hooks.py` (with the recent `Comprehensive Bash security hook`, `Allowlist-based recursive rm`, `Auto-approve all standard tools` work) is promoted to `bob/hooks/` and wired as the policy engine for *all* agent calls in *all* phases. PreToolUse hooks pattern-match dangerous operations and block them before execution.

This is defense-in-depth with the sandbox tier: hooks block at the API level, sandbox blocks at the OS level. **Hook policies are expected to need iteration.** Treat the initial set as a baseline to observe, not a finished policy. Every blocked-call event is logged to `.bob/run-log.jsonl` for periodic review; over time, surprising blocks (legitimate work blocked) and missed blocks (dangerous calls allowed) refine the rules. We should plan a first-week review cadence after rollout.

### 6.8 HITL gates (`bob/hitl/`)

Three default-on gates (extending `human_input.py`):

1. **Post-Duplo:** user reviews and approves the spec + verification plan before McLoop starts.
2. **Orchestra disagreement:** when KS never stabilizes / no consensus / max rounds reached, user is shown the debate log and tie-breaks.
3. **Vroom triage:** for each finding cluster, user approves or skips fix-attempt.

Each gate disable-able per run with `--no-gate <name>`. Default-on because the talk's premise is *"expert in the loop at the right moments,"* not *"expert in the loop never."*

### 6.9 YOLO mode (`bob run --yolo`)

YOLO mode is the explicit opt-in for fully unattended overnight operation. It is deliberately a single flag with documented downstream effects rather than a sprawl of independent tunables — the user makes one decision ("I am committing to autonomy tonight") and Bob applies a coherent policy.

**What `--yolo` changes:**

| Component | Default behavior | YOLO behavior |
|---|---|---|
| Post-Duplo HITL gate (§6.8.1) | User must approve spec | Auto-approve if meta-rubric check (§6.6) passed |
| Orchestra disagreement HITL (§6.8.2) | User tie-breaks | Auto-take judge's tentative verdict; if even the judge abstains, abandon feature with a `vroom-style` finding for next-day triage |
| Vroom triage HITL (§6.8.3) | User approves each finding | Auto-approve findings at or above `BOB_YOLO_VROOM_SEVERITY` (default `high`) |
| McLoop `Inconclusive` (§6.6) | Halt loud immediately | Feed back into loop as context; halt only after `BOB_YOLO_MAX_INCONCLUSIVE` consecutive (default 3) |
| Sandbox tier (§6.10) | Tier 1 (hooks + worktree) | Tier 2 (Docker) **required**; YOLO refuses to start in tier 1 |
| `--max-cost` flag | Optional advisory | **Required**; YOLO refuses to start without one |
| Notification on halt | Console message | Configured channel (email / Slack / desktop notification via `BOB_YOLO_NOTIFY`) |

**What `--yolo` deliberately does NOT change:**
- Per-iteration verifier still runs every iteration. YOLO does not skip verification — it changes the response policy.
- Hook policies (§6.7) still enforce tool-use safety. YOLO does not bypass hooks.
- Process locks (§3.1) still apply. YOLO does not allow concurrent runs in one `.bob/` dir.
- Append-only audit logs still record everything. YOLO is auditable.
- The 2-of-N auditor consensus rule for Vroom findings still applies. YOLO doesn't lower the bar for finding *creation*; it lowers the bar for *triage*.

**Failure modes specific to YOLO that warrant respect:**
- Bob may merge a fix that introduces a worse bug (mitigations: Vroom keeps running, the fix's diff is small by `vroom.auto_merge.max_lines`, full git history lets you `git revert` in the morning).
- A confused agent may rack up cost spinning on a stuck feature (mitigations: `--max-cost` hard ceiling, `failed_attempts.md` heuristic for stuck-loop detection, per-feature retry budget).
- A novel-but-flawed verifier might pass invalid work (mitigations: meta-rubric coverage check at Duplo time, post-mortem auditing of any merged change).

YOLO mode is built for the use case the user is actually paying for: *go to sleep, come back in the morning to a list of completed features, merged fixes, and a triage queue of things Bob couldn't decide.*

### 6.10 Sandbox tier details (`bob/sandbox/`)

Each McLoop iteration (and each Vroom auditor) runs inside a sandbox tier chosen at run config. The tier wraps the agent subprocess in different layers of isolation.

**Tier 1 — Hooks + worktree (default).** No new infrastructure. The agent runs as a regular subprocess on the host with `.bob/hooks/` enforcing PreToolUse policy. `git worktree` isolates file edits to the per-feature directory. Adequate for supervised work or experiments where the user is checking in periodically. *Insufficient for YOLO.*

**Tier 2 — Docker dev container (`--sandbox docker`, REQUIRED for YOLO).** Each McLoop subprocess runs inside an ephemeral Docker container.

- **Image strategy:** project-supplied `bob.dockerfile` if present; otherwise a default image per language detected (Python 3.12, Node 20, Go 1.23, Rust stable). Default images carry common dev tools (`git`, `gh`, `ripgrep`, `jq`, `curl`, language runtimes); users override with `bob.toml`'s `sandbox.docker.image`.
- **Mount surface:** the worktree at `/workspace` (rw); a credential drop at `/secrets` (ro, only the keys the agent needs — Anthropic API key, GitHub token, registry credentials, nothing else); `/tmp` (rw, ephemeral, discarded with the container).
- **Network policy:** outbound allowlist by default. Allowed: Anthropic API, OpenAI/Codex API, GitHub API, npm registry, PyPI, configurable additions via `sandbox.docker.network.allow`. Everything else blocked at the iptables/firewall level. This prevents an agent that picks up a malicious dependency from exfiltrating data.
- **Resource limits:** CPU and memory caps via Docker's native flags (defaults: 4 CPUs, 8 GB; configurable). Prevents runaway loops from starving the host.
- **Container lifecycle:** one container per McLoop iteration (cold start cost is ~2-5s, acceptable). Container destroyed on iteration exit. State persists via the bind-mounted worktree.
- **Hooks still apply** inside the container (defense-in-depth: hooks block at the Anthropic API level, container blocks at the OS level).
- **Image caching:** containers reuse the base image layer; only the per-iteration tmpfs is fresh. First run pulls images, subsequent runs are fast.

**Tier 3 — Devcontainer (`--sandbox devcontainer`, for "I really want belt and suspenders" YOLO runs).**

- **Spec:** uses VS Code's `devcontainer.json` standard. Project supplies one at `.devcontainer/devcontainer.json` or Bob generates a default.
- **What's different from Tier 2:** the `devcontainer.json` provisions a full reproducible environment with declared tooling, language runtimes, VS Code-style features, and post-create scripts. Cleaner for projects that already use Codespaces or VS Code Dev Containers — Bob just inherits the existing definition.
- **Network and resource policies:** inherited from `devcontainer.json` plus Bob-applied defaults. Network allowlist still enforced.
- **Trade-off vs Tier 2:** more setup (devcontainer.json must exist or be generated); more standardized (anyone with VS Code can replicate the environment); slightly slower startup (full feature provisioning on first build).

**Choosing a tier:** `--sandbox` flag at the command line; `sandbox.tier` in `bob.toml`; `BOB_SANDBOX_TIER` env var. Order of precedence: flag > project config > env > default. YOLO mode (§6.9) refuses tier 1 and prefers tier 2.

**Implementation:** `bob/sandbox/` ships three executors (`HostExecutor`, `DockerExecutor`, `DevcontainerExecutor`) implementing a common `SubprocessExecutor` interface so the rest of Bob doesn't care which tier is active. `DockerExecutor` shells to the `docker` CLI rather than using `docker-py` to keep the dependency surface minimal.

**What v1 does NOT do (deferred):**
- Lighter-weight sandboxes like macOS `sandbox-exec`, Linux `bubblewrap` / `firejail` / `nsjail` — useful but increase platform-specific code; v1.1+ if there's demand.
- Remote sandbox execution (running the container on a beefier remote host while the orchestrator stays local) — interesting for cloud agents, defer.
- gVisor / Kata for higher-isolation Docker — defer; default Docker is already a meaningful step up from Tier 1.

## 7. Data flow: one feature end-to-end

```
1. user invokes:  bob run --inputs ./brief.pdf ./screenshots/
2. Duplo:
     - reads .bob/inputs/ (multimodal)
     - emits .bob/spec.md
     - creates .bob/features/001-auth/, .bob/features/002-dashboard/, ...
       each with spec.md + state.json + empty activity/failed/debate/verifier files
     - meta-rubric check on each feature (halt loud if inadequate)
     - HITL gate: user approves
3. Coordinator:
     - records run start in .bob/cursor.json + .bob/run-log.jsonl
     - starts vroom thread (background)
     - walks .bob/features/, picks 001-auth:
4. McLoop:
     - creates worktree at .bob/worktrees/001-auth/
     - sandbox tier wraps subprocess
     - spawns claude -p (iter 1) → edits, verifier returns Ok
       → commit; appends to .bob/features/001-auth/{activity.md, verifier-results.jsonl}
     - spawns claude -p (iter 2) → verifier returns Fail
       → appends to .bob/features/001-auth/failed_attempts.md
     - ...
     - spawns claude -p (iter N) → emits EXIT_SIGNAL
     - updates .bob/features/001-auth/state.json: status=mcloop_done
5. Orchestra:
     - AutoGen GroupChat: Claude defends, Codex attacks, Judge synthesizes
     - KS detects stability at round 3
     - writes .bob/features/001-auth/debate.json
     - Verdict: approve
6. Coordinator: merges 001-auth worktree to main; state.json: status=merged
7. Vroom (running in parallel):
     - audits new commit
     - findings clustered, severity assigned, appended to .bob/findings.jsonl
     - HITL triage: user picks 2 findings to fix
     - spawns fix-McLoops on vroom/<finding-id> branches (in their own worktrees)
     - verifier passes, diffs small → auto-merge
     - findings status updated in .bob/findings.jsonl (append-only: new entry marks them resolved)
8. Coordinator advances to 002-dashboard.
```

## 8. Failure modes (all Reddit-validated)

| Failure mode | Mitigation |
|---|---|
| Mega-PR overnight (Careless_Bat critique) | Per-feature worktree+branch+PR contract. Small PRs by construction. |
| Compounding mistakes | Orchestra runs *between* features. Per-iteration verifier halt. Failed-attempts log feeds next iteration. |
| Expert out of the loop | Three default-on HITL gates (post-Duplo, Orchestra-disagreement, Vroom-triage). |
| Context rot (plugin failure mode) | McLoop spawns fresh `claude -p` per iteration. Plugin only used in Duplo's single-document refinement. |
| **Silent rubric failures** | **Verifier protocol with `Inconclusive` halt; meta-rubric check pre-McLoop; refusal to start without matching verifier.** |
| Tool-use babysitting | Hooks (PreToolUse pattern-match) + sandbox tiers. No human approval per tool call in autonomous mode. |
| Cost runaway | `--max-cost`, `--max-iterations`, per-phase model selection (Haiku for Vroom first-pass, Sonnet for McLoop, Opus for judge). |
| Stuck loop / "digging the hole" | `failed_attempts.md` per worktree; coordinator detects stuck state (no commits in N iters) and escalates to user. |
| Container escape / system damage | Sandbox tier 2/3 for high-autonomy runs; hooks as defense-in-depth. |
| Auditor hallucinations (Vroom false positives) | Findings shown in triage only when ≥2 auditors agree (default threshold; configurable via `vroom.consensus.min_agreeing_auditors`). Single-auditor findings logged to `findings.jsonl` with `status="pending"` but suppressed from active triage queue. |

## 9. Cost & budget controls

- Per-phase default models:
  - Duplo: Opus 4.6 (slow, smart, fewer total tokens).
  - McLoop iteration body: Sonnet 4.6.
  - Orchestra: ClaudeAgent Sonnet, CodexAgent GPT-5.2, JudgeAgent Opus.
  - Vroom auditors: Haiku 4.5 first pass; escalate flagged to Sonnet/Opus.
- `bob run --max-cost $X --max-iterations N` enforced by coordinator.
- Cost tracking: every API call's `response.usage` is persisted to `cursor.json`.
- On budget breach: coordinator pauses, persists state, requests HITL decision.

**Subscription-aware tracking.** Bob assumes most users run a Claude Max plan + ChatGPT/OpenAI Pro plan rather than pay-as-you-go API. Cost-tracking has two modes per provider:
- `subscription` (default): counts requests-per-period, watches for plan-imposed rate limits, backs off and resumes automatically when limits reset. Displays "approx hours remaining" estimates from observed token rate.
- `api`: tallies actual USD using `response.usage` and the published price table; halts on `--max-cost` breach.

Mode is set per-provider via env vars: `BOB_ANTHROPIC_BILLING={subscription,api}` and `BOB_OPENAI_BILLING={subscription,api}`. The `--max-cost` flag is a hard stop in `api` mode and an advisory ceiling in `subscription` mode (where the plan limit is the actual stop).

## 10. Observability

OpenTelemetry instrumentation on:
- Phase entry/exit (span per phase).
- Each model call (provider, model, tokens, cost, duration, prompt hash).
- Verifier results and coverage notes.
- Hook decisions (approved / blocked, with reason).
- HITL events (gate name, decision, latency).

Configurable exporter via `OTEL_EXPORTER_OTLP_ENDPOINT`. Default backend recommendation: **Phoenix (Arize, OSS, runs locally)**. Alternatives: Langfuse, LangSmith, Honeycomb, Datadog.

**Why not LangGraph + LangSmith:** LangGraph would force LangChain ecosystem coupling for an essentially linear graph (Duplo → foreach feature: [McLoop → Orchestra] → Vroom is a foreach + a daemon, not a graph that benefits from conditional edges). Observability is decoupleable from orchestration. We get the value (traces, spans, replay) without the dependency surface, abstraction churn, and version-break risk of LangChain. If Vroom's auditor pool ever grows complex routing logic, we revisit then — adopting LangGraph there is much easier than escaping it from the spine.

## 11. Testing strategy

- **Unit tests** for: coordinator state machine, phase contracts, SARIF coalescer, KS-statistic, each verifier adapter.
- **Integration tests** with mocked Claude/Codex CLIs (subprocess fakes that emit deterministic outputs) covering each phase.
- **End-to-end smoke test:** `bob run` against a tiny demo repo (a Python CLI with a tested function) verifying all four phases complete on real APIs (gated behind an env flag for cost reasons).
- **Verifier-coverage test:** synthetic features with known-bad rubrics — confirm the meta-rubric check rejects them.
- **Failure-injection tests:** deliberately corrupt a feature's `state.json`, delete a feature directory mid-run, kill subprocesses mid-iteration, exhaust budget — confirm graceful state persistence and resumability.

## 12. Project layout

```
claude_orchestrator/                 # existing package, extended
  __init__.py
  cli.py                              # extends with new bob commands
  config.py
  models.py                           # extends with new contracts (§5)
  state.py                            # extends with cursor.json
  human_input.py                      # extends with HITL gate registry
  hooks.py                            # promoted to bob/hooks/

  bob/
    coordinator.py                    # ~100 LOC state machine
    duplo/
      __init__.py
      multimodal.py                   # Anthropic vision call
      schema.py                       # Pydantic Spec
      meta_rubric.py                  # LLM-as-judge coverage check
      prompts/
    mcloop/
      __init__.py
      runner.py                       # bash-loop pattern in Python
      worktree.py                     # git worktree wrapper
      sandbox.py                      # tier dispatcher
      prompts/
    orchestra/
      __init__.py
      debate.py                       # AutoGen GroupChat wrapper
      stability.py                    # KS-statistic detector
      prompts/
    vroom/
      __init__.py
      auditor_pool.py                 # parallel subprocess runner
      coalescer.py                    # SARIF dedupe + cluster
      triage.py                       # HITL gate
      auditors/
        pr_agent.py
        semgrep.py
        claude_architect.py
        codex_security.py
    verifiers/
      __init__.py
      protocol.py                     # Verifier ABC
      python_pytest.py
      js_jest.py
      lint_universal.py
      playwright_ui.py
      data_analysis.py
      geospatial.py
      ml_training.py
      cli_smoke.py
    hooks/                            # promoted from hooks.py
      __init__.py
      pretooluse.py
      bash_security.py
    hitl/
      __init__.py
      gates.py
    observability/
      __init__.py
      otel.py

docs/superpowers/specs/
  2026-05-06-bob-design.md            # this file
```

## 13. Open questions / deferred

- **Multi-runtime plugin packaging:** ship Bob as a Claude Code plugin AND a Codex plugin AND a Gemini extension from a single canonical source (maestro-orchestrate's `src/`-then-generate pattern). Powerful but unlikely to justify the engineering for v1; out of scope unless a clear demand emerges.
- **Express workflow** for trivial features: skip Orchestra and Vroom on small additions, run only Duplo→McLoop→one-verifier→merge. Inspired by maestro-orchestrate's Express path. Reduces ceremony for one-line fixes; adds a routing decision (who classifies a task as Express?). Defer to v1.1.
- **Vroom triage UI:** terminal TUI vs simple local web page for finding triage? TUI for v1; web UI deferred.
- **Multi-repo Vroom:** auditing repo A and opening PRs across repos. Out of scope for v1.
- **Distribution:** PyPI for v1; Homebrew formula and Docker image deferred.
- **Resumability granularity:** mid-iteration resumability vs per-iteration boundary. Per-iteration for v1 (simpler).
- **Codex CLI vs OpenAI API direct:** CLI for v1 (matches user's existing setup); API mode as opt-in for batch / non-interactive deployments.

## 14. Component dependency summary

**External dependencies added:**
- `microsoft/autogen` (Orchestra)
- `qodo-ai/pr-agent` (Vroom auditor — invoked as subprocess, not imported)
- `semgrep` (Vroom auditor — subprocess)
- `playwright` (UI verifier — only when needed)
- `pandera` or `great-expectations` (data-analysis verifier — only when needed)
- `shapely`, `pyproj` (geospatial verifier — only when needed)
- `hypothesis` (data-analysis verifier — only when needed)
- `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp` (observability)
- `arize-phoenix` (recommended OTel backend for local development; opt-in)

**Existing dependencies kept:** `claude-agent-sdk`, `pydantic`, `tomli`.

**Deliberately not added:** `langchain`, `langgraph`, `crewai`, `openhands` (see §10 and reuse map for rationale).

---

## Appendix A: Related work — Maestro and why Bob is not a Maestro fork

[maestro-orchestrate](https://github.com/josstei/maestro-orchestrate) is the closest existing OSS project to Bob's spec-first phased workflow. It is a multi-runtime plugin/extension (Gemini CLI, Claude Code, Codex, Qwen Code) with 39 specialist agents, a two-tier workflow (Express / Standard), and standalone audit commands.

**Where Maestro and Bob converge** (~60% overlap by surface area): spec-first design, phased execution with approval gates, code review on outputs, persisted session state, opt-in audit specialists.

**Where Bob differs (the load-bearing 40%):**
1. **Cross-runtime adversarial debate (Orchestra).** Maestro is multi-runtime in *distribution* — same code installs into one host runtime per session. Bob's Orchestra is multi-runtime in *coordination* — Claude and Codex run as separate processes that argue with each other within one orchestration step. This shape doesn't fit a single-host plugin.
2. **Continuous proactive audit (Vroom).** Maestro's audits are user-invoked and reactive. Vroom is autonomous — long-running, propose-and-merge, runs whether or not a CLI session is open. Cannot be a plugin because it outlives any session.
3. **Verifier rubric protocol with halt-loud `Inconclusive`.** Maestro requires "validation results for the changed surface" and "no Critical/Major findings" — generic. Bob has an explicit per-task-type verifier protocol with a meta-rubric coverage check before McLoop runs, and `Inconclusive` results halt the loop. This is a deeper safety commitment.
4. **Sandbox tiers** (hooks → Docker → Devcontainer) with a PreToolUse policy engine. Maestro defers entirely to host runtime permissions.
5. **Implementation language and shape.** Maestro is JavaScript/Node, distributed as plugins. Bob is Python (extending `claude-orchestrator`), distributed as a standalone CLI/library.

**What Bob borrows from Maestro:** the auditor specialist taxonomy (a11y, perf, compliance, SEO) inspired Vroom's expanded default roster; the Express/Standard two-tier workflow is a strong v1.1 idea (§13); the canonical-`src/`-with-codegen distribution pattern is the right model if Bob ever ships as multi-runtime plugins (§13).

**Could Bob have been built on top of Maestro?** Maestro could replace ~60% of Bob (Duplo + McLoop + a sequential Orchestra-as-review). It could not host Vroom (plugin sessions don't outlive the host CLI), Orchestra-as-cross-runtime-debate (single-host architecture), the verifier protocol (would require replacing Maestro's execute/complete phases), or our existing `hooks.py` security work (Node/Python language mismatch). Building on top would have meant rewriting in Node and inheriting design opinions that don't match the talk's specific vision.

---

## Appendix B: How this design responds to the talk's manifesto

| Talk claim | This design's response |
|---|---|
| *"Duplo is Bob creating the spec. The quality of the output is a direct function of the quality of the plan."* | Duplo refuses to ship without a verifier-coverage meta-check; spec quality is enforced by the coverage gate. |
| *"McLoop is Bob running, testing, and debugging the build while you sleep. Fresh context per task, tests and lint after every change, only clean code committed."* | Bash-loop pattern (fresh `claude -p`) per iteration; verifier adapter runs every iteration; `Inconclusive` halts loud. |
| *"Orchestra gives different models different jobs, then makes them argue."* | AutoGen GroupChat with Claude/Codex/Judge; KS-stability termination; default-on HITL on disagreement. |
| *"Vroom runs parallel auditors on your system, coalesces their findings, and proposes a corrected or expanded version."* | Parallel auditor pool emitting SARIF; coalescer dedupes/clusters; fix-McLoop with verification gate; auto-merge survivors. |
| *"Bob doesn't like LLM slop."* | Multi-auditor consensus threshold before triage; KS-stability for debate; halt-loud verifier protocol. |
