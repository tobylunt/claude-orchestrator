# Bob

Bob is a Python CLI that orchestrates multi-phase AI-driven feature development. Given a markdown spec, Bob takes a feature from natural-language description through implementation, adversarial code review, and continuous security auditing — all with a single command. It is designed around the principle that LLM coding agents need tightly bounded loops with loud failure modes, not optimistic pipelines that silently accumulate debt.

---

## Quick start

**Prerequisites:** Python 3.10+, the `claude` CLI in your PATH, and (for Orchestra / Vroom) API keys for Anthropic and OpenAI.

```bash
# Install into your active Python environment
pip install -e ".[m2]"

# Put your API keys in the user-level .env (loaded automatically by Bob)
mkdir -p ~/.bob
cat > ~/.bob/.env <<'EOF'
ANTHROPIC_API_KEY=sk-ant-…
OPENAI_API_KEY=sk-…
EOF

# Write a spec (see docs/superpowers/specs/2026-05-06-bob-design.md for format)
cat > my_spec.md <<'EOF'
# My Feature

## Motivation
Add a small verified behavior.

## Features

### F1: Hello world
- task_type: library
- verifier: python_pytest
- success_criteria:
  - pytest passes for the new behavior
- description: |
    Add a hello-world behavior with focused tests.
EOF

# Validate the spec before running
bob validate --inputs my_spec.md

# Or ask Duplo to draft a spec from a directory of rough inputs, then review it
bob draft --inputs ./inputs --output draft_spec.md

# Run (from your project root)
bob run --inputs my_spec.md

# Follow progress in another terminal
bob status

# View costs when done
bob costs --by phase
```

To run fully unattended overnight, use **YOLO mode** (requires a Docker or
devcontainer sandbox and an explicit advisory budget):

```bash
bob run --inputs spec.md --yolo --sandbox docker --max-cost 20
```

---

## CLI reference

| Subcommand | Description |
|---|---|
| `bob run` | Run the full orchestration pipeline (Duplo → McLoop → Orchestra → optional Vroom). |
| `bob status` | Print current phase, active feature, and per-feature statuses. |
| `bob validate` | Parse and validate a spec file without acquiring the run lock. |
| `bob draft` | Run Duplo only and emit a parser-readable draft spec for human review. |
| `bob costs` | Aggregate cost data from `.bob/costs.jsonl`. |
| `bob runs` | Show recent runs with duration, outcome, and cost from `.bob/run-log.jsonl`. |
| `bob vroom` | Start the Vroom audit daemon in the foreground (blocking). |
| `bob vroom now` | Run one Vroom audit cycle synchronously and exit. |
| `bob vroom stop` | Send SIGTERM to the running Vroom daemon. |

### `bob run` flags

