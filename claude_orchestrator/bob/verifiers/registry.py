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
