"""Tests for the real Codex security auditor."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_orchestrator.bob.vroom.auditors.codex_security import (
    CodexSecurityAuditor,
    OpenAIClient,
    _ProductionOpenAIClient,
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


class _FakeCompletions:
    def __init__(self) -> None:
        self.kwargs = {}

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"findings": []}'))],
        )


def test_production_openai_client_passes_reasoning_effort(tmp_path: Path, monkeypatch):
    (tmp_path / "app.py").write_text("def handler(): pass\n")
    completions = _FakeCompletions()
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(OpenAI=lambda: fake_client),
    )

    client = _ProductionOpenAIClient(model="gpt-5.4", reasoning_effort="high")
    assert client.audit_workspace(tmp_path, []) == '{"findings": []}'
    assert completions.kwargs["reasoning_effort"] == "high"
