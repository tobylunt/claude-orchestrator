"""Feature execution engine: run a single feature via ClaudeSDKClient."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from typing import TYPE_CHECKING

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from .hooks import OrchestratorHooks
from .human_input import HumanInputHandler
from .models import Feature, FeatureResult
from .prompts import build_feature_prompt

if TYPE_CHECKING:
    from .config import OrchestratorConfig

logger = logging.getLogger("orchestrator")


class FeatureRunner:
    """Executes a single feature using the Claude Agent SDK."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config

    async def run_feature(self, feature: Feature) -> FeatureResult:
        """Execute a feature with stall detection."""
        start_time = time.monotonic()

        # Initialize hooks and human input handler
        hooks = OrchestratorHooks(stall_timeout=self.config.stall_timeout_seconds)
        human_handler = HumanInputHandler(
            input_timeout=self.config.human_input_timeout_seconds,
        )

        # Build the prompt
        prompt = build_feature_prompt(feature=feature, config=self.config)

        # Build SDK options
        options = ClaudeAgentOptions(
            model=self.config.model,
            permission_mode=self.config.permission_mode,
            allowed_tools=self.config.allowed_tools,
            disallowed_tools=self.config.disallowed_tools,
            cwd=str(self.config.project_dir),
            max_turns=self.config.max_turns_per_feature,
            max_budget_usd=self.config.max_budget_per_feature_usd,
            can_use_tool=human_handler.can_use_tool,
            setting_sources=["project"],
            mcp_servers=self.config.mcp_servers,
            hooks={
                "PreToolUse": [
                    # Keepalive must come first (Python SDK requirement)
                    HookMatcher(matcher=None, hooks=[hooks.keepalive_hook]),
                    # Security on Bash commands
                    HookMatcher(matcher="Bash", hooks=[hooks.security_hook]),
                    # Activity tracking on all tools
                    HookMatcher(matcher=None, hooks=[hooks.activity_tracker]),
                ],
                "PostToolUse": [
                    HookMatcher(matcher=None, hooks=[hooks.post_tool_logger]),
                ],
                "Stop": [
                    HookMatcher(hooks=[hooks.stop_hook]),
                ],
            },
        )

        # Execute
        session_id: str | None = None
        cost_usd: float | None = None
        is_error = False
        error_msg: str | None = None

        try:
            async with ClaudeSDKClient(options) as client:
                await client.query(prompt)

                # Launch stall detector in background
                stall_task = asyncio.create_task(
                    self._stall_detector(hooks, client, feature.id)
                )

                try:
                    async for message in client.receive_messages():
                        # Capture session_id from init message
                        if isinstance(message, SystemMessage):
                            if message.subtype == "init":
                                session_id = message.data.get("session_id")

                        # Log assistant text
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    logger.debug(f"Claude: {block.text[:200]}")

                        # Capture final result
                        if isinstance(message, ResultMessage):
                            is_error = message.is_error
                            cost_usd = message.total_cost_usd
                            session_id = session_id or message.session_id
                            if is_error:
                                error_msg = message.result
                            break
                finally:
                    stall_task.cancel()
                    try:
                        await stall_task
                    except asyncio.CancelledError:
                        pass

        except Exception as e:
            is_error = True
            error_msg = f"{type(e).__name__}: {str(e)}"
            logger.error(f"Feature #{feature.id} crashed: {error_msg}")

        # Check git for commit hash
        commit_hash = None
        if not is_error:
            commit_hash = self._get_latest_commit_hash()

        duration = time.monotonic() - start_time

        return FeatureResult(
            feature_id=feature.id,
            success=not is_error,
            error=error_msg,
            session_id=session_id,
            commit_hash=commit_hash,
            duration_seconds=duration,
            cost_usd=cost_usd,
        )

    async def _stall_detector(
        self,
        hooks: OrchestratorHooks,
        client: ClaudeSDKClient,
        feature_id: int,
    ) -> None:
        """Background task that interrupts the client if stalled."""
        while True:
            await asyncio.sleep(30)
            if hooks.is_stalled:
                elapsed = hooks.seconds_since_last_activity
                logger.warning(
                    f"Feature #{feature_id}: stall detected "
                    f"({elapsed:.0f}s since last tool). Interrupting."
                )
                await client.interrupt()
                return

    def _get_latest_commit_hash(self) -> str | None:
        """Get the latest commit hash from the project."""
        try:
            result = subprocess.run(
                ["git", "log", "--format=%H", "-1"],
                cwd=self.config.project_dir,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return result.stdout.strip()[:12]
        except Exception:
            pass
        return None
