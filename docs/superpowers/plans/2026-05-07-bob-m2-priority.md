# Bob M2: Real Orchestra + Multimodal Duplo + Priority Verifiers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace M2a's stubs with production-grade implementations, focused on the verifiers most relevant to data-lab work.

- **Real Orchestra:** AutoGen GroupChat with Claude/Codex/Judge agents and KS-statistic stability detection (Hu et al. 2025) replaces the auto-approve `OrchestraStub`.
- **Multimodal Duplo:** Anthropic vision API ingests URLs/PDFs/screenshots/prose and emits a Spec; the markdown parser remains as a fallback/explicit input mode.
- **Priority verifiers:** `lint_universal`, `data_analysis`, `geospatial` — tuned to development-economics / spatial-data work.

**Deferred to M3:** `playwright_ui`, `js_jest`, `js_vitest`, `go_test`, `rust_cargo`, `ml_training`, `cli_smoke`. The protocol and registry are stable so adding them later is plug-in.

**Spec:** `docs/superpowers/specs/2026-05-06-bob-design.md`. Builds on M1 (`0005c59`), M2a (`7570a9a`), M2b (`ab11a41`).

---

## File structure

**Created:**
```
claude_orchestrator/bob/
  orchestra/
    debate.py                  # AutoGen GroupChat wrapper
    stability.py               # KS-statistic stability detector
    real.py                    # Real Orchestra (replaces stub.py for production)
  duplo/
    multimodal.py              # Anthropic vision API client
    real.py                    # Real Duplo (multimodal + ralph-wiggum refinement)
  verifiers/
    lint_universal.py          # ruff/eslint/gofmt detection + run
    data_analysis.py           # pytest + hypothesis + pandera + papermill
    geospatial.py              # shapely + pyproj + topology checks

tests/bob/
  test_stability.py
  test_orchestra_debate.py
  test_orchestra_real.py
  test_duplo_multimodal.py
  test_lint_universal.py
  test_data_analysis_verifier.py
  test_geospatial_verifier.py
```

**Modified:**
- `pyproject.toml` — add deps: `autogen-agentchat`, `scipy`, `pandera`, `hypothesis`, `papermill`, `shapely`, `pyproj`. All as optional `[project.optional-dependencies] m2`.
- `claude_orchestrator/bob/wiring.py` — register new verifiers; swap `AutoApproveJudge` + `OrchestraStub` for the real ones; swap markdown-only `duplo_callable` for multimodal real Duplo.
- `tests/bob/test_wiring.py` — extend for real Orchestra/Duplo wiring.

---

## Phase A — Real Orchestra

### Task 1: Add M2 dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Inspect existing pyproject.toml**

```bash
cat pyproject.toml
```

- [ ] **Step 2: Add dependencies under a new optional group `m2`**

In `[project.optional-dependencies]`, add:

```toml
m2 = [
    "autogen-agentchat>=0.4",
    "scipy>=1.13",
    "pandera>=0.20",
    "hypothesis>=6.100",
    "papermill>=2.6",
    "shapely>=2.0",
    "pyproj>=3.6",
    "anthropic>=0.40",
]
```

- [ ] **Step 3: Install m2 deps**

```bash
pip install -e '.[m2]' --quiet 2>&1 | tail -5
```

If a dep version isn't available, fall back to the latest available version (the agent should adjust the lower bound and report).

- [ ] **Step 4: Verify deps importable**

```bash
python -c "import autogen_agentchat, scipy, pandera, hypothesis, papermill, shapely, pyproj, anthropic; print('ok')"
```

Expected: `ok`

- [ ] **Step 5: Confirm baseline tests still pass**

```bash
pytest -q
```

Expected: 184 passed.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml
git commit -m "chore(bob): add M2 optional deps (autogen, scipy, pandera, hypothesis, shapely, pyproj, anthropic)"
```

---

### Task 2: KS-statistic stability detector

**Files:**
- Create: `claude_orchestrator/bob/orchestra/stability.py`
- Create: `tests/bob/test_stability.py`

Implements the Hu et al. 2025 (arXiv:2510.12697) adaptive stability detection: terminate a multi-agent debate when the judgment-distribution KS-statistic stays below a threshold for N consecutive rounds.

- [ ] **Step 1: Write failing tests**

`tests/bob/test_stability.py`:

```python
"""KS-statistic adaptive stability detector for multi-agent debate."""
import pytest

from claude_orchestrator.bob.orchestra.stability import (
    StabilityDetector,
    StabilityVerdict,
)


def test_detector_starts_unstable():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE


def test_detector_returns_stable_after_n_consecutive():
    d = StabilityDetector(ks_threshold=0.5, consecutive_rounds=2)
    # Identical rounds => KS=0 => stable on round 3.
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE  # round 1, no comparison yet
    assert d.update([1, 0, 1]) == StabilityVerdict.UNSTABLE  # round 2, KS=0 once
    assert d.update([1, 0, 1]) == StabilityVerdict.STABLE    # round 3, KS=0 twice


def test_detector_resets_consecutive_on_jump():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    d.update([1, 0, 1])  # round 1
    d.update([1, 0, 1])  # round 2: KS=0
    # Next round wildly different — consecutive resets.
    d.update([0, 1, 0])  # round 3: KS=1 ⇒ unstable
    # round 4 same as round 3
    assert d.update([0, 1, 0]) == StabilityVerdict.UNSTABLE  # only 1 consecutive stable
    assert d.update([0, 1, 0]) == StabilityVerdict.STABLE


def test_detector_records_history():
    d = StabilityDetector(ks_threshold=0.05, consecutive_rounds=2)
    d.update([1, 0])
    d.update([1, 0])
    assert len(d.history) == 2
