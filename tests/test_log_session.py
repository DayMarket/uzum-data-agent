import datetime
import importlib.util
import io
import json
import os
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
            "id": "msg_1",
            "content": [{"type": "tool_use", "name": "mcp__clickhouse__run_query"}],
            "usage": {"input_tokens": 100, "output_tokens": 40,
                      "cache_read_input_tokens": 900},
        }},
        # результат инструмента — тоже запись type == "user"
        {"type": "user", "message": {
            "content": [{"type": "tool_result", "content": "12 строк"}]}},
        {"type": "user", "message": {"content": "пароль hunter2-secret"}},
    ]
    p = tmp_path / "t.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return str(p)


def test_reads_transcript_and_counts(tmp_path):
    text, agg = log_session.read_transcript(_write_transcript(tmp_path), {})
    assert agg["n_prompts"] == 2  # третья запись — tool_result, не промпт
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
                   "tokens_out", "tokens_cache", "repo_sha",
                   "end_reason", "transcript"):
        assert column in row
    assert row["end_reason"] == "clear"
    assert row["jira_key"] == "OE-3491"


def test_row_columns_match_the_table_schema():
    """Набор ключей строки должен совпадать с колонками sandbox.ai_usage_sessions
    (кроме inserted_at с DEFAULT now()) — иначе INSERT молча теряет поле либо
    в таблице остаётся колонка, которую никто не заполняет. Так ушла cost_usd:
    она была в схеме, а писался в неё всегда ноль."""
    schema = (REPO_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    body = schema.split("CREATE TABLE IF NOT EXISTS sandbox.ai_usage_sessions", 1)[1]
    body = body.split("(", 1)[1].split("ENGINE", 1)[0]
    columns = set()
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("--") or line.startswith("(") or line.startswith(")"):
            continue
        columns.add(line.split()[0])
    columns.discard("inserted_at")

    row = log_session.build_session_row(
        {"session_id": "s", "transcript_path": "/nope.jsonl",
         "hook_event_name": "SessionEnd", "reason": "clear"},
        {},
    )
    assert set(row) == columns


def test_cost_usd_is_gone_from_row_and_schema():
    """Находка 7: cost_usd всегда была нулём — пустое обещание в отчётности.
    Колонки нет ни в строке, ни в схеме; стоимость считается из токенов."""
    row = log_session.build_session_row(
        {"session_id": "s", "transcript_path": "/nope.jsonl",
         "hook_event_name": "SessionEnd", "reason": "clear"},
        {},
    )
    assert "cost_usd" not in row
    schema = (REPO_ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    assert "cost_usd     Decimal" not in schema


# --- находки 6 и 7 финального ревью ----------------------------------------


def test_tool_results_are_not_counted_as_prompts(tmp_path):
    """Находка 6: результат инструмента приходит записью с type == "user".
    На живом транскрипте это давало 733 «промпта» вместо 139 настоящих —
    воронка адопшена врала бы в разы."""
    lines = [
        {"type": "user", "message": {"content": "посчитай OPH за июль"}},
        {"type": "user", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t1",
                         "content": "1000 строк"}]}},
        {"type": "user", "message": {
            "content": [{"type": "tool_result", "tool_use_id": "t2",
                         "content": "ещё 1000 строк"}]}},
        {"type": "user", "message": {
            "content": [{"type": "text", "text": "а теперь по складам"}]}},
        # служебная вставка самого Claude Code — не промпт человека
        {"type": "user", "isMeta": True, "message": {
            "content": [{"type": "text", "text": "Base directory for this skill: /x"}]}},
    ]
    p = tmp_path / "prompts.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    _, agg = log_session.read_transcript(str(p), {})

    assert agg["n_prompts"] == 2


def test_usage_is_counted_once_per_message_id(tmp_path):
    """Находка 7: одно сообщение ассистента разложено на несколько записей с
    общим message.id и повторённым usage — наивная сумма задваивает токены
    (на живом транскрипте 2 292 123 против 879 392)."""
    usage = {"input_tokens": 100, "output_tokens": 40,
             "cache_read_input_tokens": 900, "cache_creation_input_tokens": 300}
    lines = [
        {"type": "assistant", "message": {"id": "msg_1", "usage": usage,
                                          "content": [{"type": "text", "text": "думаю"}]}},
        {"type": "assistant", "message": {"id": "msg_1", "usage": usage,
                                          "content": [{"type": "tool_use", "name": "Read"}]}},
        {"type": "assistant", "message": {"id": "msg_1", "usage": usage,
                                          "content": [{"type": "tool_use", "name": "Read"}]}},
        {"type": "assistant", "message": {"id": "msg_2", "usage": usage,
                                          "content": [{"type": "text", "text": "готово"}]}},
    ]
    p = tmp_path / "tokens.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    _, agg = log_session.read_transcript(str(p), {})

    assert agg["tokens_in"] == 200          # два уникальных message.id, не четыре
    assert agg["tokens_out"] == 80
    assert agg["tokens_cache"] == 2400      # (900 чтения + 300 создания) x 2
    assert agg["n_tools"] == 2              # вызовы инструментов не дедуплицируются


