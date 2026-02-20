"""Feature execution engine: run a single feature via ClaudeSDKClient."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
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
    ToolUseBlock,
    ToolResultBlock,
)

from .hooks import OrchestratorHooks
from .human_input import HumanInputHandler
from .models import Feature, FeatureResult
from .prompts import build_feature_prompt

if TYPE_CHECKING:
    from .config import OrchestratorConfig

logger = logging.getLogger("orchestrator")


def _get_sdk_subprocess_pid(client: ClaudeSDKClient) -> int | None:
    """Extract the PID of the Claude Code subprocess from the SDK client.

    Navigates: client._transport._process.pid
    Returns None if any attribute is missing (SDK internals changed).
    """
    try:
        transport = getattr(client, "_transport", None)
        if transport is None:
            return None
        proc = getattr(transport, "_process", None)
        if proc is None:
            return None
        return getattr(proc, "pid", None)
    except Exception:
        return None


class FeatureRunner:
    """Executes a single feature using the Claude Agent SDK."""

    # Class-level tracking of the active client for signal-based cleanup
    _active_client: ClaudeSDKClient | None = None
    _active_client_pid: int | None = None

    def __init__(self, config: OrchestratorConfig):
        self.config = config

    async def run_feature(self, feature: Feature) -> FeatureResult:
        """Execute a feature with stall detection and progress streaming."""
        start_time = time.monotonic()

        # Initialize hooks and human input handler
        hooks = OrchestratorHooks(stall_timeout=self.config.stall_timeout_seconds)
        human_handler = HumanInputHandler(
            input_timeout=self.config.human_input_timeout_seconds,
            prompt_unknown_tools=self.config.prompt_unknown_tools,
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
        tool_count = 0

        try:
            async with ClaudeSDKClient(options) as client:
                FeatureRunner._active_client = client

                await client.query(prompt)

                # Capture subprocess PID for cleanup on Ctrl-C.
                # Must be after query() — that's when the subprocess spawns.
                FeatureRunner._active_client_pid = _get_sdk_subprocess_pid(client)

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
                                logger.info(f"  Session started (id: {session_id})")

                        # Stream progress from assistant messages
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    self._log_assistant_text(block.text)
                                elif isinstance(block, ToolUseBlock):
                                    tool_count += 1
                                    self._log_tool_use(block, tool_count)

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
                    FeatureRunner._active_client = None
                    FeatureRunner._active_client_pid = None

        except Exception as e:
            FeatureRunner._active_client = None
            FeatureRunner._active_client_pid = None
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

    # --- Progress streaming helpers ---

    @staticmethod
    def _log_assistant_text(text: str) -> None:
        """Log assistant text, showing the first meaningful line as progress."""
        # Show the first non-empty line, trimmed to 120 chars
        for line in text.split("\n"):
            line = line.strip()
            if line:
                if len(line) > 120:
                    line = line[:117] + "..."
                logger.info(f"  Claude: {line}")
                # Only log additional lines at debug to avoid flooding
                break
        logger.debug(f"  [full text] {text[:500]}")

    @staticmethod
    def _log_tool_use(block: ToolUseBlock, count: int) -> None:
        """Log tool use with a concise summary."""
        name = block.name
        inp = block.input
        detail = ""
        if name == "Read":
            detail = f" {inp.get('file_path', '')}"
        elif name == "Edit":
            path = inp.get("file_path", "")
            detail = f" {path}"
        elif name == "Write":
            detail = f" {inp.get('file_path', '')}"
        elif name == "Bash":
            cmd = inp.get("command", "")
            if len(cmd) > 80:
                cmd = cmd[:77] + "..."
            detail = f" $ {cmd}"
        elif name == "Glob":
            detail = f" {inp.get('pattern', '')}"
        elif name == "Grep":
            detail = f" /{inp.get('pattern', '')}/"
        elif name.startswith("mcp__playwright"):
            short = name.replace("mcp__playwright__", "pw:")
            detail = f" ({short})"
            name = "Playwright"
        elif name == "Task":
            detail = f" [{inp.get('subagent_type', '')}]"

        logger.info(f"  [{count:3d}] {name}{detail}")

    @classmethod
    def kill_active_subprocess(cls) -> None:
        """Kill the active Claude Code subprocess, if any.

        Called during signal handling to prevent orphaned processes.
        Uses SIGTERM first, then SIGKILL after a brief wait.
        Also kills the entire process group to catch child processes
        (e.g., dev servers spawned by the worker).
        """
        pid = cls._active_client_pid
        cls._active_client = None
        cls._active_client_pid = None

        if pid is None:
            return

        logger.info(f"  Terminating Claude Code subprocess (PID {pid})...")
        try:
            # Kill the process group (catches child processes like dev servers)
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            # Process already gone, or not a group leader — try direct kill
            try:
                os.kill(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

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