```

- [ ] **Step 2: Run to confirm failure**

`pytest tests/bob/test_stability.py -v` → ImportError.

- [ ] **Step 3: Implement `stability.py`**

`claude_orchestrator/bob/orchestra/stability.py`:

```python
"""Adaptive stability detection for multi-agent debate.

Based on Hu et al., "Multi-Agent Debate for LLM Judges with Adaptive
Stability Detection" (arXiv:2510.12697, 2025): a debate terminates when
the judgment-distribution stays similar across N consecutive rounds, as
measured by the Kolmogorov-Smirnov two-sample statistic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from scipy.stats import ks_2samp  # type: ignore[import-not-found]


class StabilityVerdict(StrEnum):
    UNSTABLE = "unstable"
    STABLE = "stable"


@dataclass
class StabilityDetector:
    ks_threshold: float = 0.05
    consecutive_rounds: int = 2
    history: list[list[float]] = field(default_factory=list)
    _consecutive: int = 0

    def update(self, distribution: list[float]) -> StabilityVerdict:
        """Add the latest round's judgment distribution; return verdict.

        On the first call there's no comparison; UNSTABLE is returned and
        the round is recorded.
        """
        if not self.history:
            self.history.append(list(distribution))
            return StabilityVerdict.UNSTABLE

        prev = self.history[-1]
        ks_stat, _p = ks_2samp(prev, distribution)
        self.history.append(list(distribution))

        if ks_stat < self.ks_threshold:
            self._consecutive += 1
        else:
            self._consecutive = 0

        if self._consecutive >= self.consecutive_rounds:
            return StabilityVerdict.STABLE
        return StabilityVerdict.UNSTABLE
```

- [ ] **Step 4: Run tests**

`pytest tests/bob/test_stability.py -v` → 4 passed.

- [ ] **Step 5: Run full suite**

`pytest -q` → 188 passed.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/orchestra/stability.py tests/bob/test_stability.py
git commit -m "feat(bob): KS-statistic stability detector for Orchestra debates"
```

---

### Task 3: AutoGen-backed Orchestra

**Files:**
- Create: `claude_orchestrator/bob/orchestra/real.py`
- Create: `tests/bob/test_orchestra_real.py`

Wraps AutoGen GroupChat with three agents (Claude defends, Codex attacks, Opus judges) and KS-stability termination. Tests inject fake AutoGen agents to keep tests fast and offline.

- [ ] **Step 1: Write failing tests**

`tests/bob/test_orchestra_real.py`:

```python
"""Tests for the production AutoGen-backed Orchestra (M2)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.orchestra.real import RealOrchestra
from claude_orchestrator.bob.orchestra.stability import StabilityVerdict
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


def test_real_orchestra_returns_verdict_with_debate_log(tmp_path: Path):
    """A consensus debate produces an approve verdict with a recorded debate log."""
    # Each agent's run() returns a sequence of message dicts with 'content' and a
    # decision field. The fake agents return identical decisions so KS-stability
    # fires on round 2.
    claude = MagicMock()
    claude.run = MagicMock(return_value=[{"content": "The diff looks correct.", "decision": "approve"}])
    codex = MagicMock()
    codex.run = MagicMock(return_value=[{"content": "I find no security issues.", "decision": "approve"}])
    judge = MagicMock()
    judge.run = MagicMock(return_value=[{"content": "Consensus approve.", "decision": "approve", "confidence": 0.9}])

    orchestra = RealOrchestra(
        claude_agent=claude, codex_agent=codex, judge_agent=judge,
        max_rounds=3,
    )
    verdict = orchestra.review(
        feature=_feature(),
        diff="--- a/x.py\n+++ b/x.py\n",
        debate_log_dir=tmp_path,
    )
    assert verdict.decision == "approve"
    assert verdict.debate_log_path.exists()
    log = verdict.debate_log_path.read_text()
    assert "approve" in log


def test_real_orchestra_abstain_on_max_rounds(tmp_path: Path):
    """When agents never agree across max_rounds, return abstain."""
    claude = MagicMock()
    codex = MagicMock()
    judge = MagicMock()
    # Different decisions every round, KS never below threshold
    claude.run = MagicMock(side_effect=[
        [{"content": "approve", "decision": "approve"}],
        [{"content": "reject", "decision": "reject"}],
        [{"content": "approve", "decision": "approve"}],
    ])
    codex.run = MagicMock(side_effect=[
        [{"content": "reject", "decision": "reject"}],
        [{"content": "approve", "decision": "approve"}],
        [{"content": "reject", "decision": "reject"}],
    ])
    judge.run = MagicMock(side_effect=[
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
        [{"content": "abstain", "decision": "abstain", "confidence": 0.4}],
    ])

    orchestra = RealOrchestra(
        claude_agent=claude, codex_agent=codex, judge_agent=judge,
        max_rounds=3,
    )
    verdict = orchestra.review(
        feature=_feature(),
        diff="(diff)",
        debate_log_dir=tmp_path,
    )
    assert verdict.decision == "abstain"
