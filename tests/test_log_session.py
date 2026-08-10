import datetime
import importlib.util
import io
import json
import os
import pathlib
from pathlib import Path

import transcript_codex

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


# --- Codex-4: признак движка, второй разбор транскрипта --------------------


CODEX_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "codex"
# Имя сохранено таким же, каким его пишет сам Codex (префикс "rollout-") —
# это тот же признак движка, что использует lib/hook_payload.detect_engine().
CODEX_TRANSCRIPT = str(
    CODEX_FIXTURES / "rollout-2026-08-07T20-49-54-019fdd21-9868-7633-a9ca-63122c263433.jsonl"
)
# Транскрипт интерактивной сессии (TUI) — снят живым запуском 10.08.2026,
# см. докстринг tests/test_transcript_codex.py. Разбор промптов у exec и TUI
# разный, и до этой правки телеметрия считала промпты только в exec.
CODEX_TRANSCRIPT_TUI = str(
    CODEX_FIXTURES / "rollout-tui-2026-08-10T18-11-56-019fec04.jsonl"
)


def test_claude_session_row_has_engine_claude(tmp_path):
    """Путь должен реально выглядеть как раскладка Claude Code
    (.claude/projects/<slug>/<id>.jsonl, docs/codex-facts.md, раздел 2) —
    иначе detect_engine() честно вернёт "unknown" (ревью-находка 4, задача
    Codex-4), а не "claude" по умолчанию, и тест проверял бы не то, что
    заявлено в его названии."""
    claude_dir = tmp_path / ".claude" / "projects" / "slug"
    claude_dir.mkdir(parents=True)
    row = log_session.build_session_row(
        {"session_id": "s", "transcript_path": _write_transcript(claude_dir),
         "hook_event_name": "SessionEnd", "reason": "clear"},
        {},
    )
    assert row["engine"] == "claude"


def test_unrecognized_transcript_produces_session_engine_unknown_not_claude(tmp_path):
    """Ревью-находка 4: раньше это молча становилось engine == 'claude'."""
    row = log_session.build_session_row(
        {"session_id": "s", "transcript_path": _write_transcript(tmp_path),
         "hook_event_name": "SessionEnd", "reason": "clear"},
        {},
    )
    assert row["engine"] == "unknown"


def test_codex_session_row_has_engine_codex_and_real_counts():
    """Настоящий транскрипт живого запуска Codex (см. докстринг
    tests/test_transcript_codex.py): два хода, два реальных промпта, два
    вызова инструментов."""
    payload = {
        "session_id": "019fdd21-9868-7633-a9ca-63122c263433",
        "transcript_path": CODEX_TRANSCRIPT,
        "cwd": "/tmp/codex-project",
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }
    row = log_session.build_session_row(payload, {})
    assert row["engine"] == "codex"
    assert row["n_prompts"] == 2
    assert row["n_tools"] == 2
    assert row["tokens_in"] == 58109


def test_interactive_codex_session_row_counts_the_prompts():
    """Строка сессии из ИНТЕРАКТИВНОГО Codex — того режима, в котором
    работает аналитик. На живых данных у всех таких сессий было
    n_prompts = 0 при непустых n_tools и токенах: разбор знал только формат
    `codex exec`. Здесь тот же путь, что у хука на SessionEnd, но на
    транскрипте TUI."""
    payload = {
        "session_id": "019fec04-0de1-79f2-9897-9212dfc25265",
        "transcript_path": CODEX_TRANSCRIPT_TUI,
        "cwd": "/tmp/codex-project",
        "hook_event_name": "SessionEnd",
        "reason": "other",
    }
    row = log_session.build_session_row(payload, {})

    assert row["engine"] == "codex"
    assert row["n_prompts"] == 1, (
        "промпт интерактивной сессии снова не посчитан — это и есть "
        "n_prompts = 0 в sandbox.ai_usage_sessions")


def test_end_reason_is_written_as_the_engine_reported_it():
    """Codex сообщает только 'other' — словаря причин у него нет вовсе
    (проверено живым запуском обоих режимов и строками самого бинаря,
    docs/codex-facts.md, раздел 2). Значение пишется как пришло: любая
    «нормализация» здесь была бы выдуманной причиной завершения, а по этой
    колонке сравнивают движки."""
    for reason in ("other", "prompt_input_exit", "clear"):
        payload = {
            "session_id": "s-reason",
            "transcript_path": CODEX_TRANSCRIPT_TUI,
            "cwd": "/tmp/codex-project",
            "hook_event_name": "SessionEnd",
            "reason": reason,
        }
        assert log_session.build_session_row(payload, {})["end_reason"] == reason


