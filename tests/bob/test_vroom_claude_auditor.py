"""Tests for the real Claude architect auditor."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.vroom.auditors.claude_architect import (
    ClaudeArchitectAuditor,
    AnthropicClient,
)
from claude_orchestrator.models import Finding


class FakeAnthropicClient:
    def __init__(self, response_json: str):
        self.response_json = response_json
        self.calls = 0

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str:
        self.calls += 1
        return self.response_json


def test_claude_auditor_returns_empty_when_no_findings(tmp_path: Path):
    client = FakeAnthropicClient(response_json='{"findings": []}')
    auditor = ClaudeArchitectAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert findings == []
    assert client.calls == 1


def test_claude_auditor_parses_structured_findings(tmp_path: Path):
    client = FakeAnthropicClient(response_json='''
    {
      "findings": [
        {
          "rule_id": "claude.tight_coupling",
          "severity": "medium",
          "uri": "src/orders.py",
          "start_line": 42,
          "end_line": 50,
          "message": "OrderProcessor directly imports DatabaseClient; consider DI."
        }
      ]
    }
    ''')
    auditor = ClaudeArchitectAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "claude.tight_coupling"
    assert f.severity == "medium"
    assert f.location.uri == "src/orders.py"
    assert f.location.start_line == 42
    assert f.auditor == "claude_architect"
    assert f.fingerprint  # non-empty


def test_claude_auditor_handles_malformed_response(tmp_path: Path):
    """Garbled JSON should produce no findings, not crash."""
    client = FakeAnthropicClient(response_json="this is not JSON at all")
    auditor = ClaudeArchitectAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert findings == []


def test_claude_auditor_handles_json_with_markdown_fence(tmp_path: Path):
    """Anthropic often wraps JSON in ```json ... ``` fences. Parser should strip them."""
    client = FakeAnthropicClient(response_json='''
    Here is my analysis:

    ```json
    {"findings": [{"rule_id": "claude.naming", "severity": "low",
      "uri": "x.py", "start_line": 1, "message": "rename foo"}]}
    ```
    ''')
    auditor = ClaudeArchitectAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert len(findings) == 1
    assert findings[0].rule_id == "claude.naming"


def test_claude_auditor_id_and_triggers_on(tmp_path: Path):
    client = FakeAnthropicClient(response_json='{"findings": []}')
    auditor = ClaudeArchitectAuditor(client=client)
    assert auditor.id == "claude_architect"
    assert auditor.triggers_on([]) is True