| Flag | Default | Description |
|---|---|---|
| `--inputs PATH` | required | Path to a markdown spec file or directory of multimodal inputs. |
| `--project DIR` | `.` (cwd) | Project root — where `.bob/` is created. |
| `--max-iterations N` | `30` | McLoop iteration cap per feature. |
| `--max-cost USD` | none | Advisory USD spend bound. Required by `--yolo`; hard enforcement is planned budget-guard work. |
| `--sandbox {host,docker,devcontainer}` | `host` | Sandbox tier (or `BOB_SANDBOX_TIER`). |
| `--vroom` | off | Spawn the Vroom daemon in parallel with the feature loop. |
| `--yolo` | off | Enable YOLO mode (unattended; requires `--sandbox docker` or `--sandbox devcontainer`, plus `--max-cost`). |
| `--no-gate NAME` | none | Disable a named HITL gate (repeatable). |
| `--otel-endpoint URL` | `$OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP traces endpoint. |

### `bob costs` flags

`--by {run|provider|phase|model}` — grouping dimension (default: `run`).

### `bob draft` flags

`--inputs PATH` — markdown spec file or directory of rough Duplo inputs.
`--output PATH` — optional output file; omitted means print the draft spec to stdout.

Use `bob draft` when the contract itself needs review before implementation.
It does not acquire the run lock, materialize feature worktrees, or enter
McLoop. Real multimodal Duplo calls can still record `duplo` cost rows in
`.bob/costs.jsonl`.

### `bob runs` flags

`--limit N` — number of recent runs to display (default: 10; 0 = all).

### `bob vroom` flags

`--interval N` — seconds between timer-driven audit cycles (default: 1800).
`--watch-main-ref` — also trigger a cycle when `.git/refs/heads/main` changes.

---

## The four phases

### Duplo

**Spec ingestion.** Duplo accepts a markdown spec file or a directory of multimodal inputs (screenshots, diagrams, documents) and produces the canonical `spec.md` and a structured feature list. With `BOB_USE_STUB_DUPLO=1` it skips the vision API and uses the markdown path only, which is the default for text specs.

Related: `bob run --inputs`, `bob validate`.

### McLoop

**Implementation loop.** For each feature, McLoop spawns a fresh `claude -p` subprocess per iteration (Geoffrey Huntley's "Ralph Wiggum" bash-loop pattern) and runs the configured verifier after each attempt. An `Inconclusive` verifier result halts loudly by default; in YOLO mode it is re-fed into the loop up to `BOB_YOLO_MAX_INCONCLUSIVE` times (default 3). Each iteration's streaming output lands in `.bob/features/<NNN>/iter-<N>.log`.

### Orchestra

**Adversarial code review.** After McLoop produces a passing diff, Orchestra runs a 3-agent debate: Claude defends the implementation, GPT-5.4 Codex attacks it, and a fast Claude judge synthesizes. Debate terminates when the judge's verdict reaches KS-stability (consecutive rounds agree). A premium pass (GPT-5.5 deep review plus Claude Opus final judge) runs only when the review policy flags risk, low confidence, disagreement, or large diffs. The full debate transcript is saved to `.bob/features/<NNN>/debate.json`. Set `BOB_USE_STUB_ORCHESTRA=1` to skip the debate and auto-approve.

### Vroom

**Continuous audit daemon.** Vroom runs a parallel auditor pool — Claude architect, Codex security, and Semgrep — over the project on each cycle. Findings are SARIF-coalesced, clustered by rule, and routed through a HITL triage gate. When a finding is approved for fix, Vroom spawns an isolated McLoop on a `vroom/<id>` branch. In YOLO mode, findings at or above `BOB_YOLO_VROOM_SEVERITY` (default `high`) are auto-approved. Vroom can run as a daemon alongside `bob run` (`--vroom` flag) or independently.

Related: `bob vroom`, `bob vroom now`, `bob vroom stop`.

---

## Configuration

### .env loading

Bob auto-loads `.env` files at startup. Precedence (highest first):

1. Process environment (always wins)
2. `<project_root>/.env`
3. `<cwd>/.env`
4. `~/.bob/.env` — recommended for API keys shared across projects

Copy `.env.example` to `~/.bob/.env` and fill in your keys.

### Model selection

Override any default model via environment variable. The defaults are listed in `.env.example`:

| Variable | Default |
|---|---|
| `BOB_DUPLO_MODEL` | `claude-opus-4-7` |
| `BOB_MCLOOP_MODEL` | `claude-sonnet-4-6` |
| `BOB_ORCHESTRA_CLAUDE_MODEL` | `claude-sonnet-4-6` |
| `BOB_ORCHESTRA_CODEX_MODEL` | `gpt-5.4` |
| `BOB_ORCHESTRA_CODEX_EFFORT` | `medium` |
| `BOB_ORCHESTRA_FAST_JUDGE_MODEL` | `claude-sonnet-4-6` |
| `BOB_ORCHESTRA_PREMIUM_POLICY` | `adaptive` |
| `BOB_ORCHESTRA_PREMIUM_MIN_CONFIDENCE` | `0.85` |
| `BOB_ORCHESTRA_PREMIUM_DIFF_BYTES` | `12000` |
| `BOB_ORCHESTRA_PREMIUM_FILE_COUNT` | `8` |
| `BOB_ORCHESTRA_PREMIUM_RISK_FRAGMENTS` | `auth,oauth,login,security,secret,token,payment,billing,sandbox,docker` |
| `BOB_ORCHESTRA_PREMIUM_CODEX_MODEL` | `gpt-5.5` |
| `BOB_ORCHESTRA_PREMIUM_CODEX_EFFORT` | `xhigh` |
| `BOB_ORCHESTRA_JUDGE_MODEL` | `claude-opus-4-7` |
| `BOB_ORCHESTRA_MAX_ROUNDS` | `5` |
| `BOB_VROOM_CLAUDE_MODEL` | `claude-sonnet-4-6` |
| `BOB_VROOM_CODEX_MODEL` | `gpt-5.4` |
| `BOB_VROOM_CODEX_EFFORT` | `low` |

OpenAI effort values are `none`, `low`, `medium`, `high`, `xhigh`, or
`default` (omit the API parameter and let the model choose). Use `gpt-5.5` +
`xhigh` for deliberate deep-review runs rather than as the continuous default.
Premium policy values are `adaptive`, `always`, or `never`.

### Stub modes

Skip API calls for offline testing or CI:

```bash
BOB_USE_STUB_ORCHESTRA=1   # auto-approve all verdicts; no debate
BOB_USE_STUB_VROOM=1       # auditors return empty findings
BOB_USE_STUB_DUPLO=1       # markdown-only spec ingestion (no vision)
```

### Sandbox tiers

**Tier 1 — `host` (default).** Runs `claude -p` in a git worktree under the hooks policy (`claude_orchestrator/bob/hooks/`). No container isolation.

**Tier 2 — `docker`.** Spawns an ephemeral container per McLoop call. Bob auto-detects a `bob.dockerfile` in your project root; if absent it falls back to `BOB_DOCKER_IMAGE`. Relevant env vars:

```bash
BOB_SANDBOX_TIER=docker
BOB_DOCKER_IMAGE=python:3.10-slim   # fallback image
BOB_DOCKER_CPUS=4
BOB_DOCKER_MEMORY=8g
BOB_DOCKER_NETWORK=bob-allowlist    # custom network for egress filtering
BOB_DOCKER_EXTRA_ARGS='-v $HOME/.claude:/tmp/.claude:ro'
BOB_DOCKER_FORWARD_ENV=EXTRA_TOKEN,OTHER_SETTING
```

Docker automatically forwards Bob, Anthropic, OpenAI, and OTEL-related
environment variables into the container. Use `BOB_DOCKER_FORWARD_ENV` for any
additional comma-separated host env vars and `BOB_DOCKER_EXTRA_ARGS` for raw
Docker flags such as extra read-only mounts.

Copy `bob.dockerfile.example` to `bob.dockerfile` in your project root and customise for your language stack. The file is intentionally not committed; add it to `.gitignore`.

**Tier 3 — `devcontainer`.** Uses VS Code's `devcontainer.json` for container spec. Same isolation as Tier 2, with declarative reproducibility. Requires the `devcontainer` CLI.

### YOLO mode

`--yolo` is the single-flag opt-in for unattended overnight runs. It requires
`--sandbox docker` (or `devcontainer`) and `--max-cost`. The configured
`--max-cost` is currently an advisory spend bound and an explicit autonomy
signal; central hard budget enforcement is tracked as future budget-guard work.
Effects:

- Auto-approves the post-Duplo HITL gate if the meta-rubric passes.
- Re-feeds `Inconclusive` McLoop results back into the loop (bounded by `BOB_YOLO_MAX_INCONCLUSIVE`, default 3).
- Auto-approves Vroom triage for findings at or above `BOB_YOLO_VROOM_SEVERITY` (default `high`).

```bash
# Configure a $20 advisory spend bound and run unattended in Docker
bob run --inputs spec.md --yolo --sandbox docker --max-cost 20 --vroom
```

---

## Observability

### Cost tracking

API calls and Claude CLI-reported costs are appended to `.bob/costs.jsonl`.
View aggregated breakdowns:

```bash
bob costs --by phase      # where did the money go?
bob costs --by model      # which models are most expensive?
bob costs --by provider   # anthropic vs openai split
```

### Run history

```bash
bob runs              # last 10 runs with status, duration, cost
bob runs --limit 0    # all runs
```

### Distributed traces (OpenTelemetry)

Bob emits OTLP spans across Coordinator phases, McLoop iterations, Orchestra debate rounds, and Vroom cycles. To view them locally:

```bash
pip install arize-phoenix
phoenix serve          # starts at http://localhost:6006

