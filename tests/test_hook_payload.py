"""Тесты на настоящих payload обоих движков.

Payload Claude Code скопированы дословно из docs/codex-facts.md, раздел 2
"Содержимое события хука в обоих движках" — они там уже помечены как снятые
живым запуском (`claude -p` + хук-логгер на реальном временном проекте),
это не выдумка.

Payload Codex взяты из tests/fixtures/codex/hook_events.jsonl — тоже живой
запуск (изолированный CODEX_HOME, `codex exec` + `codex exec resume --last`,
07.08.2026, при подготовке этой задачи): один turn с успешным
`echo hello-fixture-success`, второй — с падающим `exit 9`, в одной сессии,
чтобы transcript_path у обоих событий указывал на один и тот же реальный
транскрипт (tests/fixtures/codex/transcript.jsonl).

Payload в tests/fixtures/claude/hook_events.jsonl — отдельный, ещё более
свежий живой запуск: настоящий `claude -p` с настоящими
.claude/hooks/log_event.py и log_session.py этого репозитория, подключённый
к mock ClickHouse (07.08.2026, при подготовке этой задачи, живая проверка
из отчёта). Именно на нём поймана регрессия — см.
test_permission_mode_is_not_a_codex_signal().
"""
import json
from pathlib import Path

import hook_payload

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"


def _codex_events():
    with open(FIXTURES / "hook_events.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


CODEX_EVENTS = _codex_events()
CODEX_SESSION_START = CODEX_EVENTS[0]
CODEX_USER_PROMPT = CODEX_EVENTS[1]
CODEX_POST_TOOL_USE_SUCCESS = CODEX_EVENTS[2]
CODEX_SESSION_END = CODEX_EVENTS[3]
CODEX_POST_TOOL_USE_FAILURE = CODEX_EVENTS[6]

# docs/codex-facts.md, раздел 2 — Claude Code, PostToolUse (единственный
# успешный вызов, `echo hello-hook-test`), живой запуск `claude -p` с
# зарегистрированным хук-логгером.
CLAUDE_POST_TOOL_USE = {
    "session_id": "32190b20-ba76-4975-8ff7-000000000000",
    "transcript_path": "/Users/anastasiabir/.claude/projects/slug/32190b20-ba76-4975-8ff7-000000000000.jsonl",
    "cwd": "/private/tmp/claude-hooktest",
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "echo hello-hook-test", "description": "run echo"},
    "tool_response": {"stdout": "hello-hook-test", "stderr": "", "interrupted": False,
                       "isImage": False, "noOutputExpected": False},
    "tool_use_id": "toolu_01L9example",
    "duration_ms": 51,
}

# docs/codex-facts.md, раздел 2 — Claude Code, SessionEnd, живой запуск.
CLAUDE_SESSION_END = {
    "session_id": "32190b20-ba76-4975-8ff7-000000000000",
    "transcript_path": "/Users/anastasiabir/.claude/projects/slug/32190b20-ba76-4975-8ff7-000000000000.jsonl",
    "hook_event_name": "SessionEnd",
    "reason": "other",
}

# docs/codex-facts.md, раздел 3 — Claude Code, PostToolUseFailure (единственное
# место, где у Claude Code вообще есть текст ошибки; PostToolUse при сбое
# инструмента не вызывается вовсе — раздел 3). Схема события зашита в самом
# Claude Code (см. .claude/hooks/log_event.py): поле называется "error".
CLAUDE_POST_TOOL_USE_FAILURE = {
    "session_id": "32190b20-ba76-4975-8ff7-000000000000",
    "transcript_path": "/Users/anastasiabir/.claude/projects/slug/32190b20-ba76-4975-8ff7-000000000000.jsonl",
    "hook_event_name": "PostToolUseFailure",
    "tool_name": "Bash",
    "tool_input": {"command": "exit 7"},
    "tool_use_id": "toolu_01example",
    "error": "Exit code 7",
}


