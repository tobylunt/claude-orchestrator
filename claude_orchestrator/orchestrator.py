"""Main orchestration loop: state machine advancing through features with retry."""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime
from typing import TYPE_CHECKING

from .logging_config import setup_logger
from .models import Feature, FeatureResult, FeatureStatus, ProgressEntry
from .runner import FeatureRunner
from .state import StateManager

if TYPE_CHECKING:
    from .config import OrchestratorConfig


class Orchestrator:
    """Main orchestration loop: advance through features with retry and error handling."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.logger = setup_logger(config)
        self.state = StateManager(
            features_path=config.project_dir / config.features_file,
            progress_path=config.project_dir / config.progress_file,
        )
        self.runner = FeatureRunner(config)
        self._shutdown_requested = False

    async def run(self) -> None:
        """Main execution loop with graceful shutdown on Ctrl-C."""

        # Install signal handlers for graceful shutdown
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown_signal, sig)

        self.logger.info("=" * 60)
        self.logger.info("Claude Code Orchestrator starting")
        self.logger.info(f"Project: {self.config.project_dir}")
        self.logger.info("=" * 60)

        # Load state
        features = self.state.load_features()
        self.logger.info(f"Loaded {len(features)} features")
        self.logger.info(self.state.get_progress_summary())

        if self.config.dry_run:
            self._dry_run(features)
            return

        consecutive_failures = 0
        max_consecutive_failures = 3

        try:
            while not self._shutdown_requested:
                # Find next feature
                feature = self.state.get_next_feature(self.config.start_from_feature)
                if feature is None:
                    self.logger.info("All features complete!")
                    break

                if (
                    self.config.stop_after_feature is not None
                    and feature.id > self.config.stop_after_feature
                ):
                    self.logger.info(
                        f"Reached stop-after limit (feature #{self.config.stop_after_feature})"
                    )
                    break

                # Check retry budget
                if feature.attempts >= self.config.max_retries:
                    self.logger.error(
                        f"Feature #{feature.id} has exhausted {self.config.max_retries} retries. "
                        f"Last error: {feature.last_error}"
                    )
                    action = await self._ask_retry_exhausted(feature)
                    if action == "skip":
                        feature.status = FeatureStatus.SKIPPED
                        self.state.save_features()
                        continue
                    elif action == "retry":
                        feature.attempts = 0
                        self.state.save_features()
                        # Fall through to execution
                    else:  # abort
                        self.logger.info("User chose to abort orchestration")
                        break

                # Display progress
                self._print_feature_header(feature, features)

                # Execute with retry
                result = await self._execute_with_retry(feature)

                # Record result
                self.state.mark_feature(feature.id, result)

                # Log progress
                self.state.append_progress(ProgressEntry(
                    timestamp=datetime.now(),
                    feature_id=feature.id,
                    feature_name=feature.name,
                    status=FeatureStatus.PASSED if result.success else FeatureStatus.FAILED,
                    summary=(
                        f"Completed successfully in {result.duration_seconds:.0f}s"
                        if result.success
                        else f"Failed after {result.retries_used} retries: {result.error}"
                    ),
                    commit_hash=result.commit_hash,
                    session_id=result.session_id,
                    error=result.error,
                ))

                if result.success:
                    consecutive_failures = 0
                    cost_str = f"${result.cost_usd:.2f}" if result.cost_usd else "n/a"
                    self.logger.info(
                        f"Feature #{feature.id} PASSED "
                        f"({result.duration_seconds:.0f}s, cost: {cost_str})"
                    )
                    # Brief pause between features for Ctrl+C opportunity
                    await asyncio.sleep(2)
                else:
                    consecutive_failures += 1
                    self.logger.error(f"Feature #{feature.id} FAILED: {result.error}")
                    if consecutive_failures >= max_consecutive_failures:
                        self.logger.error(
                            f"{max_consecutive_failures} consecutive failures. "
                            f"Pausing for human review."
                        )
                        action = await self._ask_consecutive_failures(feature)
                        if action == "continue":
                            consecutive_failures = 0
                        else:
                            break

        except (KeyboardInterrupt, asyncio.CancelledError):
            self.logger.info("Interrupted by user")
        finally:
            self._cleanup()

        # Final summary
        self.logger.info("=" * 60)
        self.logger.info("Orchestration complete")
        self.logger.info(self.state.get_progress_summary())
        self.logger.info("=" * 60)

    def _handle_shutdown_signal(self, sig: signal.Signals) -> None:
        """Handle SIGINT/SIGTERM: kill child processes and request shutdown."""
        sig_name = sig.name
        if self._shutdown_requested:
            # Second signal — force exit immediately
            self.logger.warning(f"Second {sig_name} received — force exiting")
            FeatureRunner.kill_active_subprocess()
            raise SystemExit(1)

        self._shutdown_requested = True
        self.logger.info(f"\n{sig_name} received — shutting down gracefully...")
        self.logger.info("  (press Ctrl-C again to force-quit)")
        FeatureRunner.kill_active_subprocess()

    def _cleanup(self) -> None:
        """Final cleanup: kill any lingering subprocesses."""
        FeatureRunner.kill_active_subprocess()

    async def _execute_with_retry(self, feature: Feature) -> FeatureResult:
        """Execute a feature with exponential backoff retry."""
        last_result: FeatureResult | None = None
        retries_remaining = max(0, self.config.max_retries - feature.attempts)

        for attempt in range(retries_remaining + 1):
            if attempt > 0:
                backoff = min(
                    self.config.retry_backoff_base ** attempt,
                    self.config.retry_backoff_max,
                )
                self.logger.info(
                    f"Retry {attempt}/{retries_remaining} for feature #{feature.id} "
                    f"(backoff: {backoff:.1f}s)"
                )
                await asyncio.sleep(backoff)

            result = await self.runner.run_feature(feature)
            result.retries_used = attempt

            if result.success:
                return result

            last_result = result
            self.logger.warning(f"Attempt {attempt + 1} failed: {result.error}")

            if not self._is_retriable_error(result.error):
                self.logger.error("Non-retriable error. Stopping retries.")
                break

        return last_result or FeatureResult(
            feature_id=feature.id,
            success=False,
            error="Exhausted all retries",
        )

    @staticmethod
    def _is_retriable_error(error: str | None) -> bool:
        """Determine if an error warrants a retry."""
        if not error:
            return True
        non_retriable_keywords = [
            "user denied",
            "user chose to abort",
            "permission denied",
            "eacces",
            "authentication required",
        ]
        error_lower = error.lower()
        return not any(kw in error_lower for kw in non_retriable_keywords)

    async def _ask_retry_exhausted(self, feature: Feature) -> str:
        """Ask user what to do when retries are exhausted."""
        print(f"\nFeature #{feature.id} ({feature.name}) failed {feature.attempts} times.")
        print(f"Last error: {feature.last_error}")
        print("Options: [s]kip  [r]etry (reset counter)  [a]bort")
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: input("Choice: "))
        r = response.strip().lower()
        if r.startswith("s"):
            return "skip"
        elif r.startswith("r"):
            return "retry"
        return "abort"

    async def _ask_consecutive_failures(self, feature: Feature) -> str:
        """Ask user whether to continue after consecutive failures."""
        print(f"\nMultiple consecutive failures. Last on feature #{feature.id}.")
        print("Options: [c]ontinue  [a]bort")
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: input("Choice: "))
        if response.strip().lower().startswith("c"):
            return "continue"
        return "abort"

    def _print_feature_header(
        self, feature: Feature, all_features: list[Feature],
    ) -> None:
        total = len(all_features)
        completed = sum(1 for f in all_features if f.passes)
        attempt_str = f" (attempt {feature.attempts + 1})" if feature.attempts > 0 else ""
        print()
        print("=" * 60)
        print(f"Feature #{feature.id} / {total}: {feature.name}{attempt_str}")
        print(f"Progress: {completed} / {total} complete")
        print("=" * 60)

    def _dry_run(self, features: list[Feature]) -> None:
        start_from = self.config.start_from_feature or 1
        for f in features:
            if not f.passes and f.id >= start_from:
                print(f"[dry-run] Would run: Feature #{f.id} -- {f.name}")
