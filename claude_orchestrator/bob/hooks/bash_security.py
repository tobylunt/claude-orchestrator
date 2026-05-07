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
# Three layers:
#   1. BLOCKED_SUBSTRINGS — fast "in" check for obvious patterns
#   2. BLOCKED_PATTERNS   — regex for nuanced matching
#   3. _check_rm_recursive — allowlist-based: blocks ALL recursive rm
#      except a small set of known-safe build artifact directories
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

# ---------------------------------------------------------------------------
# Recursive rm: allowlist approach
#
# ALL `rm -r` / `rm -rf` / `rm -fr` etc. are blocked by default.
# Only the specific directory basenames below are permitted.
# Add entries here as needed for your project's build artifacts.
# ---------------------------------------------------------------------------

# Detects rm with a recursive flag: -r, -rf, -fr, -Rf, etc.
_RM_RECURSIVE_RE = re.compile(r"\brm\s+.*-[a-zA-Z]*[rR]")

# Basenames (not paths) that are safe to recursively delete.
# Matched against each argument after the flags.
RM_RECURSIVE_ALLOWLIST: set[str] = {
    # JS/Node build artifacts
    "node_modules",
    "dist",
    "build",
    ".cache",
    ".astro",
    ".next",
    ".nuxt",
    ".turbo",
    ".parcel-cache",
    ".output",
    ".vercel",
    # Test / coverage
    "coverage",
    ".nyc_output",
    # Python
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    # Misc
    "tmp",
    ".tmp",
}


def _check_rm_recursive(command: str) -> str | None:
    """Block all recursive rm unless every target is in the allowlist.

    Parses the arguments after `rm` and its flags, strips leading `./`
    and trailing `/`, then checks each basename against RM_RECURSIVE_ALLOWLIST.
    """
    if not _RM_RECURSIVE_RE.search(command):
        return None

    # Extract everything after "rm" and its flags
    # Split the command into tokens (simple split, not shell-aware)
    tokens = command.split()
    try:
        rm_idx = tokens.index("rm")
    except ValueError:
        # rm might be at a different position in a pipe chain;
        # conservative: block it
        return "recursive rm (could not parse targets)"

    # Collect targets: everything after rm that isn't a flag
    targets: list[str] = []
    for token in tokens[rm_idx + 1:]:
        if token.startswith("-"):
            continue
        # Normalize: strip leading "./" prefix and trailing "/"
        cleaned = token
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.rstrip("/")
        if not cleaned or cleaned in (".", ".."):
            # Bare ".", "..", "/" — definitely block
            return "recursive rm targeting root or current directory"
        targets.append(cleaned)

    if not targets:
        return "recursive rm with no identifiable target"

    for target in targets:
        # Extract the basename (last path component)
        basename = target.rsplit("/", 1)[-1]
        if basename not in RM_RECURSIVE_ALLOWLIST:
            return f"recursive rm — '{target}' not in allowlist"

    return None


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

    # Allowlist-based recursive rm check (runs last since it's more expensive)
    rm_reason = _check_rm_recursive(command)
    if rm_reason:
        return rm_reason

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