# Point Bob at it
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006/v1/traces
bob run --inputs spec.md
```

Or pass `--otel-endpoint` directly to `bob run`.

---

## State layout

Bob creates a `.bob/` directory in your project root:

```
<project>/.bob/
├── spec.md                         # canonical spec produced by Duplo
├── cursor.json                     # current run + active phase
├── run-log.jsonl                   # append-only event log (all phases)
├── findings.jsonl                  # Vroom audit findings
├── costs.jsonl                     # per-call cost ledger
├── .bob.lock                       # PID file (single-instance lock)
├── vroom.pid                       # Vroom daemon PID (when running)
├── inputs/                         # captured copy of Duplo inputs
└── features/<NNN>-<slug>/          # per-feature working state
    ├── spec.md                     # feature-scoped spec
    ├── state.json                  # current status, iteration count
    ├── activity.md                 # running narrative log
    ├── failed_attempts.md          # accumulated failure context for McLoop
    ├── verifier-results.jsonl      # per-iteration verifier output
    ├── debate.json                 # Orchestra debate transcript
    └── iter-<N>.log                # per-iteration claude streaming output
```

Bob enforces single-instance execution via `.bob.lock` (PID file). If a lock is held by a dead process, `bob run` reports a stale-PID error and exits cleanly.

---

## Verifiers

Bob ships four built-in verifiers, selected per-feature in the spec:

| Verifier ID | What it runs |
|---|---|
| `python_pytest` | Project's pytest suite; halts loud on exit code 5 (no tests collected). |
| `lint_universal` | Auto-detects ruff / eslint / gofmt / clippy and runs the appropriate linter. |
| `data_analysis` | pytest + Hypothesis + Papermill notebook regression. |
| `geospatial` | Shapely topology checks + `.geojson` / `.shp` / `.gpkg` validation. |

---

## Status and roadmap

### Shipped milestones

| Milestone | Description |
|---|---|
| **M1** | Initial orchestrator skeleton, markdown spec parser, basic McLoop, host sandbox. |
| **M2** | Verifiers (`python_pytest`, `lint_universal`, `data_analysis`, `geospatial`), Orchestra stub. |
| **M3** | Docker sandbox (Tier 2), cost tracker, `bob costs` and `bob runs` subcommands. |
| **M4** | HITL gates (`PostDuploGate`), meta-rubric, spec validation pre-flight. |
| **M5** | Real Orchestra (3-agent debate, KS-stability termination). |
| **M6** | YOLO mode with configurable thresholds and sandbox invariants. |
| **M7** | Vroom audit daemon, SARIF coalescer, auditor pool (Claude + Codex + Semgrep). |
| **M8** | Vroom fix-loop driver (isolated McLoop per finding), devcontainer sandbox (Tier 3), OpenTelemetry instrumentation. |
| **M9** | Real-mode hardening across Docker, Vroom, cost context, and subprocess boundaries. |
| **M10** | Deferred P2 fixes, Docker dogfood fixes, and Claude CLI cost ledger capture. |
| **M11** | Typed run/Vroom wiring extraction, OpenAI effort configuration, premium review policy, locked `uv` dev environment, and boundary-contract tests. |

### Known gaps / deferred

- `BOB_YOLO_NOTIFY` (email/Slack/desktop notification on YOLO completion) — not yet implemented.
- Hard budget enforcement for `--max-cost` — currently advisory; planned as the next budget-guard layer.
- `claude -p` model selection is not yet wired through to the actual Claude CLI invocation; `BOB_MCLOOP_MODEL` should become an execution setting, not only a cost-label convention.
- Orchestra/debate-agent JSON parsing should accept fenced JSON replies instead of treating otherwise valid responses as abstentions.
- Docker sandbox does not auto-mount `$HOME/.claude` for claude auth inside the container; see the note in `bob.dockerfile.example`.
- `bob run --inputs <directory>` (multimodal Duplo from a folder of images/docs) is wired but the vision extraction path depends on `BOB_USE_STUB_DUPLO=0` and a configured `BOB_DUPLO_MODEL`.
- Tmux/PTY interactive-session execution is out of scope for the current roadmap; useful learnings from those workflows are folded into hook policy, project memory, and observability instead.
- Terminal observability (`bob sessions` / dashboard view over runs, workers, costs, hooks, verifier results, and transcripts) is a secondary roadmap objective; durable `.bob/` state remains the source of truth.

### Agent control surfaces

- `MAINTAIN.md` records Bob's work principles and invariants for future agents.
- `BUGS.md` records unchecked bugs, deferred contract gaps, and `[RULEDOUT]`
  dead ends that should not be retried.

---

## Spec and design docs

- **Primary design spec:** `docs/superpowers/specs/2026-05-06-bob-design.md` — covers the full architecture, phase contracts, verifier protocol, HITL gate design, YOLO invariants, and the Orchestra stability criterion.
- **Architecture roadmap:** `docs/superpowers/plans/2026-05-11-bob-architecture-roadmap.md` — records the audit-driven implementation plan, including budget guard, execution backend abstraction, hook policy memory, and terminal observability.
- **Related work:** The Maestro appendix in the same doc surveys comparable orchestration systems.

---

## Development

```bash
# Create a locked local dev environment
uv sync --locked --extra dev --extra m2

# Run the full test suite
uv run --locked --extra dev --extra m2 pytest -q

# Run only Bob's own tests
uv run --locked --extra dev --extra m2 pytest tests/bob/ -q

# Run without API keys (stub mode)
BOB_USE_STUB_ORCHESTRA=1 BOB_USE_STUB_VROOM=1 BOB_USE_STUB_DUPLO=1 uv run --locked --extra dev --extra m2 pytest tests/bob/ -q
```

The committed `uv.lock` is the reproducible local/CI environment. Keep
`pyproject.toml` dependency ranges broad for package consumers, and refresh the
lockfile with `uv lock` when changing dependencies.

### Contributing pattern

1. Write a failing test in `tests/bob/` that specifies the behaviour.
2. Implement in `claude_orchestrator/bob/`.
3. Run `pytest -q` — all existing tests must pass.
4. Open a PR; Orchestra will review it.

The project uses `hatchling` as its build backend (`pyproject.toml`). No `setup.py`.