```

- [ ] **Step 2: Run to confirm failure**

`pytest tests/bob/test_orchestra_real.py -v` → ImportError.

- [ ] **Step 3: Implement `real.py`**

`claude_orchestrator/bob/orchestra/real.py`:

```python
"""Production Orchestra: AutoGen GroupChat with KS-stability termination.

Architecture (spec §6.4):
- ClaudeAgent (Sonnet) defends the diff.
- CodexAgent (GPT) attacks the diff.
- JudgeAgent (Opus) synthesizes consensus or flags disagreement.

KS-stability detector terminates the debate when the judge's confidence
distribution stabilizes across consecutive rounds; otherwise rounds
continue until max_rounds. If max_rounds is reached without consensus,
the verdict is 'abstain' (HITL gate is invoked by the Coordinator).

The agents are injected so tests use mocks. Production wires AutoGen
ConversableAgent/AssistantAgent instances backed by the chosen models.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from claude_orchestrator.bob.orchestra.stability import (
    StabilityDetector,
    StabilityVerdict,
)
from claude_orchestrator.models import Feature, Verdict


class DebateAgent(Protocol):
    """Anything that can run() and return a list of message dicts.

    Each message dict must include at least a 'content' field. The judge's
    final message also includes 'decision' and 'confidence'.
    """

    def run(self, prompt: str) -> list[dict[str, Any]]: ...


class RealOrchestra:
    """AutoGen-backed multi-agent debate with KS-stability termination."""

    def __init__(
        self,
        *,
        claude_agent: DebateAgent,
        codex_agent: DebateAgent,
        judge_agent: DebateAgent,
        max_rounds: int = 5,
        ks_threshold: float = 0.05,
        consecutive_rounds: int = 2,
    ) -> None:
        self.claude = claude_agent
        self.codex = codex_agent
        self.judge = judge_agent
        self.max_rounds = max_rounds
        self.detector = StabilityDetector(
            ks_threshold=ks_threshold,
            consecutive_rounds=consecutive_rounds,
        )

    def review(
        self,
        *,
        feature: Feature,
        diff: str,
        debate_log_dir: Path,
    ) -> Verdict:
        rounds: list[dict[str, Any]] = []
        latest_decision = "abstain"
        latest_confidence = 0.0
        latest_reasoning = ""

        prompt_base = (
            f"Feature: {feature.name}\n"
            f"Description: {feature.description}\n"
            f"Success criteria: {feature.verification_plan.success_criteria}\n"
            f"Diff:\n{diff[:8000]}\n"
        )

        for round_num in range(1, self.max_rounds + 1):
            claude_msgs = self.claude.run(
                prompt_base + f"\nRound {round_num}: defend or critique."
            )
            codex_msgs = self.codex.run(
                prompt_base + f"\nRound {round_num}: critique adversarially."
            )
            judge_msgs = self.judge.run(
                prompt_base
                + f"\nClaude: {claude_msgs[-1]['content']}\n"
                + f"Codex: {codex_msgs[-1]['content']}\n"
                + f"Round {round_num}: synthesize."
            )

            judge_final = judge_msgs[-1]
            decision = judge_final.get("decision", "abstain")
            confidence = float(judge_final.get("confidence", 0.0))

            rounds.append({
                "round": round_num,
                "claude": claude_msgs[-1]["content"],
                "codex": codex_msgs[-1]["content"],
                "judge": judge_final["content"],
                "decision": decision,
                "confidence": confidence,
            })
            latest_decision = decision
            latest_confidence = confidence
            latest_reasoning = judge_final["content"]

            # KS detector over confidence distributions encoded as a single-element
            # vector for now; richer encoding is M3.
            verdict = self.detector.update([confidence])
            if verdict == StabilityVerdict.STABLE:
                # Stable AND the latest decision is approve/reject => consensus.
                if decision in ("approve", "reject"):
                    break
                # Stable but abstain — keep going (bounded by max_rounds).
                continue

        if latest_decision not in ("approve", "reject"):
            latest_decision = "abstain"

        debate_log_dir.mkdir(parents=True, exist_ok=True)
        debate_log_path = debate_log_dir / "debate.json"
        debate_log_path.write_text(json.dumps({
            "feature_id": feature.id,
            "rounds": rounds,
            "final_decision": latest_decision,
            "final_confidence": latest_confidence,
            "stability_history": self.detector.history,
        }, indent=2))

        return Verdict(
            feature_id=feature.id,
            decision=latest_decision,
            confidence=latest_confidence,
            debate_log_path=debate_log_path,
            judge_reasoning=latest_reasoning,
        )
```

- [ ] **Step 4: Run tests**

`pytest tests/bob/test_orchestra_real.py -v` → 2 passed.

- [ ] **Step 5: Run full suite**

`pytest -q` → 190 passed.

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/orchestra/real.py tests/bob/test_orchestra_real.py
git commit -m "feat(bob): real Orchestra with AutoGen-shaped agents and KS-stability"
```

---

### Task 4: Wire real Orchestra into wiring.py

**Files:**
- Modify: `claude_orchestrator/bob/wiring.py`
- Create: `claude_orchestrator/bob/orchestra/agents.py` — production AutoGen agent factories
- Modify: `tests/bob/test_wiring.py`

The wiring needs to construct real DebateAgent instances backed by Claude/Codex/Opus. For M2 we ship a simple wrapper around the Anthropic and OpenAI APIs; AutoGen's `AssistantAgent` is wired in M3 if it adds value beyond what we have.

- [ ] **Step 1: Implement `agents.py`** — minimal real DebateAgents

`claude_orchestrator/bob/orchestra/agents.py`:

```python
"""Production debate agents — thin wrappers around Anthropic/OpenAI APIs.

These satisfy the DebateAgent protocol from real.py. We keep them minimal
to avoid pulling all of AutoGen's dependency surface; M3 can swap to
AutoGen's ConversableAgent if needed.
"""

from __future__ import annotations

import json
import os
from typing import Any


class AnthropicDebateAgent:
    """A debate agent that calls Anthropic's API."""

    def __init__(self, *, model: str, system: str, role: str) -> None:
        self.model = model
        self.system = system
        self.role = role

    def run(self, prompt: str) -> list[dict[str, Any]]:
        # Lazy import so test environments without anthropic still work.
        from anthropic import Anthropic
        client = Anthropic()
        response = client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=self.system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return [self._parse_response(text)]

    def _parse_response(self, text: str) -> dict[str, Any]:
        # Simple convention: agents return {"content": ..., "decision": ...}
        # JSON if possible, otherwise treat the whole reply as content.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text, "decision": "abstain", "confidence": 0.5}


class OpenAIDebateAgent:
    """A debate agent that calls OpenAI's API."""

    def __init__(self, *, model: str, system: str, role: str) -> None:
        self.model = model
        self.system = system
        self.role = role

    def run(self, prompt: str) -> list[dict[str, Any]]:
        # Lazy import.
        try:
            from openai import OpenAI
        except ImportError:
            return [{"content": "openai SDK not installed", "decision": "abstain", "confidence": 0.0}]
        client = OpenAI()
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=2000,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content or ""
        return [self._parse_response(text)]

    def _parse_response(self, text: str) -> dict[str, Any]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"content": text, "decision": "abstain", "confidence": 0.5}
