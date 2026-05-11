# Bob

Bob is a Python CLI that orchestrates multi-phase AI-driven feature development. Given a markdown spec, Bob takes a feature from natural-language description through implementation, adversarial code review, and continuous security auditing — all with a single command. It is designed around the principle that LLM coding agents need tightly bounded loops with loud failure modes, not optimistic pipelines that silently accumulate debt.

---

## Quick start

**Prerequisites:** Python 3.10+, the `claude` CLI in your PATH, and (for Orchestra / Vroom) API keys for Anthropic and OpenAI.

```bash
# Install
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
…
## Features
### [F01] Hello world
…
EOF

# Validate the spec before running
bob validate --inputs my_spec.md

# Run (from your project root)
bob run --inputs my_spec.md

# Follow progress in another terminal
bob status

# View costs when done
bob costs --by phase
```

To run fully unattended overnight, use **YOLO mode** (requires Docker sandbox and a cost cap):

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
| `--max-cost USD` | none | Advisory USD spend cap. Required by `--yolo`. |
| `--sandbox {host,docker,devcontainer}` | `host` | Sandbox tier (or `BOB_SANDBOX_TIER`). |
| `--vroom` | off | Spawn the Vroom daemon in parallel with the feature loop. |
| `--yolo` | off | Enable YOLO mode (unattended; requires `--sandbox docker` and `--max-cost`). |
| `--no-gate NAME` | none | Disable a named HITL gate (repeatable). |
| `--otel-endpoint URL` | `$OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP traces endpoint. |

### `bob costs` flags

`--by {run|provider|phase|model}` — grouping dimension (default: `run`).

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

**Adversarial code review.** After McLoop produces a passing diff, Orchestra runs a 3-agent debate: Claude defends the implementation, GPT-5.4 Codex attacks it, and Claude Opus acts as judge. Debate terminates when the judge's verdict reaches KS-stability (consecutive rounds agree). The full debate transcript is saved to `.bob/features/<NNN>/debate.json`. Set `BOB_USE_STUB_ORCHESTRA=1` to skip the debate and auto-approve.

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
| `BOB_ORCHESTRA_JUDGE_MODEL` | `claude-opus-4-7` |
| `BOB_VROOM_CLAUDE_MODEL` | `claude-sonnet-4-6` |
| `BOB_VROOM_CODEX_MODEL` | `gpt-5.4` |

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
```

Copy `bob.dockerfile.example` to `bob.dockerfile` in your project root and customise for your language stack. The file is intentionally not committed; add it to `.gitignore`.

**Tier 3 — `devcontainer`.** Uses VS Code's `devcontainer.json` for container spec. Same isolation as Tier 2, with declarative reproducibility. Requires the `devcontainer` CLI.

### YOLO mode

`--yolo` is the single-flag opt-in for unattended overnight runs. It requires `--sandbox docker` (or `devcontainer`) and `--max-cost`. Effects:

- Auto-approves the post-Duplo HITL gate if the meta-rubric passes.
- Re-feeds `Inconclusive` McLoop results back into the loop (bounded by `BOB_YOLO_MAX_INCONCLUSIVE`, default 3).
- Auto-approves Vroom triage for findings at or above `BOB_YOLO_VROOM_SEVERITY` (default `high`).

```bash
# Cap spend at $20, run unattended in Docker
bob run --inputs spec.md --yolo --sandbox docker --max-cost 20 --vroom
```

---

## Observability

### Cost tracking

Every API call is appended to `.bob/costs.jsonl`. View aggregated breakdowns:

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

### Known gaps / deferred

- `BOB_YOLO_NOTIFY` (email/Slack/desktop notification on YOLO completion) — not yet implemented.
- Docker sandbox does not auto-mount `$HOME/.claude` for claude auth inside the container; see the note in `bob.dockerfile.example`.
- `bob run --inputs <directory>` (multimodal Duplo from a folder of images/docs) is wired but the vision extraction path depends on `BOB_USE_STUB_DUPLO=0` and a configured `BOB_DUPLO_MODEL`.

---

## Spec and design docs

- **Primary design spec:** `docs/superpowers/specs/2026-05-06-bob-design.md` — covers the full architecture, phase contracts, verifier protocol, HITL gate design, YOLO invariants, and the Orchestra stability criterion.
- **Related work:** The Maestro appendix in the same doc surveys comparable orchestration systems.

---

## Development

```bash
# Install with dev and M2 extras
pip install -e ".[m2,dev]"

# Run the full test suite
pytest -q

# Run only Bob's own tests
pytest tests/bob/ -q

# Run without API keys (stub mode)
BOB_USE_STUB_ORCHESTRA=1 BOB_USE_STUB_VROOM=1 BOB_USE_STUB_DUPLO=1 pytest tests/bob/ -q
```

### Contributing pattern

1. Write a failing test in `tests/bob/` that specifies the behaviour.
2. Implement in `claude_orchestrator/bob/`.
3. Run `pytest -q` — all existing tests must pass.
4. Open a PR; Orchestra will review it.

The project uses `hatchling` as its build backend (`pyproject.toml`). No `setup.py`.
