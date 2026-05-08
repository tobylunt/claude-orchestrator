"""Real Duplo: takes multimodal inputs, emits a Spec."""

from __future__ import annotations

from pathlib import Path

from claude_orchestrator.bob.duplo.multimodal import MultimodalClient
from claude_orchestrator.models import InputRef, Spec


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
                inputs.append(InputRef(kind="file", value=str(entry), description=entry.name))
        return self.elicit(inputs)
