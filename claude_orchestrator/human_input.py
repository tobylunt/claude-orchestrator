"""Human-in-the-loop: AskUserQuestion handler and tool approval UI."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

logger = logging.getLogger("orchestrator")


class HumanInputHandler:
    """Handles AskUserQuestion and tool approval with timeout escalation.

    By default, auto-approves all standard Claude Code tools and MCP tools.
    The security_hook in hooks.py handles blocking dangerous commands.
    Only truly unknown tools or explicitly denied tools require human approval.
    """

    # Tools that are always safe to auto-approve
    STANDARD_TOOLS = {
        "Read", "Glob", "Grep", "WebSearch", "WebFetch",
        "Write", "Edit", "Bash", "Task", "TodoWrite", "TodoRead",
        "NotebookEdit", "Skill",
    }

    def __init__(
        self,
        input_timeout: float = 120.0,
        auto_approve_tools: set[str] | None = None,
        auto_deny_tools: set[str] | None = None,
        prompt_unknown_tools: bool = False,
    ):
        self.input_timeout = input_timeout
        self.auto_approve_tools = auto_approve_tools or self.STANDARD_TOOLS
        self.auto_deny_tools = auto_deny_tools or set()
        self.prompt_unknown_tools = prompt_unknown_tools

    async def can_use_tool(
        self,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Main callback for the SDK's can_use_tool parameter.

        Auto-approves:
        - AskUserQuestion (routed to terminal UI)
        - All standard Claude Code tools (security handled by hooks)
        - All MCP tools (prefixed with "mcp__")

        Only prompts for unknown tools if prompt_unknown_tools is True.
        """
        if tool_name == "AskUserQuestion":
            return await self._handle_ask_user_question(input_data)

        if tool_name in self.auto_deny_tools:
            return PermissionResultDeny(
                message=f"Tool {tool_name} is not permitted by orchestrator policy"
            )

        if tool_name in self.auto_approve_tools:
            return PermissionResultAllow(updated_input=input_data)

        # MCP tools (e.g. mcp__playwright__browser_navigate) — auto-approve
        if tool_name.startswith("mcp__"):
            return PermissionResultAllow(updated_input=input_data)

        # Unknown tool — either auto-approve or prompt based on config
        if not self.prompt_unknown_tools:
            logger.debug(f"Auto-approving unknown tool: {tool_name}")
            return PermissionResultAllow(updated_input=input_data)

        return await self._prompt_tool_approval(tool_name, input_data)

    async def _handle_ask_user_question(
        self, input_data: dict[str, Any],
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Surface Claude's clarifying questions to the terminal."""
        questions = input_data.get("questions", [])
        answers: dict[str, str] = {}

        print("\n" + "=" * 60)
        print("  CLAUDE NEEDS YOUR INPUT")
        print("=" * 60)

        for q in questions:
            print(f"\n[{q.get('header', '?')}] {q['question']}")
            options = q.get("options", [])
            for i, opt in enumerate(options):
                desc = opt.get("description", "")
                print(f"  {i + 1}. {opt['label']}" + (f" -- {desc}" if desc else ""))
            if q.get("multiSelect"):
                print("  (Enter numbers separated by commas, or type a custom answer)")
            else:
                print("  (Enter a number, or type a custom answer)")

            try:
                response = await asyncio.wait_for(
                    _async_input("  Your choice: "),
                    timeout=self.input_timeout,
                )
                answers[q["question"]] = _parse_response(response, options)
            except asyncio.TimeoutError:
                print(f"\n  [TIMEOUT] No response after {self.input_timeout:.0f}s.")
                if options:
                    default = options[0]["label"]
                    print(f"  Using default: {default}")
                    answers[q["question"]] = default
                else:
                    answers[q["question"]] = "No response (timeout)"

        print("=" * 60 + "\n")

        return PermissionResultAllow(
            updated_input={"questions": questions, "answers": answers}
        )

    async def _prompt_tool_approval(
        self, tool_name: str, input_data: dict[str, Any],
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Prompt for tool approval with timeout auto-deny."""
        print(f"\n--- Tool Approval Required ---")
        print(f"  Tool: {tool_name}")
        if tool_name == "Bash":
            print(f"  Command: {input_data.get('command', '???')}")
            if input_data.get("description"):
                print(f"  Description: {input_data['description']}")
        elif tool_name in ("Write", "Edit"):
            print(f"  File: {input_data.get('file_path', '???')}")
        else:
            display = str(input_data)[:200]
            print(f"  Input: {display}")

        try:
            response = await asyncio.wait_for(
                _async_input("  Allow? (y/n): "),
                timeout=self.input_timeout,
            )
            if response.strip().lower() in ("y", "yes"):
                return PermissionResultAllow(updated_input=input_data)
            else:
                return PermissionResultDeny(message="User denied this operation")
        except asyncio.TimeoutError:
            print(f"  [TIMEOUT] Auto-denying after {self.input_timeout:.0f}s")
            return PermissionResultDeny(
                message=f"No human response within {self.input_timeout:.0f}s timeout"
            )


async def _async_input(prompt: str) -> str:
    """Non-blocking input that works with asyncio."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


def _parse_response(response: str, options: list[dict[str, Any]]) -> str:
    """Parse numeric selection or free-text input."""
    response = response.strip()
    if not response:
        return options[0]["label"] if options else ""
    try:
        indices = [int(s.strip()) - 1 for s in response.split(",")]
        labels = [options[i]["label"] for i in indices if 0 <= i < len(options)]
        return ", ".join(labels) if labels else response
    except (ValueError, IndexError):
        return response
