"""HITL gates for Bob.

M1 ships the post-Duplo gate only. Orchestra-disagreement and Vroom-triage
gates land with their respective phases (M2 / M3).
"""

from __future__ import annotations

from typing import Any, Protocol, TYPE_CHECKING

from claude_orchestrator.models import Spec, StrEnum

if TYPE_CHECKING:
    from claude_orchestrator.bob.yolo import YoloConfig


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

    def __init__(self, *, yolo: "YoloConfig | None" = None) -> None:
        self.yolo = yolo

    def run(self, spec: Spec) -> GateDecision:
        # YOLO auto-approve: enabled + meta-rubric passed.
        if (
            self.yolo is not None
            and self.yolo.enabled
            and spec.rubric_meta_check_passed
        ):
            print("[YOLO] post-Duplo gate auto-approved (meta-rubric passed)")
            return GateDecision.APPROVE

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
