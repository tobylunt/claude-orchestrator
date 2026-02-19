"""Custom exception hierarchy for the orchestrator."""


class OrchestratorError(Exception):
    """Base exception for the orchestrator."""


class FeatureExecutionError(OrchestratorError):
    """Error during feature execution."""

    def __init__(self, feature_id: int, message: str, retriable: bool = True):
        self.feature_id = feature_id
        self.retriable = retriable
        super().__init__(f"Feature #{feature_id}: {message}")


class StallError(FeatureExecutionError):
    """Worker session stalled (no tool activity within timeout)."""

    def __init__(self, feature_id: int, seconds: float):
        super().__init__(feature_id, f"Stalled for {seconds:.0f}s", retriable=True)


class HumanTimeoutError(OrchestratorError):
    """Human did not respond within timeout."""


class SpecParseError(OrchestratorError):
    """Failed to parse spec into features."""


class StateCorruptionError(OrchestratorError):
    """Features.json or progress file is corrupted."""