# --- detect_engine ----------------------------------------------------------


def test_detects_codex_from_transcript_path_on_every_event_type():
    """Основной и единственный практический признак — имя файла транскрипта
    (rollout-*.jsonl), присутствует в каждом типе события Codex, включая
    SessionEnd, где полей turn_id/permission_mode/model нет вовсе
    (docs/codex-facts.md, раздел 2)."""
    assert hook_payload.detect_engine(CODEX_POST_TOOL_USE_SUCCESS) == "codex"
    assert hook_payload.detect_engine(CODEX_USER_PROMPT) == "codex"
    assert hook_payload.detect_engine(CODEX_SESSION_START) == "codex"
    assert "turn_id" not in CODEX_SESSION_END
    assert hook_payload.detect_engine(CODEX_SESSION_END) == "codex"


def _claude_live_events():
    fixtures = Path(__file__).resolve().parent / "fixtures" / "claude"
    with open(fixtures / "hook_events.jsonl", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def test_permission_mode_is_not_a_codex_signal():
    """Регрессия на настоящий баг, пойманный живым запуском при подготовке
    этой задачи (см. предупреждение в docstring detect_engine()):
    docs/codex-facts.md утверждал, что Claude Code не кладёт
    permission_mode в hook payload. Живой `claude -p` (версия 2.1.224 —
    та же, что и в разведке) с реальными хуками показал обратное: поле
    "permission_mode":"bypassPermissions" реально пришло в UserPromptSubmit,
    PostToolUse и PostToolUseFailure (tests/fixtures/claude/hook_events.jsonl).
    Раньше detect_engine() принимал любое из turn_id/permission_mode/model
    за признак Codex — это помечало бы такую сессию Claude Code как Codex и
    путало бы ok/duration_ms/error_text местами. Теперь единственный
    практический признак — имя файла транскрипта, а не набор ключей."""
    events = _claude_live_events()
    with_permission_mode = [e for e in events if "permission_mode" in e]
    assert len(with_permission_mode) == 3  # UserPromptSubmit, PostToolUse, PostToolUseFailure
    for event in with_permission_mode:
        assert hook_payload.detect_engine(event) == "claude"


def test_detects_claude_by_default():
    assert hook_payload.detect_engine(CLAUDE_POST_TOOL_USE) == "claude"
    assert hook_payload.detect_engine(CLAUDE_SESSION_END) == "claude"


def test_detects_claude_from_realistic_flat_project_path():
    """Путь Claude Code — плоский файл <session_id>.jsonl без префикса
    rollout-, в отличие от Codex; проверяем, что это не ложно матчится."""
    payload = {"session_id": "s", "transcript_path": "/x/.claude/projects/foo/s.jsonl"}
    assert hook_payload.detect_engine(payload) == "claude"


def test_detect_engine_survives_missing_transcript_path():
    """Ревью-находка 4 (задача Codex-4): раньше отсутствие transcript_path
    молча трактовалось как Claude Code — тот же класс ошибки, что и провал
    с permission_mode, только слоем ниже. Теперь это отдельное, видимое в
    данных состояние ("unknown"), а не тихая догадка. Ни в одном реальном
    payload'е обоих движков (docs/codex-facts.md, раздел 2) transcript_path
    не отсутствовал — это защитный случай, не наблюдаемый на практике."""
    assert hook_payload.detect_engine({"session_id": "s"}) == "unknown"


def test_detect_engine_returns_unknown_for_unrecognized_transcript_path():
    """Путь есть, но не похож ни на Codex (rollout-*.jsonl), ни на Claude
    Code (.claude/projects/...) — тоже "unknown", не молчаливый Claude."""
    payload = {"session_id": "s", "transcript_path": "/var/log/something-else.jsonl"}
    assert hook_payload.detect_engine(payload) == "unknown"


# --- normalize: session_id / transcript_path / tool_name -------------------


def test_normalize_extracts_common_fields_from_claude_payload():
    norm = hook_payload.normalize(CLAUDE_POST_TOOL_USE)
    assert norm["session_id"] == "32190b20-ba76-4975-8ff7-000000000000"
    assert norm["tool_name"] == "Bash"
    assert norm["engine"] == "claude"
    assert norm["transcript_path"].endswith(".jsonl")


def test_normalize_extracts_common_fields_from_codex_payload():
    norm = hook_payload.normalize(CODEX_POST_TOOL_USE_SUCCESS)
    assert norm["session_id"] == CODEX_POST_TOOL_USE_SUCCESS["session_id"]
    assert norm["tool_name"] == "Bash"
    assert norm["engine"] == "codex"
    assert norm["transcript_path"] == CODEX_POST_TOOL_USE_SUCCESS["transcript_path"]


# --- normalize: duration_ms --------------------------------------------------


def test_claude_duration_ms_is_read_when_present():
    assert hook_payload.normalize(CLAUDE_POST_TOOL_USE)["duration_ms"] == 51


def test_claude_duration_ms_degrades_to_zero_when_missing_or_bad():
    """Существующее, покрытое тестами (tests/test_log_event.py) поведение
    Claude Code: поле в схеме есть, но конкретное значение может быть
    битым/отсутствовать — это не то же самое, что "поля нет в принципе".

    transcript_path задан по реальной раскладке Claude Code (docs/codex-facts.md,
    раздел 2) специально — иначе detect_engine() честно вернёт "unknown"
    (ревью-находка 4) и тест проверял бы не то поведение, что заявлено."""
    claude_path = "/Users/x/.claude/projects/slug/s.jsonl"
    assert hook_payload.normalize(
        {"tool_name": "Bash", "transcript_path": claude_path}
    )["duration_ms"] == 0
    assert hook_payload.normalize(
        {"duration_ms": "не число", "transcript_path": claude_path}
    )["duration_ms"] == 0


def test_codex_duration_ms_is_none_not_zero():
    """docs/codex-facts.md, раздел 2, строка «Длительность»: у Codex поля
    duration_ms нет НИКОГДА в hook payload — ни при успехе, ни при ошибке.
    0 читался бы как «уложились в 0 мс», что неправда — normalize() честно
    отдаёт None, а не подставляет число."""
    assert hook_payload.normalize(CODEX_POST_TOOL_USE_SUCCESS)["duration_ms"] is None
    assert hook_payload.normalize(CODEX_POST_TOOL_USE_FAILURE)["duration_ms"] is None
    assert "duration_ms" not in CODEX_POST_TOOL_USE_SUCCESS  # поля нет и в сырых данных


# --- normalize: error_text ----------------------------------------------------


def test_claude_error_text_comes_from_error_field_not_tool_error():
    norm = hook_payload.normalize(CLAUDE_POST_TOOL_USE_FAILURE)
    assert norm["error_text"] == "Exit code 7"


def test_claude_error_text_empty_when_no_failure_event():
    assert hook_payload.normalize(CLAUDE_POST_TOOL_USE)["error_text"] == ""


def test_codex_error_text_is_always_empty_at_normalize_level():
    """docs/codex-facts.md, раздел 3: у Codex текста ошибки в hook payload
    нет вообще ни при каком событии — tool_response при сбое пустая строка,
    без exit_code/is_error/stderr. normalize() не пытается угадать: реальный
    текст ошибки достаёт lib/transcript_codex.find_tool_error() из
    транскрипта, отдельным шагом, не внутри normalize()."""
    assert CODEX_POST_TOOL_USE_FAILURE.get("tool_response") == ""
    norm = hook_payload.normalize(CODEX_POST_TOOL_USE_FAILURE)
    assert norm["error_text"] == ""
    # и на успешном вызове тоже — normalize() не читает transcript вовсе
    assert hook_payload.normalize(CODEX_POST_TOOL_USE_SUCCESS)["error_text"] == ""
