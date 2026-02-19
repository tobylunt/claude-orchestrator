"""Hook callbacks for monitoring and controlling worker sessions."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("orchestrator")


class OrchestratorHooks:
    """Hook callbacks for security, activity tracking, and logging."""

    def __init__(self, stall_timeout: float = 300.0):
        self.stall_timeout = stall_timeout
        self._last_tool_time = time.monotonic()
        self._tool_count = 0
        self._tool_log: list[dict[str, Any]] = []

    # --- Required dummy hook for Python SDK can_use_tool workaround ---

    async def keepalive_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Required PreToolUse hook to keep the stream open for can_use_tool.

        The Python SDK requires at least one PreToolUse hook returning
        {"continue_": True} for the can_use_tool callback to function.
        """
        return {"continue_": True}

    # --- Security: block dangerous commands ---

    async def security_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Block destructive git/system commands."""
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        if input_data.get("tool_name") != "Bash":
            return {}

        command = input_data.get("tool_input", {}).get("command", "")
        dangerous_patterns = [
            "rm -rf /",
            "git push --force",
            "git push -f ",
            "git reset --hard",
            "git clean -fd",
            "> /dev/",
            "mkfs.",
            ":(){:|:&};:",
            "dd if=/dev/",
        ]
        for pattern in dangerous_patterns:
            if pattern in command:
                logger.warning(f"Blocked dangerous command: {command}")
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Blocked by orchestrator security policy: "
                            f"pattern '{pattern}' detected"
                        ),
                    }
                }
        return {}

    # --- Activity tracking for stall detection ---

    async def activity_tracker(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Track tool activity timestamps for stall detection."""
        self._last_tool_time = time.monotonic()
        self._tool_count += 1

        tool_name = input_data.get("tool_name", "unknown")
        self._tool_log.append({
            "tool": tool_name,
            "time": self._last_tool_time,
        })
        logger.debug(f"  Hook: tool #{self._tool_count}: {tool_name}")
        return {}

    # --- Post-tool logging ---

    async def post_tool_logger(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Log tool results, highlighting errors."""
        tool_name = input_data.get("tool_name", "unknown")
        response = input_data.get("tool_response", "")
        if isinstance(response, dict) and response.get("is_error"):
            logger.warning(f"Tool {tool_name} error: {str(response)[:500]}")
        return {}

    # --- Stop hook ---

    async def stop_hook(
        self,
        input_data: dict[str, Any],
        tool_use_id: str | None,
        context: Any,
    ) -> dict[str, Any]:
        """Log session completion stats."""
        logger.info(f"Session stopping. Tools used: {self._tool_count}")
        return {}

    # --- Stall detection properties ---

    @property
    def seconds_since_last_activity(self) -> float:
        return time.monotonic() - self._last_tool_time

    @property
    def is_stalled(self) -> bool:
        return self.seconds_since_last_activity > self.stall_timeout

    def reset(self) -> None:
        """Reset counters for a new feature session."""
        self._last_tool_time = time.monotonic()
        self._tool_count = 0
        self._tool_log.clear()
