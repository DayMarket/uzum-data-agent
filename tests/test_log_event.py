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
    payload = {
        "hook_event_name": "PostToolUseFailure",
        "session_id": "s-1",
        "tool_name": "Bash",
        "tool_error": "connection refused",
    }
    row = log_event.build_row(payload, {})
    assert row["ok"] == 0
    assert row["error_text"] == "connection refused"


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
