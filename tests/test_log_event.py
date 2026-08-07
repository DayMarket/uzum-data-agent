import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "log_event", REPO_ROOT / ".claude" / "hooks" / "log_event.py"
)
log_event = importlib.util.module_from_spec(spec)
spec.loader.exec_module(log_event)

# Реальная раскладка пути транскрипта Claude Code (docs/codex-facts.md,
# раздел 2) — нужна там, где тест намеренно проверяет поведение,
# специфичное для engine == "claude": без неё detect_engine() честно
# вернёт "unknown" (ревью-находка 4, задача Codex-4), и тест бы проверял не
# то, что заявлено в его собственном докстринге.
A_CLAUDE_TRANSCRIPT_PATH = "/Users/x/.claude/projects/slug/s.jsonl"


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
        "transcript_path": A_CLAUDE_TRANSCRIPT_PATH,
    }
    row = log_event.build_row(payload, {})
    assert row["ok"] == 0
    assert row["outcome"] == "failed"
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
        "transcript_path": A_CLAUDE_TRANSCRIPT_PATH,
    }
    row = log_event.build_row(payload, {})
    assert row["engine"] == "claude"  # проверяем деградацию именно для Claude Code
    assert row["duration_ms"] == 0


# --- Codex-4: признак движка, второй разбор транскрипта --------------------


def test_row_columns_match_the_events_table_schema():
    """Тот же страж, что test_row_columns_match_the_table_schema в
    tests/test_log_session.py: набор ключей строки должен совпадать с
    колонками sandbox.ai_usage_events (кроме inserted_at с DEFAULT now()) —
    иначе INSERT молча теряет поле, либо в таблице остаётся неиспользуемая
    колонка."""
    schema = (REPO_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    body = schema.split("CREATE TABLE IF NOT EXISTS sandbox.ai_usage_events", 1)[1]
    body = body.split("(", 1)[1].split("ENGINE", 1)[0]
    columns = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith("(") or line.startswith(")"):
            continue
        columns.add(line.split()[0])
    columns.discard("inserted_at")

    row = log_event.build_row(
        {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash"}, {}
    )
    assert set(row) == columns


def test_claude_row_has_engine_claude():
    row = log_event.build_row(
        {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash",
         "transcript_path": A_CLAUDE_TRANSCRIPT_PATH}, {}
    )
    assert row["engine"] == "claude"
    assert row["outcome"] == "ok"


def test_unrecognized_transcript_produces_engine_unknown_not_claude():
    """Ревью-находка 4: раньше это молча становилось engine == 'claude'."""
    row = log_event.build_row(
        {"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Bash"}, {}
    )
    assert row["engine"] == "unknown"
    assert row["outcome"] == "unknown"
    assert row["ok"] == 1  # unknown != failed, старое поле не путает "не знаем" с "упал"


CODEX_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"


def _codex_events():
    with open(CODEX_FIXTURES / "hook_events.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_codex_successful_tool_call_without_exit_code_marker_is_unknown_not_ok():
    """Настоящий payload/транскрипт живого запуска Codex (см. докстринг
    tests/test_hook_payload.py и tests/test_transcript_codex.py). На ЭТОМ
    конкретном реальном вызове модель отчиталась голым `text(r.output)`, без
    какого-либо упоминания exit_code (см.
    tests/test_transcript_codex.py::test_successful_tool_call_without_exit_code_marker_is_unknown_not_ok,
    тот же turn_id) — то есть честно "не знаем", а не подтверждённый успех.
    Ревью-находка 3: раньше это писалось как ok=1/error_text="", неотличимо
    от настоящего успеха; теперь outcome явно "unknown", а не "ok"."""
    success_payload = _codex_events()[2]
    assert success_payload["hook_event_name"] == "PostToolUse"

    row = log_event.build_row(success_payload, {})
    assert row["engine"] == "codex"
    assert row["outcome"] == "unknown"
    assert row["ok"] == 1  # unknown != failed — старое поле не путает это с отказом
    assert row["error_text"] == ""
    # Codex не присылает duration_ms никогда — 0 здесь означает "поле не
    # передано", отличить от настоящего 0мс можно по engine == "codex"
    # (см. lib/hook_payload.py и .claude/hooks/log_event.py).
    assert row["duration_ms"] == 0


def test_codex_tool_call_with_confirmed_zero_exit_code_is_outcome_ok(tmp_path):
    """Контрольный случай для test_codex_successful_tool_call_without_..._is_unknown_not_ok
    выше: когда транскрипт ДЕЙСТВИТЕЛЬНО подтверждает exit_code=0, outcome
    должен быть "ok", а не "unknown". Ни один живой образец с явным
    exit_code=0 в собранных фикстурах не встретился (модель, судя по всему,
    реже отчитывается явным кодом на успехе, чем на сбое — см. отчёт
    задачи) — поэтому здесь синтетический транскрипт с реальной структурой
    custom_tool_call_output (docs/codex-facts.md, раздел 3)."""
    turn_id = "turn-confirmed-ok"
    lines = [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "call_x",
            "output": [
                {"type": "input_text", "text": "Script completed\n"},
                {"type": "input_text", "text": '{"exit_code":0,"output":"done"}'},
            ],
            "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
        }},
    ]
    p = tmp_path / "rollout-2026-08-07T00-00-00-turn-confirmed-ok.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    payload = {
        "hook_event_name": "PostToolUse", "session_id": "s", "turn_id": turn_id,
        "tool_name": "Bash", "transcript_path": str(p), "tool_response": "done\n",
    }
    row = log_event.build_row(payload, {})
    assert row["engine"] == "codex"
    assert row["outcome"] == "ok"
    assert row["ok"] == 1
    assert row["error_text"] == ""