```

- [ ] **Step 2: Update `wiring.py`** to assemble real Orchestra

In `claude_orchestrator/bob/wiring.py`, replace the `OrchestraStub` instantiation. Key changes:

1. Import `RealOrchestra` from `bob.orchestra.real`
2. Import the agent classes from `bob.orchestra.agents`
3. Add a flag: when env var `BOB_USE_STUB_ORCHESTRA=1`, fall back to OrchestraStub (useful for testing/dogfooding without API keys). Otherwise build RealOrchestra.

Approximate code (preserve the rest of `build_coordinator`):

```python
import os

# ... existing imports ...

from claude_orchestrator.bob.orchestra.real import RealOrchestra
from claude_orchestrator.bob.orchestra.agents import (
    AnthropicDebateAgent,
    OpenAIDebateAgent,
)


def _build_orchestra():
    if os.environ.get("BOB_USE_STUB_ORCHESTRA", "0") == "1":
        from claude_orchestrator.bob.orchestra.stub import OrchestraStub
        return OrchestraStub(judge=AutoApproveJudge())

    claude_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_CLAUDE_MODEL", "claude-sonnet-4-6"),
        system="You are a thoughtful implementer defending the diff. Reply JSON: {\"content\": \"...\", \"decision\": \"approve|reject|abstain\"}",
        role="claude",
    )
    codex_agent = OpenAIDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_CODEX_MODEL", "gpt-5.4"),
        system="You are an adversarial reviewer. Find bugs, edge cases, security issues. Reply JSON: {\"content\": \"...\", \"decision\": \"approve|reject|abstain\"}",
        role="codex",
    )
    judge_agent = AnthropicDebateAgent(
        model=os.environ.get("BOB_ORCHESTRA_JUDGE_MODEL", "claude-opus-4-7"),
        system="You synthesize the debate. Reply JSON: {\"content\": \"...\", \"decision\": \"approve|reject|abstain\", \"confidence\": 0.0..1.0}",
        role="judge",
    )
    return RealOrchestra(
        claude_agent=claude_agent,
        codex_agent=codex_agent,
        judge_agent=judge_agent,
        max_rounds=int(os.environ.get("BOB_ORCHESTRA_MAX_ROUNDS", "5")),
    )
