"""Строка сессии, которая не ждёт `/exit`.

Повод. Строка в sandbox.ai_usage_sessions писалась ТОЛЬКО на SessionEnd, то
есть при чистом выходе. Люди `/exit` не набирают — закрывают окно, и сессии не
существует вовсе: у Рамиля за 12.08 было 129 событий и ноль строк, а на
дашборде 2730 его не было видно совсем. Теперь строка пишется по ходу работы:
у Claude Code — на хуке Stop, у Codex — на уже зарегистрированном
UserPromptSubmit (его hooks.json лежит вне репозитория и через `git pull` не
обновляется, новое событие потребовало бы обхода всех машин).

Проверяем не функции, а то, что реально запускает движок: скрипты целиком,
отдельными процессами, с настоящими payload'ами и настоящими транскриптами.
Именно так ловится, например, `import log_session` внутри log_event.py: при
запуске скриптом он работает, а при импорте модуля тестом — мог бы и не
работать, и разница осталась бы незамеченной.

Телеметрия направлена в локальную очередь (фикстура telemetry_queue), поэтому
«строка записана» и «строка не записана» — проверяемые факты.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
CODEX_TRANSCRIPT = str(
    REPO_ROOT / "tests" / "fixtures" / "codex"
    / "rollout-2026-08-07T20-49-54-019fdd21-9868-7633-a9ca-63122c263433.jsonl"
)


def _claude_transcript(tmp_path):
    """Транскрипт в настоящей раскладке Claude Code: detect_engine() опознаёт
    движок по пути (.claude/projects/<slug>/<id>.jsonl), и без неё строка
    честно уехала бы с engine == 'unknown'."""
    d = tmp_path / ".claude" / "projects" / "slug"
    d.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"content": "посчитай OPH по OE-3491"}},
        {"type": "assistant", "message": {
            "id": "msg_1", "model": "claude-opus-5",
            "content": [{"type": "tool_use", "name": "mcp__clickhouse__run_query"}],
            "usage": {"input_tokens": 100, "output_tokens": 40,
                      "cache_read_input_tokens": 900}}},
    ]
    p = d / "sess.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def _run(script, payload, telemetry_queue):
    """Хук так, как его запускает движок: отдельный процесс, payload на
    stdin, рабочий каталог внутри нашего клона."""
    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)], cwd=str(REPO_ROOT),
        input=json.dumps(payload), env=telemetry_queue.env,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result


def _rows(telemetry_queue):
    """Очередь, прочитанная устойчиво.

    Отправку забирает ОТВЯЗАННЫЙ процесс (telemetry.flush_in_background), и
    он переписывает файлы очереди неатомарно: открывает на запись (усечение),
    потом пишет заново. Чтение, попавшее в это окно, видит пустой или
    оборванный файл — редко, но видит. Здесь ClickHouse заведомо недоступен
    (127.0.0.1:1), поэтому отправщик кладёт ровно те же строки обратно;
    значит достаточно дождаться, чтобы два подряд чтения совпали.

    Само окно — свойство доставки, а не тестов: при падении машины ровно в
    этот момент очередь теряется. Здесь мы его только обходим.
    """
    previous, deadline = None, time.time() + 5
    while time.time() < deadline:
        try:
            current = telemetry_queue.rows()
        except Exception:                                        # noqa: BLE001
            current = None
        if current is not None and current == previous:
            return current
        previous = current
        time.sleep(0.05)
    return previous or []


def _sessions(telemetry_queue):
    return [row for table, row in _rows(telemetry_queue)
            if table == "ai_usage_sessions"]


def _events(telemetry_queue):
    return [row for table, row in _rows(telemetry_queue)
            if table == "ai_usage_events"]


# ── Claude Code: хук Stop ──────────────────────────────────────────────────


def test_stop_writes_a_session_row_without_waiting_for_the_exit(tmp_path, telemetry_queue):
    """Главное утверждение задачи: сессия попадает в отчётность до выхода."""
    _run("log_session.py", {"hook_event_name": "Stop", "session_id": "s-stop",
                            "transcript_path": _claude_transcript(tmp_path)},
         telemetry_queue)

    rows = _sessions(telemetry_queue)
    assert len(rows) == 1, "строка сессии не появилась на Stop"
    assert rows[0]["session_id"] == "s-stop"
    assert rows[0]["engine"] == "claude"
    assert rows[0]["n_prompts"] == 1
    assert rows[0]["n_tools"] == 1
    assert rows[0]["tokens_in"] == 100
    assert rows[0]["model"] == "claude-opus-5"


def test_intermediate_row_carries_no_transcript(tmp_path, telemetry_queue):
    """Транскрипт — сотни килобайт (в замерах 283-490 КБ). Слать их на каждом
    ходу нельзя: это и сеть, и место в очереди, которая при недоступном
    ClickHouse живёт на диске аналитика."""
    _run("log_session.py", {"hook_event_name": "Stop", "session_id": "s-notr",
                            "transcript_path": _claude_transcript(tmp_path)},
         telemetry_queue)

    assert _sessions(telemetry_queue)[0]["transcript"] == ""


def test_intermediate_row_has_the_same_columns_as_the_final_one(tmp_path, telemetry_queue):
    """Набор ключей не должен зависеть от того, промежуточная строка или
    финальная: «на промежуточной колонок меньше» — это вторая, молчаливая
    форма той же строки, и сверка со схемой таблицы её бы не поймала."""
    transcript = _claude_transcript(tmp_path)
    _run("log_session.py", {"hook_event_name": "Stop", "session_id": "s-cols",
                            "transcript_path": transcript}, telemetry_queue)
    _run("log_session.py", {"hook_event_name": "SessionEnd", "session_id": "s-cols2",
                            "transcript_path": transcript, "reason": "clear"},
         telemetry_queue)

    intermediate, final = _sessions(telemetry_queue)
    assert set(intermediate) == set(final)


def test_session_end_still_carries_the_transcript(tmp_path, telemetry_queue):
    """Страж обратной стороны: разделив строки, легко случайно лишить
    транскрипта и финальную — а он единственный источник контекста сессии."""
    _run("log_session.py", {"hook_event_name": "SessionEnd", "session_id": "s-end",
                            "transcript_path": _claude_transcript(tmp_path),
                            "reason": "prompt_input_exit"}, telemetry_queue)

    row = _sessions(telemetry_queue)[0]
    assert "OE-3491" in row["transcript"]
    assert row["end_reason"] == "prompt_input_exit"


# ── Троттлинг ─────────────────────────────────────────────────────────────


def test_second_stop_within_a_minute_writes_nothing(tmp_path, telemetry_queue):
    """Stop срабатывает после КАЖДОГО ответа модели. Без потолка частоты
    активная сессия слала бы десятки почти одинаковых строк в минуту, каждая
    с полным разбором транскрипта."""
    payload = {"hook_event_name": "Stop", "session_id": "s-throttle",
               "transcript_path": _claude_transcript(tmp_path)}
    _run("log_session.py", payload, telemetry_queue)
    _run("log_session.py", payload, telemetry_queue)
    _run("log_session.py", payload, telemetry_queue)

    assert len(_sessions(telemetry_queue)) == 1


def test_stop_writes_again_once_the_interval_has_passed(tmp_path, telemetry_queue):
    """Обратная сторона того же: троттлинг не должен превращаться в «одна
    строка на сессию» — иначе агрегаты замрут на первой минуте работы.
    Время не ждём, а состариваем метку — ждать минуту в тестах нечестно."""
    payload = {"hook_event_name": "Stop", "session_id": "s-again",
               "transcript_path": _claude_transcript(tmp_path)}
    _run("log_session.py", payload, telemetry_queue)

    marker = Path(telemetry_queue.env["UZUM_STATE_DIR"]) / "progress-s-again"
    assert marker.exists(), "метка троттлинга не создана"
    old = time.time() - 61
    os.utime(marker, (old, old))

    _run("log_session.py", payload, telemetry_queue)
    assert len(_sessions(telemetry_queue)) == 2


def test_session_end_removes_the_throttle_marker(tmp_path, telemetry_queue):
    """Файл на каждую сессию бессрочно копиться в STATE_DIR не должен — та же
    причина, по которой убирается метка started-<id>."""
    transcript = _claude_transcript(tmp_path)
    _run("log_session.py", {"hook_event_name": "Stop", "session_id": "s-clean",
                            "transcript_path": transcript}, telemetry_queue)
    marker = Path(telemetry_queue.env["UZUM_STATE_DIR"]) / "progress-s-clean"
    assert marker.exists()

    _run("log_session.py", {"hook_event_name": "SessionEnd", "session_id": "s-clean",
                            "transcript_path": transcript, "reason": "clear"},
         telemetry_queue)
    assert not marker.exists()


# ── Codex: тот же результат через UserPromptSubmit ─────────────────────────


def test_codex_prompt_writes_a_session_row_too(telemetry_queue):
    """У Codex хука Stop нет и не будет: hooks.json живёт в $CODEX_HOME, вне
    репозитория, и `git pull` его не обновляет. Строку пишет log_event.py на
    уже зарегистрированном UserPromptSubmit — вместе со строкой события."""
    _run("log_event.py", {"hook_event_name": "UserPromptSubmit",
                          "session_id": "019fdd21-9868-7633-a9ca-63122c263433",
                          "transcript_path": CODEX_TRANSCRIPT,
                          "turn_id": "t-1"}, telemetry_queue)

    sessions = _sessions(telemetry_queue)
    assert len(sessions) == 1, "сессия Codex снова видна только после /exit"
    assert sessions[0]["engine"] == "codex"
    assert sessions[0]["tokens_in"] == 58109
    assert sessions[0]["transcript"] == ""
    assert len(_events(telemetry_queue)) == 1, "строка события пропала"


def test_claude_prompt_does_not_duplicate_the_stop_row(tmp_path, telemetry_queue):
    """У Claude Code за обогащение отвечает Stop. Если бы то же делал ещё и
    UserPromptSubmit, каждая сессия писала бы вдвое больше строк без единого
    нового факта в них."""
    _run("log_event.py", {"hook_event_name": "UserPromptSubmit",
                          "session_id": "s-claude-prompt",
                          "transcript_path": _claude_transcript(tmp_path)},
         telemetry_queue)

    assert _sessions(telemetry_queue) == []
    assert len(_events(telemetry_queue)) == 1


def test_codex_tool_call_does_not_write_a_session_row(telemetry_queue):
    """PostToolUse у Codex — самое частое событие. Разбор транскрипта на
    каждом вызове инструмента стоил бы аналитику времени на каждом шаге."""
    _run("log_event.py", {"hook_event_name": "PostToolUse",
                          "session_id": "019fdd21-9868-7633-a9ca-63122c263433",
                          "transcript_path": CODEX_TRANSCRIPT,
                          "tool_name": "shell", "turn_id": "t-1"}, telemetry_queue)

    assert _sessions(telemetry_queue) == []
