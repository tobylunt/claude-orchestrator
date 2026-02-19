"""Prompt templates for worker sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import OrchestratorConfig
    from .models import Feature

FEATURE_PROMPT_TEMPLATE = """\
You are implementing a single feature for this project. Follow these instructions precisely.

## Your Task

Implement Feature #{feature_id}: {feature_name}

### Implementation Steps
{steps_text}

## Protocol

1. **Orientation**: Run `pwd` to confirm working directory. Check `git log --oneline -5` for recent context.{init_script_instruction}
2. **Read project state**: Read the progress file at `{progress_file}` for context on recent work.
3. **Implement**: Work through the implementation steps above. Use the project's existing patterns and conventions.
4. **Verify**: Verify the feature works as expected. Run the project's build command to confirm no errors.{mcp_verification_instruction}
5. **Commit**: Create a git commit with message: `{commit_prefix}implement feature #{feature_id} -- {feature_name}`
6. **STOP**: Print a short completion summary. Do NOT continue to the next feature. One feature per session.

## If You Get Stuck

- If you encounter a bug that breaks existing functionality, revert your changes and try a different approach.
- If you need human input, use AskUserQuestion to ask specific questions with clear options.
- If you cannot complete the feature after reasonable effort, explain what went wrong and stop.

## Important Rules

- Work on exactly ONE feature, then stop.
- Never remove or edit feature descriptions -- only implement them.
- After verifying, commit your work.
- Stop after committing. Do not proceed to the next feature.
"""


def build_feature_prompt(feature: Feature, config: OrchestratorConfig) -> str:
    """Build the full prompt for a feature worker session."""
    steps_text = "\n".join(
        f"  {i + 1}. {step}" for i, step in enumerate(feature.steps)
    )

    init_instruction = ""
    if config.init_script:
        init_instruction = (
            f"\n   Run `./{config.init_script}` to start any required services "
            f"(dev server, etc.)."
        )

    mcp_instruction = ""
    if "playwright" in config.mcp_servers:
        mcp_instruction = (
            "\n   Use Playwright MCP to navigate to the project and "
            "visually verify rendering in both dark and light themes."
        )

    return FEATURE_PROMPT_TEMPLATE.format(
        feature_id=feature.id,
        feature_name=feature.name,
        steps_text=steps_text,
        init_script_instruction=init_instruction,
        progress_file=config.progress_file,
        mcp_verification_instruction=mcp_instruction,
        commit_prefix=config.commit_prefix,
    )