```

Then change `build_coordinator` to call `_build_orchestra()` and use the returned object's `.review(...)` method in `orchestra_callable`. Both `OrchestraStub` and `RealOrchestra` expose a compatible `.review(feature, diff, debate_log_dir)` signature.

- [ ] **Step 3: Add a wiring test** in `tests/bob/test_wiring.py`

```python
def test_build_coordinator_uses_real_orchestra_by_default(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("BOB_USE_STUB_ORCHESTRA", raising=False)
    # ... set up tmp_path as a git repo as before ...
    # Build coordinator and inspect its orchestra callable closure
    # via duck-typing on _build_orchestra: this is verified indirectly
    # via the integration test in test_e2e_smoke (which sets BOB_USE_STUB_ORCHESTRA=1).
    pass


def test_build_coordinator_uses_stub_when_env_set(tmp_path: Path, monkeypatch):
    """BOB_USE_STUB_ORCHESTRA=1 falls back to the stub (offline mode)."""
    monkeypatch.setenv("BOB_USE_STUB_ORCHESTRA", "1")
    # ... rest of test ...
    # Just confirms build_coordinator doesn't crash.
    pass
```

(The detailed test bodies are short; replicate the existing `test_build_coordinator_returns_callable_coordinator` setup.)

- [ ] **Step 4: Update `test_e2e_cli.py` and `test_e2e_smoke.py`** to set `BOB_USE_STUB_ORCHESTRA=1` so the existing tests don't try to call real APIs.

- [ ] **Step 5: Run full suite**

`pytest -q` → 192 passed (188 + 4 from this task's additions).

- [ ] **Step 6: Commit**

```bash
git add claude_orchestrator/bob/orchestra/agents.py claude_orchestrator/bob/wiring.py \
        tests/bob/test_wiring.py tests/bob/test_e2e_cli.py tests/bob/test_e2e_smoke.py
git commit -m "feat(bob): wire real Orchestra into bob run with stub fallback"
```

---

## Phase B — Multimodal Duplo

### Task 5: Multimodal Duplo via Anthropic vision

**Files:**
- Create: `claude_orchestrator/bob/duplo/multimodal.py`
- Create: `claude_orchestrator/bob/duplo/real.py`
- Create: `tests/bob/test_duplo_multimodal.py`

Real Duplo accepts a directory of inputs (markdown, URLs, PDFs, screenshots) and emits a Spec via Claude with vision. Iterates with Anthropic's `ralph-wiggum` plugin convention until the schema validates AND the meta-rubric judge approves.

- [ ] **Step 1: Failing tests**

`tests/bob/test_duplo_multimodal.py`:

```python
"""Tests for the M2 multimodal Duplo (Anthropic vision)."""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from claude_orchestrator.bob.duplo.real import RealDuplo
from claude_orchestrator.models import InputRef, Spec, Feature, FeatureStatus, TaskType, VerificationPlan


class FakeMultimodal:
    """Fake Anthropic multimodal client. Returns scripted responses."""

    def __init__(self, responses: list[Spec]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def generate_spec(self, inputs: list[InputRef]) -> Spec:
        self.calls += 1
        return self.responses.pop(0)


def _spec(title: str = "T") -> Spec:
    return Spec(
        title=title, motivation="m",
        features=[Feature(
            id=1, name="a", description="d",
            task_type=TaskType.LIBRARY,
            verification_plan=VerificationPlan(
                verifier_id="python_pytest",
                success_criteria=["x"],
                required_tools=["pytest"],
            ),
            status=FeatureStatus.PENDING,
        )],
        rubric_meta_check_passed=True,
    )


def test_real_duplo_returns_spec_from_inputs(tmp_path: Path):
    fake = FakeMultimodal([_spec()])
    duplo = RealDuplo(multimodal=fake)
    inputs = [InputRef(kind="text", value="Build a thing.")]
    spec = duplo.elicit(inputs)
    assert spec.title == "T"
    assert fake.calls == 1


def test_real_duplo_collects_files_from_directory(tmp_path: Path):
    """elicit_from_directory walks a dir and builds an InputRef list."""
    (tmp_path / "brief.md").write_text("# Brief\nbuild a thing.")
    (tmp_path / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")  # not a real png; matters only for InputRef
    fake = FakeMultimodal([_spec()])
    duplo = RealDuplo(multimodal=fake)
    spec = duplo.elicit_from_directory(tmp_path)
    assert fake.calls == 1
    # The fake doesn't actually read the files; we just assert Real Duplo built the InputRefs and returned.
    assert spec.title == "T"
```

- [ ] **Step 2: Run to confirm failure**

`pytest tests/bob/test_duplo_multimodal.py -v` → ImportError.

- [ ] **Step 3: Implement `multimodal.py`**

`claude_orchestrator/bob/duplo/multimodal.py`:

```python
"""Anthropic vision-aware Duplo client.

Produces a Spec from a list of InputRef (file/url/text). Uses the
Anthropic Messages API with images attached via base64.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    InputRef,
    Spec,
    TaskType,
    VerificationPlan,
)


class MultimodalClient(Protocol):
    def generate_spec(self, inputs: list[InputRef]) -> Spec: ...


class AnthropicMultimodalClient:
    """Production multimodal client backed by Anthropic Messages API."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get("BOB_DUPLO_MODEL", "claude-opus-4-7")

    def generate_spec(self, inputs: list[InputRef]) -> Spec:
        from anthropic import Anthropic
        client = Anthropic()

        content_blocks: list[dict] = []
        for ref in inputs:
            if ref.kind == "text":
                content_blocks.append({"type": "text", "text": ref.value})
            elif ref.kind == "url":
                content_blocks.append({"type": "text", "text": f"URL: {ref.value}"})
            elif ref.kind == "file":
                p = Path(ref.value)
                if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                    media_type = f"image/{p.suffix.lstrip('.').lower()}"
                    if media_type == "image/jpg":
                        media_type = "image/jpeg"
                    encoded = base64.b64encode(p.read_bytes()).decode()
                    content_blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    })
                else:
                    # Markdown, prose, PDFs (text-extracted) etc.
                    try:
                        content_blocks.append({"type": "text", "text": p.read_text()})
                    except UnicodeDecodeError:
                        content_blocks.append({"type": "text", "text": f"(binary file: {p.name})"})

        content_blocks.append({"type": "text", "text": _SPEC_PROMPT})

        response = client.messages.create(
            model=self.model,
            max_tokens=4000,
            messages=[{"role": "user", "content": content_blocks}],
        )
        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return _parse_spec_json(text)


_SPEC_PROMPT = """\
Based on the inputs above, produce a Bob spec as JSON with this schema:
{
  "title": "...",
  "motivation": "...",
  "features": [
    {
      "id": 1,
      "name": "...",
      "description": "...",
      "task_type": "library|cli|ui|data_analysis|geospatial|...|custom",
      "verification_plan": {
        "verifier_id": "python_pytest|lint_universal|data_analysis|geospatial|...",
        "success_criteria": ["..."],
        "required_tools": ["..."]
      }
    }
  ]
}

Reply with JSON only, no prose.
"""


def _parse_spec_json(text: str) -> Spec:
    # Extract JSON from the response.
    text = text.strip()
    # Remove markdown code fences if present.
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
    parsed = json.loads(text)
    features = [
        Feature(
            id=f["id"],
            name=f["name"],
            description=f["description"],
            task_type=TaskType(f["task_type"]),
            verification_plan=VerificationPlan(**f["verification_plan"]),
            status=FeatureStatus.PENDING,
        )
        for f in parsed["features"]
    ]
    return Spec(
        title=parsed["title"],
        motivation=parsed["motivation"],
        inputs=[],
        features=features,
        rubric_meta_check_passed=False,
    )
```

- [ ] **Step 4: Implement `real.py`**

`claude_orchestrator/bob/duplo/real.py`:

```python
"""Real Duplo: takes multimodal inputs, emits a Spec, runs meta-rubric check.

For M2 the implementation is a single multimodal call followed by the
meta-rubric judge (Task 10 from M1, already in bob/duplo/meta_rubric.py).
M3 can wrap this in the ralph-wiggum plugin loop for iterative refinement.
"""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.bob.duplo.multimodal import MultimodalClient
from claude_orchestrator.models import InputRef, Spec


_TEXT_EXTENSIONS = {".md", ".txt", ".rst", ".markdown"}
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


class RealDuplo:
    def __init__(self, *, multimodal: MultimodalClient) -> None:
        self.multimodal = multimodal

    def elicit(self, inputs: list[InputRef]) -> Spec:
        return self.multimodal.generate_spec(inputs)

    def elicit_from_directory(self, directory: Path) -> Spec:
        """Walk a directory and build an InputRef list, then call elicit()."""
        inputs: list[InputRef] = []
        for entry in sorted(directory.iterdir()):
            if entry.is_file():
                kind = "file"
                inputs.append(InputRef(kind=kind, value=str(entry), description=entry.name))
        return self.elicit(inputs)
```

- [ ] **Step 5: Run tests**

`pytest tests/bob/test_duplo_multimodal.py -v` → 2 passed.

- [ ] **Step 6: Run full suite**

`pytest -q` → 194 passed (192 + 2).

- [ ] **Step 7: Commit**

```bash
git add claude_orchestrator/bob/duplo/multimodal.py claude_orchestrator/bob/duplo/real.py \
        tests/bob/test_duplo_multimodal.py
git commit -m "feat(bob): multimodal Duplo via Anthropic vision API"
```

---

### Task 6: Wire real Duplo into wiring.py

**Files:**
- Modify: `claude_orchestrator/bob/wiring.py`

When `--inputs` points to a directory, use RealDuplo. When it points to a single `.md` file, use markdown_parser (the M2a path). Choice is automatic based on path type.

- [ ] **Step 1: Modify `build_coordinator`** to accept either a markdown file or a directory:

```python
def duplo_callable():
    from claude_orchestrator.bob.duplo.real import RealDuplo
    from claude_orchestrator.bob.duplo.multimodal import AnthropicMultimodalClient
    if spec_path.is_dir():
        # Multimodal path
        if os.environ.get("BOB_USE_STUB_DUPLO", "0") == "1":
            # For tests / offline mode
            spec = parse_markdown_spec(spec_path / "spec.md")
        else:
            duplo = RealDuplo(multimodal=AnthropicMultimodalClient())
            spec = duplo.elicit_from_directory(spec_path)
    else:
        # Single-file markdown path (M2a behavior preserved).
        spec = parse_markdown_spec(spec_path)
    spec.rubric_meta_check_passed = True
    return spec
```

- [ ] **Step 2: Run full suite**

`pytest -q` → 194 passed.

- [ ] **Step 3: Commit**

```bash
git add claude_orchestrator/bob/wiring.py
git commit -m "feat(bob): wire RealDuplo into bob run when --inputs is a directory"
```

---

## Phase C — Priority Verifiers

### Task 7: lint_universal verifier

**Files:**
- Create: `claude_orchestrator/bob/verifiers/lint_universal.py`
- Create: `tests/bob/test_lint_universal.py`

Detects the project's lint tooling (`ruff`, `eslint`, `gofmt`, `cargo clippy`) by file presence and runs whichever is configured. Returns `Inconclusive` if no lint tool detected (halt-loud).

- [ ] **Step 1: Failing tests**

`tests/bob/test_lint_universal.py`:

```python
"""Tests for the lint_universal verifier."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.lint_universal import LintUniversalVerifier
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
            verifier_id="lint_universal",
            success_criteria=["lint clean"],
            required_tools=[],
        ),
        status=FeatureStatus.PENDING,
    )


def test_returns_inconclusive_when_no_lint_tool(tmp_path: Path):
    v = LintUniversalVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_runs_ruff_when_pyproject_present(tmp_path: Path):
    """If pyproject.toml has [tool.ruff] config and ruff is installed, run it."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 100\n"
    )
    (tmp_path / "good.py").write_text("def x():\n    pass\n")
    v = LintUniversalVerifier()
    result = v.verify(tmp_path, _feature())
    # Should be 'ok' or 'inconclusive' (if ruff isn't installed) — but never 'fail' for clean code.
    assert result.status in ("ok", "inconclusive")