def test_row_columns_match_the_table_schema_for_codex_engine():
    """Тот же страж, что test_row_columns_match_the_table_schema выше, но
    по пути определения движка Codex — набор ключей не должен зависеть от
    того, каким парсером транскрипта построена строка."""
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
        {"session_id": "019fdd21-9868-7633-a9ca-63122c263433",
         "transcript_path": CODEX_TRANSCRIPT, "hook_event_name": "SessionEnd",
         "reason": "other"},
        {},
    )
    assert set(row) == columns


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


# --- Модель и корпоративный аккаунт в строке сессии ------------------------
#
# Обе колонки заведены по запросу владельца: по данным нельзя было ответить
# ни «какой моделью работали», ни «кто это был» (колонка user — имя
# пользователя ноутбука). Проверяется на НАСТОЯЩИХ транскриптах обоих
# движков, а не на выдуманных: у Claude Code и Codex модель лежит в разных
# местах, и «работает на моей фикстуре» тут ничего не значит.

CLAUDE_TRANSCRIPT_REAL = str(
    REPO_ROOT / "tests" / "fixtures" / "claude" / "transcript-7dc1bb17.jsonl"
)


def test_model_is_taken_from_a_real_claude_transcript():
    """Живой файл Claude Code: одна сессия, один ответ модели.
    message.model записей assistant — единственное место, где движок её
    называет."""
    _text, agg = log_session.read_transcript(CLAUDE_TRANSCRIPT_REAL, {})

    assert agg["_models"] == ["claude-opus-5"], agg["_models"]
    assert log_session.pick_model(agg["_models"]) == "claude-opus-5"


def test_model_is_taken_from_a_real_codex_transcript():
    """Живой файл Codex (интерактивная сессия). Модель там называется в
    turn_context — в session_meta ключа model нет вовсе, проверено."""
    _text, agg = transcript_codex.read_transcript(CODEX_TRANSCRIPT_TUI, {})

    assert agg["_models"] == ["gpt-5.6-sol"], agg["_models"]


def test_session_row_carries_the_model_of_each_engine(tmp_path):
    """Та же проверка, но через строку, которая реально уходит в
    ClickHouse: колонка не должна теряться по дороге от парсера к строке.

    Файл Claude Code кладём по НАСТОЯЩЕЙ раскладке
    (~/.claude/projects/<slug>/<session_id>.jsonl) — движок определяется по
    имени и пути файла (lib/hook_payload.detect_engine), и из каталога
    фикстур он был бы «unknown», то есть тест проверял бы не тот путь."""
    projects = tmp_path / ".claude" / "projects" / "slug"
    projects.mkdir(parents=True)
    claude_path = projects / "7dc1bb17-2de2-4ad4-972c-a486d894bb6b.jsonl"
    claude_path.write_bytes(pathlib.Path(CLAUDE_TRANSCRIPT_REAL).read_bytes())

    claude_row = log_session.build_session_row(
        {"session_id": "s-claude", "transcript_path": str(claude_path),
         "hook_event_name": "SessionEnd", "reason": "clear"}, {})
    codex_row = log_session.build_session_row(
        {"session_id": "s-codex", "transcript_path": CODEX_TRANSCRIPT_TUI,
         "cwd": "/tmp/codex-project", "hook_event_name": "SessionEnd",
         "reason": "other"}, {})

    assert claude_row["engine"] == "claude"
    assert claude_row["model"] == "claude-opus-5"
    assert codex_row["engine"] == "codex"
    assert codex_row["model"] == "gpt-5.6-sol"


def test_dominant_model_wins_over_the_one_that_happened_to_be_last():
    """Правило выбора, если модель меняли за сессию. «Последняя» ответила бы
    неверно на вопрос «какой моделью работали»: двадцать ходов на одной
    модели и один в конце на другой — это работа на первой."""
    models = ["claude-opus-5"] * 20 + ["claude-fable-5"]

    assert log_session.pick_model(models) == "claude-opus-5"