def test_usage_without_message_id_is_still_counted(tmp_path):
    """Дедупликация не должна проглатывать строки, у которых id нет вовсе."""
    lines = [
        {"type": "assistant", "message": {
            "usage": {"input_tokens": 7, "output_tokens": 3}, "content": []}},
        {"type": "assistant", "message": {
            "usage": {"input_tokens": 7, "output_tokens": 3}, "content": []}},
    ]
    p = tmp_path / "noid.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    _, agg = log_session.read_transcript(str(p), {})

    assert agg["tokens_in"] == 14
    assert agg["tokens_out"] == 6


# --- регрессии по ревью: находки 2-6 ---------------------------------------


def test_read_transcript_skips_malformed_json_structures(tmp_path):
    """Находка 2: валидный JSON, но не объект (список/null/число/строка вместо
    message), не должен ронять чтение всего транскрипта AttributeError'ом —
    ревьюер воспроизвёл 'list' object has no attribute 'get'. Строка
    пропускается, остальные читаются как обычно."""
    lines = [
        json.dumps([1, 2, 3]),
        json.dumps(None),
        json.dumps(42),
        json.dumps("просто строка"),
        json.dumps({"type": "user", "message": "message тоже не объект"}),
        json.dumps({"type": "user", "message": {"content": "ок, дошли до OE-1"}}),
    ]
    p = tmp_path / "broken.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")

    text, agg = log_session.read_transcript(str(p), {})

    # запись с message не-объектом промптом не считается (её текст всё равно
    # не прочитать), а нормальная — считается; главное, что разбор не упал
    assert agg["n_prompts"] == 1
    assert "OE-1" in text


def test_find_jira_key_ignores_non_project_codes():
    """Находка 3: UTF-8 / ISO-8601 / HTTP-404 не являются ключами задач —
    их префиксы не входят в JIRA_PROJECT_PREFIXES."""
    text = "см. кодировку UTF-8 в файле, ошибка HTTP-404, дата в ISO-8601"
    assert log_session.find_jira_key(text) == ""


def test_jira_key_prioritizes_user_messages_over_assistant(tmp_path):
    """Находка 3: настоящий ключ задачи из сообщения пользователя не должен
    теряться, если раньше по тексту транскрипта встретился похожий на ключ
    код в рассуждениях ассистента."""
    lines = [
        {"type": "assistant", "message": {
            "content": [{"type": "text", "text": "похоже на баг из OE-999, но не уверен"}],
        }},
        {"type": "user", "message": {"content": "нет, актуальная задача MAD-42"}},
    ]
    p = tmp_path / "priority.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    row = log_session.build_session_row(
        {"session_id": "s-priority", "transcript_path": str(p),
         "hook_event_name": "SessionEnd", "reason": "clear"},
        {},
    )
    assert row["jira_key"] == "MAD-42"


def test_skills_used_extracted_from_skill_tool_calls_not_from_paths(tmp_path):
    """Находка 4: skills_used собирается из структурных вызовов tool_use с
    name=="Skill" и input.skill, а не регэкспом по тексту — пути файловой
    системы и обычные tool_use (Read) не должны попадать в список."""
    lines = [
        {"type": "user", "message": {
            "content": "почини /Users/anastasiabir/Desktop/uzum/report.md, см. OE-1",
        }},
        {"type": "assistant", "message": {
            "content": [
                {"type": "tool_use", "name": "Skill",
                 "input": {"skill": "systematic-debugging"}},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/x"}},
            ],
        }},
    ]
    p = tmp_path / "skills.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")

    text, agg = log_session.read_transcript(str(p), {})

    assert agg["skills_used"] == ["systematic-debugging"]
    assert "anastasiabir" not in agg["skills_used"]
    assert "report" not in agg["skills_used"]


def test_transcript_truncated_to_tail_when_over_size_cap(tmp_path, monkeypatch):
    """Находка 5: транскрипт больше потолка читается только хвостом, с
    заметным маркером обрезки — а не целиком, что на большой сессии рискует
    не уложиться в таймаут хука."""
    monkeypatch.setattr(log_session, "TRANSCRIPT_MAX_BYTES", 250)
    lines = [
        {"type": "user", "message": {"content": "самое-самое-старое-сообщение-которое-должно-быть-обрезано-из-хвоста"}},
        {"type": "user", "message": {"content": "ещё одно старое сообщение до обрезки"}},
        {"type": "user", "message": {"content": "свежее сообщение с ключом OE-777"}},
    ]
    p = tmp_path / "big.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert p.stat().st_size > 250

    text, _ = log_session.read_transcript(str(p), {})

    assert "ОБРЕЗАН" in text
    assert "должно-быть-обрезано-из-хвоста" not in text
    assert "OE-777" in text


def test_session_end_removes_started_at_marker_file(tmp_path, monkeypatch):
    """Находка 6 (minor): файл started-<session_id> не должен копиться
    бессрочно — убираем его после того, как SessionEnd перенёс время старта
    в строку сессии."""
    monkeypatch.setattr(log_session, "STATE_DIR", str(tmp_path))
    session_id = "s-cleanup"
    started_path = log_session._started_at_path(session_id)
    with open(started_path, "w", encoding="utf-8") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    assert os.path.exists(started_path)

    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": session_id,
        "transcript_path": _write_transcript(tmp_path),
        "reason": "clear",
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert log_session.main() == 0
    assert not os.path.exists(started_path)
