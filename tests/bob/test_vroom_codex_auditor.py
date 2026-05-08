"""Tests for the real Codex security auditor."""
from pathlib import Path

import pytest

from claude_orchestrator.bob.vroom.auditors.codex_security import (
    CodexSecurityAuditor,
    OpenAIClient,
)


class FakeOpenAIClient:
    def __init__(self, response_json: str):
        self.response_json = response_json
        self.calls = 0

    def audit_workspace(self, workspace: Path, changed_files: list[Path]) -> str:
        self.calls += 1
        return self.response_json


def test_codex_auditor_returns_empty_when_no_findings(tmp_path: Path):
    client = FakeOpenAIClient(response_json='{"findings": []}')
    auditor = CodexSecurityAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert findings == []
    assert client.calls == 1


def test_codex_auditor_parses_security_findings(tmp_path: Path):
    client = FakeOpenAIClient(response_json='''
    {
      "findings": [
        {
          "rule_id": "codex.sql_injection",
          "severity": "critical",
          "uri": "src/db.py",
          "start_line": 17,
          "message": "f-string interpolation into SQL query"
        }
      ]
    }
    ''')
    auditor = CodexSecurityAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id == "codex.sql_injection"
    assert f.severity == "critical"
    assert f.auditor == "codex_security"


def test_codex_auditor_handles_malformed_response(tmp_path: Path):
    client = FakeOpenAIClient(response_json="garbled response")
    auditor = CodexSecurityAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert findings == []


def test_codex_auditor_id_and_triggers_on(tmp_path: Path):
    client = FakeOpenAIClient(response_json='{"findings": []}')
    auditor = CodexSecurityAuditor(client=client)
    assert auditor.id == "codex_security"
    assert auditor.triggers_on([]) is True


def test_codex_auditor_handles_json_with_markdown_fence(tmp_path: Path):
    client = FakeOpenAIClient(response_json='''
    Analysis:
    ```json
    {"findings": [{"rule_id": "codex.xss", "severity": "high",
      "uri": "x.py", "start_line": 5, "message": "user input rendered"}]}
    ```
    ''')
    auditor = CodexSecurityAuditor(client=client)
    findings = auditor.audit(tmp_path, [])
    assert len(findings) == 1
    assert findings[0].rule_id == "codex.xss"
