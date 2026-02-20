"""Hook callbacks for monitoring and controlling worker sessions."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

logger = logging.getLogger("orchestrator")

# ---------------------------------------------------------------------------
# Bash command security rules
#
# Two tiers:
#   BLOCKED_SUBSTRINGS — simple "in" check, fast and readable
#   BLOCKED_PATTERNS   — regex for cases where substring matching is too
#                        broad or too narrow
#
# Organized by threat category. Each entry is (pattern, reason).
# ---------------------------------------------------------------------------

BLOCKED_SUBSTRINGS: list[tuple[str, str]] = [
    # --- Destructive system commands ---
    ("mkfs.", "filesystem format"),
    (":(){:|:&};:", "fork bomb"),
    ("dd if=/dev/", "raw disk write"),
    ("> /dev/sd", "raw device write"),

    # --- Git: operations visible to others / hard to reverse ---
    ("git push", "push to remote (use manually outside orchestrator)"),
    ("git reset --hard", "destructive history reset"),
    ("git clean -f", "force-delete untracked files"),
    ("git checkout .", "discard all working tree changes"),
    ("git restore .", "discard all working tree changes"),
    ("git branch -D", "force-delete branch"),
    ("git rebase", "rebase (risky in automated context)"),
    ("git stash drop", "drop stash entry"),
    ("git stash clear", "drop all stash entries"),

    # --- Package publishing ---
    ("npm publish", "publish package to npm"),
    ("npx npm publish", "publish package to npm"),
    ("yarn publish", "publish package to yarn"),
    ("pnpm publish", "publish package to pnpm"),
    ("twine upload", "publish package to PyPI"),
    ("cargo publish", "publish crate"),
    ("gem push", "publish gem"),

    # --- System administration ---
    ("shutdown", "system shutdown"),
    ("reboot", "system reboot"),
    ("systemctl stop", "stop system service"),
    ("systemctl disable", "disable system service"),
    ("launchctl unload", "unload macOS service"),

    # --- Credential / secret exposure ---
    ("--password", "password in command line"),
    ("--token", "token in command line"),
]

BLOCKED_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # --- Destructive rm: block rm -rf targeting broad paths ---
    # Blocks: rm -rf /, rm -rf ~, rm -rf ~/, rm -rf ., rm -rf .., rm -rf $HOME
    # Allows: rm -rf node_modules, rm -rf dist/, rm -rf ./dist, rm -rf .cache
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)*(-[a-zA-Z]*f[a-zA-Z]*\s+)*"
            r"(/\s*$|/\s+|~/?(\s|$)|\.\.\s*$|\.\.\s+|\.\s*$|\.\s+|\$HOME\b)"
        ),
        "recursive delete targeting root, home, or current directory",
    ),
    # Also catch the -fr variant
    (
        re.compile(
            r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*\s+)*"
            r"(/\s*$|/\s+|~/?(\s|$)|\.\.\s*$|\.\.\s+|\.\s*$|\.\s+|\$HOME\b)"
        ),
        "recursive delete targeting root, home, or current directory",
    ),

    # --- Arbitrary code execution from internet ---
    (
        re.compile(r"\bcurl\b.*\|\s*(sh|bash|zsh|python|node)\b"),
        "piping curl output to interpreter",
    ),
    (
        re.compile(r"\bwget\b.*\|\s*(sh|bash|zsh|python|node)\b"),
        "piping wget output to interpreter",
    ),
    (
        re.compile(r"\beval\b.*\$\(\s*(curl|wget)\b"),
        "eval of downloaded content",
    ),

    # --- sudo (shouldn't be needed for web dev) ---
    (
        re.compile(r"\bsudo\b"),
        "sudo (not needed for project builds)",
    ),

    # --- chmod making files world-writable ---
    (
        re.compile(r"\bchmod\s+([0-7]*7[0-7]{0,2}|a\+w|o\+w|\+w)"),
        "chmod world-writable",
    ),

    # --- Docker / k8s destructive operations ---
    (
        re.compile(r"\bdocker\s+(rm|rmi|system\s+prune)"),
        "docker destructive operation",
    ),
    (
        re.compile(r"\bkubectl\s+delete\b"),
        "kubectl delete",
    ),

    # --- Database destructive (in case of embedded sqlite, etc.) ---
    (
        re.compile(r"\b(DROP\s+(TABLE|DATABASE|SCHEMA)|TRUNCATE\s+TABLE)\b", re.IGNORECASE),
        "destructive SQL operation",
    ),
]


def check_command_safety(command: str) -> str | None:
    """Check a Bash command against security rules.

    Returns None if the command is safe, or a reason string if blocked.
    """
    for pattern, reason in BLOCKED_SUBSTRINGS:
        if pattern in command:
            return reason

    for regex, reason in BLOCKED_PATTERNS:
        if regex.search(command):
            return reason

    return None


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
        """Block destructive Bash commands.

        Checks against BLOCKED_SUBSTRINGS (fast substring match) and
        BLOCKED_PATTERNS (regex) covering: filesystem destruction, git push,
        arbitrary code execution, package publishing, sudo, and more.
        """
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        if input_data.get("tool_name") != "Bash":
            return {}

        command = input_data.get("tool_input", {}).get("command", "")
        reason = check_command_safety(command)
        if reason:
            logger.warning(f"BLOCKED: {reason} — {command}")
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"Blocked by orchestrator security policy: {reason}"
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