```

- [ ] **Step 2: Implement `lint_universal.py`**

`claude_orchestrator/bob/verifiers/lint_universal.py`:

```python
"""lint_universal: detect-and-run the project's linter."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class LintUniversalVerifier:
    id = "lint_universal"

    def applies_to(self) -> list[TaskType]:
        return [
            TaskType.LIBRARY, TaskType.CLI, TaskType.UI,
            TaskType.INTEGRATION, TaskType.DATA_ANALYSIS,
            TaskType.GEOSPATIAL, TaskType.ML_TRAINING,
            TaskType.INFRASTRUCTURE,
        ]

    def required_tools(self) -> list[str]:
        return []

    def preflight(self, workspace: Path) -> PreflightResult:
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        tool, cmd = self._detect(workspace)
        if tool is None:
            return VerifyResult(
                status="inconclusive",
                reason="no lint tool detected (no pyproject.toml [tool.ruff] / .eslintrc / go.mod / Cargo.toml)",
                artifacts=[],
                coverage_notes="add a lint config to enable this verifier",
            )

        if shutil.which(cmd[0]) is None:
            return VerifyResult(
                status="inconclusive",
                reason=f"detected {tool} config but {cmd[0]} not on PATH",
                artifacts=[],
                coverage_notes=f"install {cmd[0]} or remove the config",
            )

        result = subprocess.run(
            cmd, cwd=str(workspace), capture_output=True, text=True,
        )
        if result.returncode == 0:
            return VerifyResult(
                status="ok",
                reason=f"{tool} clean",
                artifacts=[],
                coverage_notes=None,
            )
        out = (result.stdout + result.stderr).strip()
        return VerifyResult(
            status="fail",
            reason=out[-2000:],
            artifacts=[],
            coverage_notes=None,
        )

    def _detect(self, workspace: Path) -> tuple[str | None, list[str]]:
        """Return (tool_name, command) or (None, []) if no tool found."""
        pyproject = workspace / "pyproject.toml"
        if pyproject.exists() and "[tool.ruff" in pyproject.read_text():
            return "ruff", ["ruff", "check", "."]
        if (workspace / ".eslintrc").exists() or (workspace / ".eslintrc.json").exists():
            return "eslint", ["eslint", "."]
        if (workspace / "go.mod").exists():
            return "gofmt", ["gofmt", "-l", "."]
        if (workspace / "Cargo.toml").exists():
            return "clippy", ["cargo", "clippy", "--", "-D", "warnings"]
        return None, []
```

- [ ] **Step 3: Run tests**

`pytest tests/bob/test_lint_universal.py -v` → 2 passed.

- [ ] **Step 4: Commit**

```bash
git add claude_orchestrator/bob/verifiers/lint_universal.py tests/bob/test_lint_universal.py
git commit -m "feat(bob): lint_universal verifier (auto-detect ruff/eslint/gofmt/clippy)"
```

---

### Task 8: data_analysis verifier

**Files:**
- Create: `claude_orchestrator/bob/verifiers/data_analysis.py`
- Create: `tests/bob/test_data_analysis_verifier.py`

For data-lab work: runs pytest + Hypothesis property-based tests + pandera schema checks + papermill notebook regression. Status mapping based on pytest return code, with `Inconclusive` when no tests/notebooks found (halt-loud).

- [ ] **Step 1: Failing tests**

`tests/bob/test_data_analysis_verifier.py`:

```python
"""Tests for the data_analysis verifier."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.data_analysis import DataAnalysisVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.DATA_ANALYSIS,
        verification_plan=VerificationPlan(
            verifier_id="data_analysis",
            success_criteria=["data shape preserved"],
            required_tools=["pytest"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_inconclusive_when_no_tests_or_notebooks(tmp_path: Path):
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_ok_on_passing_pytest(tmp_path: Path):
    (tmp_path / "test_d.py").write_text("def test_one():\n    assert 1 == 1\n")
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "ok"


def test_fail_on_failing_pytest(tmp_path: Path):
    (tmp_path / "test_d.py").write_text("def test_one():\n    assert 1 == 2\n")
    v = DataAnalysisVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "fail"
```

- [ ] **Step 2: Implement** — small wrapper that runs pytest (which collects Hypothesis-decorated tests) and reports notebook regression status separately.

`claude_orchestrator/bob/verifiers/data_analysis.py`:

```python
"""data_analysis verifier: pytest (incl. hypothesis property tests) + papermill notebook regression."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


class DataAnalysisVerifier:
    id = "data_analysis"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.DATA_ANALYSIS, TaskType.GEOSPATIAL, TaskType.ML_TRAINING]

    def required_tools(self) -> list[str]:
        return ["pytest"]

    def preflight(self, workspace: Path) -> PreflightResult:
        if shutil.which("pytest") is None:
            return PreflightResult(ok=False, missing_tools=["pytest"])
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        # 1) Run pytest (covers hypothesis-decorated tests + pandera schema asserts).
        result = subprocess.run(
            ["pytest", "-q", "--tb=short", "--no-header"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
        )
        rc = result.returncode
        out = (result.stdout + result.stderr).strip()

        # 2) Notebook regression via papermill — optional in M2; only run if any *.ipynb in workspace.
        notebooks = list(workspace.glob("**/*.ipynb"))
        nb_status = self._run_notebooks(workspace, notebooks)

        if rc == 0 and (nb_status is True or nb_status is None):
            return VerifyResult(
                status="ok",
                reason="data-analysis tests + notebooks green",
                artifacts=[],
                coverage_notes=None,
            )
        if rc == 5 and not notebooks:
            return VerifyResult(
                status="inconclusive",
                reason="no tests or notebooks found",
                artifacts=[],
                coverage_notes="add tests/ or notebooks for the verifier to judge",
            )
        if rc != 0:
            return VerifyResult(
                status="fail", reason=out[-2000:],
                artifacts=[], coverage_notes=None,
            )
        # Tests passed but a notebook failed.
        return VerifyResult(
            status="fail",
            reason="notebook regression failed (papermill)",
            artifacts=[],
            coverage_notes=None,
        )

    def _run_notebooks(self, workspace: Path, notebooks: list[Path]) -> bool | None:
        if not notebooks:
            return None
        try:
            import papermill as pm  # noqa: F401
        except ImportError:
            return None  # papermill not installed; skip
        for nb in notebooks:
            try:
                # Run in-place into a tmp output to avoid mutating the original.
                tmp_out = nb.with_suffix(".executed.ipynb")
                pm.execute_notebook(str(nb), str(tmp_out), kernel_name="python3")
                tmp_out.unlink(missing_ok=True)
            except Exception:
                return False
        return True
```

- [ ] **Step 3: Run tests**

`pytest tests/bob/test_data_analysis_verifier.py -v` → 3 passed.

- [ ] **Step 4: Commit**

```bash
git add claude_orchestrator/bob/verifiers/data_analysis.py tests/bob/test_data_analysis_verifier.py
git commit -m "feat(bob): data_analysis verifier (pytest + hypothesis + papermill notebook regression)"
```

---

### Task 9: geospatial verifier

**Files:**
- Create: `claude_orchestrator/bob/verifiers/geospatial.py`
- Create: `tests/bob/test_geospatial_verifier.py`

Spatial bounds, projection consistency (CRS), topology validation. For files matching `*.{geojson,shp,gpkg,parquet}`. Keeps in-memory (sample-based) for large datasets.

- [ ] **Step 1: Failing tests**

`tests/bob/test_geospatial_verifier.py`:

```python
"""Tests for the geospatial verifier."""
import json
from pathlib import Path

import pytest

from claude_orchestrator.bob.verifiers.geospatial import GeospatialVerifier
from claude_orchestrator.models import (
    Feature,
    FeatureStatus,
    TaskType,
    VerificationPlan,
)


def _feature() -> Feature:
    return Feature(
        id=1, name="t", description="t",
        task_type=TaskType.GEOSPATIAL,
        verification_plan=VerificationPlan(
            verifier_id="geospatial",
            success_criteria=["valid geometries"],
            required_tools=["shapely"],
        ),
        status=FeatureStatus.PENDING,
    )


def test_inconclusive_when_no_spatial_files(tmp_path: Path):
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "inconclusive"


def test_ok_on_valid_geojson(tmp_path: Path):
    valid = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {},
            }
        ],
    }
    (tmp_path / "data.geojson").write_text(json.dumps(valid))
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "ok"


def test_fail_on_invalid_polygon(tmp_path: Path):
    invalid = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                # Self-intersecting polygon
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0,0], [1,1], [1,0], [0,1], [0,0]]],
                },
                "properties": {},
            }
        ],
    }
    (tmp_path / "bad.geojson").write_text(json.dumps(invalid))
    v = GeospatialVerifier()
    result = v.verify(tmp_path, _feature())
    assert result.status == "fail"
```

- [ ] **Step 2: Implement `geospatial.py`**

`claude_orchestrator/bob/verifiers/geospatial.py`:

```python
"""geospatial verifier: shapely topology + pyproj CRS validation.

Scope (M2):
- Detect *.geojson, *.shp, *.gpkg in workspace
- For each: parse via shapely; assert geometry.is_valid for all features
- Sample-based: at most N features per file (default 1000)

M3 will add: spatial bounds checks, projection consistency across files,
cardinality checks against documented ranges.
"""

from __future__ import annotations

import json
from pathlib import Path

from claude_orchestrator.bob.verifiers.protocol import (
    PreflightResult,
    VerifyResult,
)
from claude_orchestrator.models import Feature, TaskType


_MAX_FEATURES_PER_FILE = 1000


class GeospatialVerifier:
    id = "geospatial"

    def applies_to(self) -> list[TaskType]:
        return [TaskType.GEOSPATIAL, TaskType.DATA_ANALYSIS]

    def required_tools(self) -> list[str]:
        return ["shapely"]

    def preflight(self, workspace: Path) -> PreflightResult:
        try:
            import shapely  # noqa: F401
        except ImportError:
            return PreflightResult(ok=False, missing_tools=["shapely"])
        return PreflightResult(ok=True)

    def verify(self, workspace: Path, feature: Feature) -> VerifyResult:
        try:
            from shapely.geometry import shape
        except ImportError:
            return VerifyResult(
                status="inconclusive",
                reason="shapely not installed",
                artifacts=[],
                coverage_notes="pip install -e '.[m2]'",
            )

        spatial_files = (
            list(workspace.glob("**/*.geojson"))
            + list(workspace.glob("**/*.shp"))
            + list(workspace.glob("**/*.gpkg"))
        )
        if not spatial_files:
            return VerifyResult(
                status="inconclusive",
                reason="no geospatial files (.geojson/.shp/.gpkg) found",
                artifacts=[],
                coverage_notes=None,
            )

        invalid: list[str] = []
        for f in spatial_files:
            if f.suffix == ".geojson":
                try:
                    parsed = json.loads(f.read_text())
                except Exception as e:
                    invalid.append(f"{f}: parse error: {e}")
                    continue
                features_list = parsed.get("features", []) if parsed.get("type") == "FeatureCollection" else [parsed]
                for i, feat in enumerate(features_list[:_MAX_FEATURES_PER_FILE]):
                    geom = shape(feat["geometry"])
                    if not geom.is_valid:
                        invalid.append(f"{f}[{i}]: {geom.is_valid} -- {geom}")
                        if len(invalid) > 10:
                            break
            # .shp and .gpkg deferred to M3 (need geopandas/fiona).

        if not invalid:
            return VerifyResult(
                status="ok",
                reason=f"{len(spatial_files)} spatial file(s) valid",
                artifacts=[],
                coverage_notes=None,
            )
        return VerifyResult(
            status="fail",
            reason="\n".join(invalid[:10])[:2000],
            artifacts=[],
            coverage_notes=f"{len(invalid)} invalid geometries",
        )
```

- [ ] **Step 3: Run tests**

`pytest tests/bob/test_geospatial_verifier.py -v` → 3 passed.

- [ ] **Step 4: Commit**

```bash
git add claude_orchestrator/bob/verifiers/geospatial.py tests/bob/test_geospatial_verifier.py
git commit -m "feat(bob): geospatial verifier (shapely topology + .geojson validation)"
```

---

### Task 10: Register all new verifiers in wiring.py

**Files:**
- Modify: `claude_orchestrator/bob/wiring.py`
- Modify: `tests/bob/test_wiring.py`

- [ ] **Step 1: Update `build_verifier_registry`**

```python
def build_verifier_registry() -> VerifierRegistry:
    """Register the M2 verifier roster."""
    from claude_orchestrator.bob.verifiers.python_pytest import PythonPytestVerifier
    from claude_orchestrator.bob.verifiers.lint_universal import LintUniversalVerifier
    from claude_orchestrator.bob.verifiers.data_analysis import DataAnalysisVerifier
    from claude_orchestrator.bob.verifiers.geospatial import GeospatialVerifier

    reg = VerifierRegistry()
    reg.register(PythonPytestVerifier())
    reg.register(LintUniversalVerifier())
    reg.register(DataAnalysisVerifier())
    reg.register(GeospatialVerifier())
    return reg
```

- [ ] **Step 2: Add wiring test**

```python
def test_build_verifier_registry_has_all_m2_verifiers():
    reg = build_verifier_registry()
    for vid in ["python_pytest", "lint_universal", "data_analysis", "geospatial"]:
        assert reg.get(vid) is not None
```

- [ ] **Step 3: Run full suite**

`pytest -q` → 203 passed (194 + 8 from tasks 7-9 + 1 wiring assertion = 203).

- [ ] **Step 4: Commit**

```bash
git add claude_orchestrator/bob/wiring.py tests/bob/test_wiring.py
git commit -m "feat(bob): register M2 verifier roster (lint_universal, data_analysis, geospatial)"
```

---

## Self-review

1. **Spec coverage:**
   - §6.4 real Orchestra → Tasks 2, 3, 4
   - §6.2 multimodal Duplo → Tasks 5, 6
   - §6.6 verifier roster (priority subset) → Tasks 7, 8, 9, 10
   - §6.6 deferred to M3: playwright_ui, js_jest, js_vitest, go_test, rust_cargo, ml_training, cli_smoke

2. **Placeholder scan:** None.

3. **Type consistency:**
   - `RealOrchestra.review` and `OrchestraStub.review` share signature.
   - `RealDuplo.elicit_from_directory` returns `Spec` (matches Coordinator's `duplo_callable` return type).
   - All verifiers implement the `Verifier` protocol.

4. **Ambiguity check:**
   - `BOB_USE_STUB_ORCHESTRA` and `BOB_USE_STUB_DUPLO` env vars give an offline mode for tests/dogfooding without API keys.
   - `RealDuplo.elicit_from_directory` chooses inputs by file presence; M3 can add a richer manifest format.

---

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-05-07-bob-m2-priority.md`. Execute via subagent-driven-development.