def test_codex_prompt_event_never_triggers_transcript_lookup(monkeypatch):
    """Ревью-находка 1 (критично, исправлено): раньше поиск в транскрипте
    (transcript_codex.find_tool_error) запускался для ЛЮБОГО отслеживаемого
    события Codex, включая UserPromptSubmit, а не только PostToolUse — для
    которого он и писан (собственный докстринг это утверждал, код
    расходился). У UserPromptSubmit тот же turn_id, что и у следующего за
    ним вызова инструмента, но в момент отправки промпта сам ход ещё не
    начался — записи о вызове в транскрипте физически быть не может, все
    попытки retry отрабатывали вхолостую ГАРАНТИРОВАННО (измерено
    ревьюером: 322 мс на событие вместо долей миллисекунды — треть секунды
    задержки на каждом промпте аналитика в Codex, без единого шанса
    что-то найти). Этот тест — страж: если find_tool_error снова начнут
    звать на UserPromptSubmit, он упадёт."""
    calls = []
    monkeypatch.setattr(
        log_event.transcript_codex, "find_tool_error",
        lambda path, turn_id: calls.append((path, turn_id)) or {"exit_code": None, "error_text": "", "ok": None},
    )

    prompt_payload = _codex_events()[1]
    assert prompt_payload["hook_event_name"] == "UserPromptSubmit"
    assert "turn_id" in prompt_payload  # тот же turn_id, что у следующего PostToolUse

    row = log_event.build_row(prompt_payload, {})

    assert calls == []  # find_tool_error не звали вовсе
    assert row["engine"] == "codex"
    assert row["outcome"] == "ok"
    assert row["ok"] == 1
    assert row["duration_ms"] == 0


def test_codex_failing_tool_call_has_real_error_text_not_empty():
    """Главная цель телеметрии — «где падает» — не должна остаться пустой
    и для Codex, хотя сам hook payload текста ошибки не несёт вовсе."""
    failure_payload = _codex_events()[6]
    assert failure_payload["hook_event_name"] == "PostToolUse"
    assert failure_payload["tool_response"] == ""  # без разбора транскрипта тут пусто

    row = log_event.build_row(failure_payload, {})
    assert row["engine"] == "codex"
    assert row["outcome"] == "failed"
    assert row["ok"] == 0
    assert "9" in row["error_text"]  # exit code 9


def test_codex_error_text_is_redacted(tmp_path):
    """Секреты маскируются и в тексте ошибки, добытом из транскрипта Codex —
    так же, как для Claude Code (test_redacts_secrets_in_error_text выше).
    Синтетический транскрипт: реальная структура custom_tool_call_output
    (docs/codex-facts.md, раздел 3), но с секретом в поле output, чтобы
    проверить именно маскирование, а не структуру разбора — её уже проверяют
    тесты на настоящем транскрипте (tests/test_transcript_codex.py)."""
    turn_id = "turn-secret-test"
    lines = [
        {"type": "response_item", "payload": {
            "type": "custom_tool_call_output", "call_id": "call_x",
            "output": [
                {"type": "input_text", "text": "Script completed\n"},
                {"type": "input_text", "text": '{"exit_code":1,"output":"auth failed for hunter2-secret"}'},
            ],
            "internal_chat_message_metadata_passthrough": {"turn_id": turn_id},
        }},
    ]
    # Имя файла — с префиксом "rollout-": это единственный признак, по
    # которому detect_engine() узнаёт Codex (lib/hook_payload.py) — набор
    # дополнительных полей hook payload для этого больше не используется.
    p = tmp_path / "rollout-2026-08-07T00-00-00-turn-secret-test.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    payload = {
        "hook_event_name": "PostToolUse", "session_id": "s", "turn_id": turn_id,
        "permission_mode": "bypassPermissions", "tool_name": "Bash",
        "transcript_path": str(p), "tool_response": "",
    }
    row = log_event.build_row(payload, {"hunter2-secret": "CH_PASSWORD"})
    assert row["ok"] == 0
    assert "hunter2-secret" not in row["error_text"]
    assert "[СКРЫТО:CH_PASSWORD]" in row["error_text"]
