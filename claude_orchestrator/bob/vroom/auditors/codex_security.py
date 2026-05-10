"""Real OpenAI-backed security/edge-case auditor for Vroom.

Asks OpenAI to scan the workspace for security issues, edge cases, and
exploitability concerns. Returns Finding objects.

Lazy-imports the OpenAI SDK so import-time doesn't require credentials
or the package being installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol

from claude_orchestrator.models import Finding, SARIFLocation


class OpenAIClient(Protocol):
    """Anything that can take a workspace and return a JSON string."""

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str: ...


class _ProductionOpenAIClient:
    """Real OpenAI API call. Lazy-built when no client is injected."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model or os.environ.get(
            "BOB_VROOM_CODEX_MODEL", "gpt-5.4"
        )

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str:
        try:
            from openai import OpenAI
        except ImportError:
            return '{"findings": []}'

        client = OpenAI()

        files = sorted(p for p in workspace.rglob("*") if p.is_file()
                       and ".bob" not in p.parts and ".git" not in p.parts)[:100]
        file_list = "\n".join(str(f.relative_to(workspace)) for f in files)

        # Pick up to 5 likely-security-relevant files for inline preview.
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

        system = (
            "You are a security auditor. Find issues like:\n"
            "- SQL injection / command injection / unsafe deserialization\n"
            "- XSS / CSRF / authn-authz bypasses\n"
            "- Secrets in code or weak credentials handling\n"
            "- Unsafe file handling, path traversal, unchecked input bounds\n"
            "- Logic edge cases that break under unusual inputs\n"
            "Be selective — surface real risks, not theoretical ones."
        )

        user = (
            f"## Files\n{file_list}\n\n"
            "## Source previews\n" + "\n\n".join(previews) + "\n\n"
            "Respond with JSON only:\n"
            '{"findings": [{"rule_id": "codex.<short-id>", "severity": '
            '"info|low|medium|high|critical", "uri": "<path>", '
            '"start_line": <int>, "end_line": <int|null>, '
            '"message": "<concise>"}]}\n'
            "Empty findings list is valid."
        )

        response = client.chat.completions.create(
            model=self.model,
            # GPT-5+ requires max_completion_tokens; older models also accept it.
            max_completion_tokens=4000,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or '{"findings": []}'


class CodexSecurityAuditor:
    """Vroom auditor for security / edge-case issues, backed by OpenAI API."""

    id = "codex_security"

    def __init__(self, *, client: OpenAIClient | None = None) -> None:
        self._client = client

    def triggers_on(self, changed_files: list[Path]) -> bool:
        return True

    def audit(self, workspace: Path, changed_files: list[Path]) -> list[Finding]:
        client = self._client or _ProductionOpenAIClient()
        try:
            text = client.audit_workspace(workspace, changed_files)
        except Exception:
            return []
        return _parse_findings(text)


def _parse_findings(text: str) -> list[Finding]:
    """Best-effort JSON extraction. Strips markdown fences, returns [] on error."""
    text = text.strip()

    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

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
            rule_id = str(raw.get("rule_id", "codex.unknown"))
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
                auditor="codex_security",
                fingerprint=fingerprint,
                status="open",
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return findings
