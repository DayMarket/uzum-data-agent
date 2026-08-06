import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "log_event", REPO_ROOT / ".claude" / "hooks" / "log_event.py"
)
log_event = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_event)


def test_builds_row_for_tool_call():
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "s-1",
        "tool_name": "mcp__clickhouse__run_query",
        "tool_input": {"sql": "select 1"},
    }
    row = log_event.build_row(payload, {})
    assert row["event_type"] == "PostToolUse"
    assert row["tool_name"] == "mcp__clickhouse__run_query"
    assert row["mcp_server"] == "clickhouse"
    assert row["ok"] == 1
    assert row["session_id"] == "s-1"


def test_marks_failure_and_keeps_error_text():
    """Поле называется `error`, а не `tool_error`.

    Схема события PostToolUseFailure зашита в самом Claude Code:
      hook_event_name: literal("PostToolUseFailure"), tool_name: string,
      tool_input: unknown, tool_use_id: string, error: string,
      is_interrupt?: boolean, duration_ms?: number
    Поля tool_error там нет вовсе — до этой правки колонка error_text
    оставалась пустой всегда, а тест был зелёным, потому что сам подавал
    выдуманное поле.
    """
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s-1",
        "tool_name": "Bash",
        "error": "connection refused",
    }
    row = log_event.build_row(payload, {})
    assert row["ok"] == 0
    assert row["error_text"] == "connection refused"


def test_invented_tool_error_field_is_not_a_source_of_error_text():
    """Страж от возврата к выдуманному имени поля: если код снова начнёт
    читать tool_error, тест на настоящем `error` пройдёт, а этот — нет."""
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s-1",
        "tool_name": "Bash",
        "tool_error": "это поле Claude Code не присылает",
    }
    assert log_event.build_row(payload, {})["error_text"] == ""


def test_native_tool_has_empty_mcp_server():
    payload = {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Read"}
    assert log_event.build_row(payload, {})["mcp_server"] == ""


def test_prompt_event_has_no_tool():
    payload = {"hook_event_name": "UserPromptSubmit", "session_id": "s"}
    row = log_event.build_row(payload, {})
    assert row["event_type"] == "UserPromptSubmit"
    assert row["tool_name"] == ""


def test_redacts_secrets_in_error_text():
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s",
        "tool_name": "Bash",
        "tool_error": "auth failed for token test-token-xxxxx",
    }
    row = log_event.build_row(payload, {"test-token-xxxxx": "JIRA_TOKEN"})
    assert "test-token-xxxxx" not in row["error_text"]


def test_unknown_event_returns_none():
    assert log_event.build_row({"hook_event_name": "CwdChanged"}, {}) is None


def test_main_exits_zero_on_broken_input(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("не json"))
    assert log_event.main() == 0


def test_main_skips_secrets_file_for_untracked_event(monkeypatch):
    calls = []
    monkeypatch.setattr(
        log_event.redact, "load_secret_values", lambda path: calls.append(path) or {}
    )
    payload = '{"hook_event_name": "CwdChanged", "session_id": "s"}'
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(payload))
    assert log_event.main() == 0
    assert calls == []


def test_duration_ms_degrades_to_zero_when_not_numeric():
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "s",
        "tool_name": "Read",
        "duration_ms": "не число",
    }
    row = log_event.build_row(payload, {})
    assert row["duration_ms"] == 0
