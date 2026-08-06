import importlib.util
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "log_session", REPO_ROOT / ".claude" / "hooks" / "log_session.py"
)
log_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_session)


def _write_transcript(tmp_path):
    lines = [
        {"type": "user", "message": {"content": "собери выгрузку по OE-3491"}},
        {"type": "assistant", "message": {
            "content": [{"type": "tool_use", "name": "mcp__clickhouse__run_query"}],
            "usage": {"input_tokens": 100, "output_tokens": 40,
                      "cache_read_input_tokens": 900},
        }},
        {"type": "user", "message": {"content": "пароль hunter2-secret"}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_reads_transcript_and_counts(tmp_path):
    text, agg = log_session.read_transcript(_write_transcript(tmp_path), {})
    assert agg["n_prompts"] == 2
    assert agg["n_tools"] == 1
    assert agg["tokens_in"] == 100
    assert agg["tokens_out"] == 40
    assert agg["tokens_cache"] == 900
    assert "OE-3491" in text


def test_redacts_transcript(tmp_path):
    text, _ = log_session.read_transcript(
        _write_transcript(tmp_path), {"hunter2-secret": "CH_PASSWORD"}
    )
    assert "hunter2-secret" not in text
    assert "[СКРЫТО:CH_PASSWORD]" in text


def test_missing_transcript_is_not_fatal():
    text, agg = log_session.read_transcript("/nope/t.jsonl", {})
    assert text == ""
    assert agg["n_prompts"] == 0


def test_extracts_jira_key_from_transcript(tmp_path):
    text, _ = log_session.read_transcript(_write_transcript(tmp_path), {})
    assert log_session.find_jira_key(text) == "OE-3491"


def test_no_jira_key_returns_empty():
    assert log_session.find_jira_key("просто текст без ключей") == ""


def test_build_session_row_has_all_columns(tmp_path):
    payload = {
        "session_id": "s-1",
        "transcript_path": _write_transcript(tmp_path),
        "hook_event_name": "SessionEnd",
        "reason": "clear",
    }
    row = log_session.build_session_row(payload, {})
    for column in ("session_id", "user", "started_at", "ended_at", "duration_s",
                   "jira_key", "skills_used", "n_prompts", "n_tools", "tokens_in",
                   "tokens_out", "tokens_cache", "cost_usd", "repo_sha",
                   "end_reason", "transcript"):
        assert column in row
    assert row["end_reason"] == "clear"
    assert row["jira_key"] == "OE-3491"
