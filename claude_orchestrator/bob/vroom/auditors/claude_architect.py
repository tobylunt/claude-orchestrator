"""Real Anthropic-backed architect auditor for Vroom.

Asks Claude to scan the workspace for architecture / design / coupling issues
and return findings as structured JSON. Parses the response into Finding objects.

Lazy-imports the Anthropic SDK so import-time doesn't require credentials or
the package being installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import Finding, SARIFLocation


class AnthropicClient(Protocol):
    """Anything that can take a workspace and return a JSON string."""

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str: ...


class _ProductionAnthropicClient:
    """Real Anthropic API call. Built lazily by ClaudeArchitectAuditor when no
    client is injected.
    """

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get(
            "BOB_VROOM_CLAUDE_MODEL", "claude-sonnet-4-6"
        )

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str:
        from anthropic import Anthropic
        client = Anthropic()

        # Build a small workspace summary: list files (cap 100), include first
        # 200 lines of a few interesting ones.
        files = sorted(p for p in workspace.rglob("*") if p.is_file()
                       and ".bob" not in p.parts and ".git" not in p.parts)[:100]
        file_list = "\n".join(str(f.relative_to(workspace)) for f in files)

        # Pick up to 5 source files for inline preview.
        candidates = [
            f for f in files
            if f.suffix in {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs"}
        ][:5]
        previews = []
        for f in candidates:
            try:
                lines = f.read_text().splitlines()[:200]
                previews.append(
                    f"--- {f.relative_to(workspace)} ---\n" + "\n".join(lines)
                )
            except Exception:
                continue

        prompt = (
            "You are an architecture/design auditor. Review the workspace below for:\n"
            "- Tight coupling / missing dependency injection\n"
            "- Overly complex abstractions or premature generalization\n"
            "- Single-responsibility violations\n"
            "- Inconsistent module boundaries\n"
            "- Architectural anti-patterns specific to the language/framework\n\n"
            f"## Files\n{file_list}\n\n"
            "## Source previews\n" + "\n\n".join(previews) + "\n\n"
            "Respond with JSON only:\n"
            "{\"findings\": [{\"rule_id\": \"claude.<short-id>\", \"severity\": "
            "\"info|low|medium|high|critical\", \"uri\": \"<path>\", "
            "\"start_line\": <int>, \"end_line\": <int|null>, "
            "\"message\": \"<concise>\"}]}\n"
            "Empty findings list is valid. Be selective — surface real issues, "
            "not style nits."
        )

        response = client.messages.create(
            model=self.model,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )

        usage = getattr(response, "usage", None)
        if usage is not None:
            from claude_orchestrator.bob.cost_tracker import record_call_in_context
            record_call_in_context(
                provider="anthropic",
                model=self.model,
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
                phase="vroom",
            )

        text = "".join(b.text for b in response.content if hasattr(b, "text"))
        return text


class ClaudeArchitectAuditor:
    """Vroom auditor for architecture / design issues, backed by Anthropic API."""

    id = "claude_architect"

    def __init__(self, *, client: AnthropicClient | None = None) -> None:
        self._client = client  # injected for tests; lazily constructed otherwise

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True  # always run; M5 can add file-pattern gates

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        client = self._client or _ProductionAnthropicClient()
        try:
            text = client.audit_workspace(workspace, changed_files)
        except Exception:
            return []  # fail-safe: never let an auditor crash the pool

        return _parse_findings(text)


def _parse_findings(text: str) -> list[Finding]:
    """Best-effort JSON extraction. Strips markdown fences, returns [] on error."""
    text = text.strip()

    # Strip ```json ... ``` fences if present.
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    # Find the first {...} block in the response.
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if not obj_match:
        return []
    try:
        payload = json.loads(obj_match.group(0))
    except json.JSONDecodeError:
        return []

    findings: list[Finding] = []
    for raw in payload.get("findings", []):
        try:
            uri = str(raw["uri"])
            start_line = int(raw["start_line"])
            end_line = raw.get("end_line")
            if end_line is not None:
                end_line = int(end_line)
            severity = raw.get("severity", "info")
            if severity not in {"info", "low", "medium", "high", "critical"}:
                severity = "info"
            rule_id = str(raw.get("rule_id", "claude.unknown"))
            message = str(raw.get("message", ""))[:500]
            fingerprint = hashlib.sha1(
                f"{rule_id}|{uri}|{start_line}|{message[:100]}".encode()
            ).hexdigest()[:16]
            findings.append(Finding(
                rule_id=rule_id,
                severity=severity,  # type: ignore[arg-type]
                location=SARIFLocation(uri=uri, start_line=start_line, end_line=end_line),
                message=message,
                proposed_fix=None,
                auditor="claude_architect",
                fingerprint=fingerprint,
                status="open",
            ))
        except (KeyError, ValueError, TypeError):
            continue  # skip malformed entries
    return findings