def test_tie_between_models_is_broken_by_the_last_one():
    """Поровну — берём последнюю: это единственный детерминированный выбор,
    который не зависит от порядка ключей в словаре."""
    assert log_session.pick_model(["a", "b", "a", "b"]) == "b"


def test_no_model_in_the_transcript_leaves_the_column_empty():
    """Сессия без единого ответа модели. Пустая строка честнее подстановки
    «наверное, дефолтная»."""
    assert log_session.pick_model([]) == ""


def test_both_clickhouse_logins_are_recorded_and_not_swapped(tmp_path):
    """Две учётки, и они не взаимозаменяемы: на рабочей машине владельца в
    складской кластер ходят под заимствованной учёткой, а личная там не
    заведена вовсе. Перепутать их местами — самая вероятная и самая
    незаметная ошибка здесь: обе строки, обе похожи на логин."""
    secrets = tmp_path / "secrets.env"
    secrets.write_text("CH_WMS_USER='n-lyubchenko'\nCH_DWH_USER='a-bir'\n",
                       encoding="utf-8")

    wms, dwh = log_session.clickhouse_users({}, str(secrets))

    assert wms == "n-lyubchenko", "в ch_wms_user не та учётка"
    assert dwh == "a-bir", "в ch_dwh_user не та учётка"


def test_dwh_login_is_empty_when_only_the_warehouse_is_configured(tmp_path):
    """DWH при установке необязателен. Пусто — это факт «личной учётки нет»,
    а не повод подставить складскую: опознание человека собирается при
    чтении, и подмена здесь спрятала бы заимствованную учётку."""
    secrets = tmp_path / "secrets.env"
    secrets.write_text("CH_WMS_USER='i-petrov'\n", encoding="utf-8")

    wms, dwh = log_session.clickhouse_users({}, str(secrets))

    assert wms == "i-petrov"
    assert dwh == ""


def test_both_logins_are_empty_when_nothing_is_configured(tmp_path):
    secrets = tmp_path / "secrets.env"
    secrets.write_text("JIRA_TOKEN='t'\n", encoding="utf-8")

    assert log_session.clickhouse_users({}, str(secrets)) == ("", "")
    assert log_session.clickhouse_users({}, str(tmp_path / "нет-такого")) == ("", "")


def test_logins_are_read_from_the_environment_the_launcher_prepared(tmp_path):
    """bin/uzum разворачивает secrets.env в окружение перед запуском
    движка — оттуда и берём в первую очередь."""
    secrets = tmp_path / "secrets.env"
    secrets.write_text("CH_DWH_USER='из-файла'\n", encoding="utf-8")

    wms, dwh = log_session.clickhouse_users(
        {"CH_DWH_USER": "из-окружения"}, str(secrets))

    assert dwh == "из-окружения"
    assert wms == ""


def test_session_row_keeps_machine_user_and_both_logins_apart(monkeypatch, tmp_path):
    """Три разных значения в одной строке, и ни одно не подменяет другое:
    user — учётка ноутбука, ch_wms_user — та, под которой ходят в склад,
    ch_dwh_user — личная в DWH. Раньше в user пытались положить
    корпоративный логин через переменную, которой давно нет (см. bin/uzum),
    и там молча оказывалось имя пользователя машины."""
    secrets = tmp_path / "secrets.env"
    secrets.write_text("CH_WMS_USER='n-lyubchenko'\nCH_DWH_USER='a-bir'\n",
                       encoding="utf-8")
    monkeypatch.setattr(log_session, "SECRETS_PATH", str(secrets))
    monkeypatch.setenv("UZUM_USER", "ноутбук-аналитика")
    monkeypatch.delenv("CH_WMS_USER", raising=False)
    monkeypatch.delenv("CH_DWH_USER", raising=False)

    row = log_session.build_session_row(
        {"session_id": "s", "transcript_path": CLAUDE_TRANSCRIPT_REAL,
         "hook_event_name": "SessionEnd", "reason": "clear"}, {})

    assert row["user"] == "ноутбук-аналитика"
    assert row["ch_wms_user"] == "n-lyubchenko"
    assert row["ch_dwh_user"] == "a-bir"
    assert len({row["user"], row["ch_wms_user"], row["ch_dwh_user"]}) == 3
