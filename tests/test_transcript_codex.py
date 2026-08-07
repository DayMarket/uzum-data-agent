"""Тесты второго разбора транскрипта — для Codex.

Транскрипт — настоящий, живым запуском (изолированный CODEX_HOME, `codex
exec` + `codex exec resume --last`, 07.08.2026, снят при подготовке этой
задачи): одна сессия, два хода — успешный `echo hello-fixture-success` и
падающий `exit 9`. Файл: tests/fixtures/codex/transcript.jsonl, hook-события
той же сессии — tests/fixtures/codex/hook_events.jsonl (turn_id совпадает
дословно между ними — см. докстринг lib/transcript_codex.py про то, почему
это единственный надёжный ключ сверки, а не tool_use_id).
"""
import json
from pathlib import Path

import transcript_codex

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "codex"
# Имя файла сохранено ровно таким, каким его написал сам Codex — с
# префиксом "rollout-" (docs/codex-facts.md, раздел 2): это же имя
# используется как признак движка в lib/hook_payload.detect_engine(), и
# тест на определение движка (tests/test_log_session.py) полагается на то,
# что путь к транскрипту выглядит по-настоящему.
TRANSCRIPT = str(FIXTURES / "rollout-2026-08-07T20-49-54-019fdd21-9868-7633-a9ca-63122c263433.jsonl")

TURN_ID_SUCCESS = "019fdd21-9884-7b10-ae30-98a741054015"
TURN_ID_FAILURE = "019fdd22-355c-7721-a656-5e7416f795f2"


# --- read_transcript ---------------------------------------------------------


def test_reads_real_transcript_and_counts_prompts_and_tools():
    text, agg = transcript_codex.read_transcript(TRANSCRIPT, {})
    # Два реальных промпта человека (event_msg/user_message), а не три —
    # role=="user" в response_item матчит и служебную вставку
    # <recommended_plugins>, которая не является промптом человека.
    assert agg["n_prompts"] == 2
    assert agg["n_tools"] == 2
    assert "hello-fixture-success" in text


def test_tokens_come_from_last_token_count_event_not_summed():
    """token_count у Codex — уже кумулятивный агрегат (docs/codex-facts.md,
    раздел 6), а не инкремент. На этом транскрипте 4 записи token_count с
    нарастающим total_tokens (14307 → 28863 → 43592 → 58376) — берём
    последнюю, а не сумму всех четырёх (это дало бы астрономическое число,
    та же ошибка по духу, что 30-кратное завышение промптов, о котором
    предупреждает бриф)."""
    _, agg = transcript_codex.read_transcript(TRANSCRIPT, {})
    assert agg["tokens_in"] == 58109
    assert agg["tokens_out"] == 267
    assert agg["tokens_cache"] == 50176  # cached_input + cache_write, оба ноль/50176


def test_missing_transcript_is_not_fatal():
    text, agg = transcript_codex.read_transcript("/nope/t.jsonl", {})
    assert text == ""
    assert agg["n_prompts"] == 0


def test_redacts_transcript_text():
    text, _ = transcript_codex.read_transcript(
        TRANSCRIPT, {"hello-fixture-success": "TEST_SECRET"}
    )
    assert "hello-fixture-success" not in text
    assert "[СКРЫТО:TEST_SECRET]" in text


def test_truncates_to_tail_when_over_size_cap(monkeypatch):
    monkeypatch.setattr(transcript_codex, "TRANSCRIPT_MAX_BYTES", 500)
    text, _ = transcript_codex.read_transcript(TRANSCRIPT, {})
    assert "ТРАНСКРИПТ ОБРЕЗАН" in text


def test_read_transcript_skips_malformed_json_lines(tmp_path):
    lines = [
        json.dumps([1, 2, 3]),
        json.dumps(None),
        json.dumps({"type": "event_msg", "payload": {"type": "user_message",
                                                        "message": "дошли до OE-1"}}),
    ]
    p = tmp_path / "broken.jsonl"
    p.write_text("\n".join(lines), encoding="utf-8")
    text, agg = transcript_codex.read_transcript(str(p), {})
    assert agg["n_prompts"] == 1
    assert "OE-1" in text


# --- find_tool_error ----------------------------------------------------------


def test_successful_tool_call_without_exit_code_marker_is_unknown_not_ok():
    """Находка сверх docs/codex-facts.md, важная для честности "ok": блок
    output записи custom_tool_call_output — не фиксированная схема, а текст,
    который САМ СОСТАВЛЯЕТ модель своим JS-кодом при вызове exec
    (`text(r.output)` против `text(JSON.stringify({exit_code:...}))` —
    смотри custom_tool_call.input этой же сессии). На успешном вызове
    (`echo hello-fixture-success`) модель написала простой `text(r.output)`
    без какой-либо обёртки exit_code — то есть узнать код выхода структурно
    неоткуда, и find_tool_error() честно возвращает "не знаю" (ok=None), а
    НЕ угадывает "успех" по отсутствию признаков ошибки. Дальше по цепочке
    (.claude/hooks/log_event.py) None трактуется как "не отказ", а не как
    подтверждённый успех — это разные вещи, см. докстринг find_tool_error()."""
    info = transcript_codex.find_tool_error(TRANSCRIPT, TURN_ID_SUCCESS)
    assert info["ok"] is None
    assert info["exit_code"] is None
    assert info["error_text"] == ""


def test_finds_real_error_text_for_failing_tool_call():
    """Главная цель телеметрии — «где падает» — не должна остаться пустой:
    exit_code лежит внутри JSON-строки, вложенной во второй элемент массива
    output (docs/codex-facts.md, раздел 3), а не полем верхнего уровня."""
    info = transcript_codex.find_tool_error(TRANSCRIPT, TURN_ID_FAILURE)
    assert info["ok"] is False
    assert info["exit_code"] == 9
    assert "9" in info["error_text"]


def test_find_tool_error_by_turn_id_not_tool_use_id():
    """Находка сверх docs/codex-facts.md: hook-событие несёт tool_use_id
    вида "exec-<uuid>", а транскрипт — call_id вида "call_<...>" — разные
    пространства идентификаторов, свести их напрямую нельзя. turn_id — общий
    ключ. Тест — страж от возврата к попытке сверки по tool_use_id."""
    with open(FIXTURES / "hook_events.jsonl", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    failure_event = events[6]
    assert failure_event["hook_event_name"] == "PostToolUse"
    assert failure_event["turn_id"] == TURN_ID_FAILURE
    # tool_use_id хука не встречается в транскрипте вовсе
    with open(TRANSCRIPT, encoding="utf-8") as f:
        raw = f.read()
    assert failure_event["tool_use_id"] not in raw


def test_find_tool_error_missing_turn_id_returns_unknown_not_false():
    """Ни в коем случае не отчёт "провал" там, где не смогли проверить —
    иначе успешный вызов с пустым stdout стал бы ложным отказом."""
    info = transcript_codex.find_tool_error(TRANSCRIPT, "не-существующий-turn")
    assert info["ok"] is None
    assert info["error_text"] == ""


def test_finds_error_when_model_reports_plain_text_not_json():
    """Живая находка сверх docs/codex-facts.md (07.08.2026, при подготовке
    этой задачи): формат блока output — не протокол, а то, что САМА модель
    написала в JS-коде вызова exec. На одном живом прогоне модель выдала
    просто строку "exit_code=6" (`text("exit_code=" + r.exit_code)`) —
    НЕ валидный JSON. Файл — настоящий живой транскрипт отдельного прогона
    (не тот же, что TRANSCRIPT выше): echo (успех) + exit 6 (сбой) в одной
    сессии, tests/fixtures/codex/rollout-...019fdd33-e348....jsonl."""
    plain_text_transcript = str(
        FIXTURES / "rollout-2026-08-07T21-09-53-019fdd33-e348-72a0-a8a2-dd69f46619e4.jsonl"
    )
    info = transcript_codex.find_tool_error(plain_text_transcript, "019fdd34-4ab7-7820-8edf-b00b483538d1")
    assert info["ok"] is False
    assert info["exit_code"] == 6


def test_find_tool_error_retries_when_transcript_not_yet_flushed(monkeypatch):
    """Живая находка сверх docs/codex-facts.md: между хуком PostToolUse и
    тем, как Codex дописывает custom_tool_call_output в файл на диске, есть
    гонка (измерено живьём при подготовке этой задачи — см. комментарий над
    find_tool_error() в lib/transcript_codex.py). Без повтора первая же
    попытка вернула бы "не знаю" для только что случившегося сбоя — то есть
    именно в горячем случае, ради которого колонка и существует."""
    calls = []
    real_scan = transcript_codex._scan_for_tool_output

    def flaky_scan(path, turn_id):
        calls.append(1)
        if len(calls) < 2:
            return None  # как будто Codex ещё не дописал запись
        return real_scan(path, turn_id)

    sleeps = []
    monkeypatch.setattr(transcript_codex, "_scan_for_tool_output", flaky_scan)
    monkeypatch.setattr(transcript_codex.time, "sleep", lambda s: sleeps.append(s))

    info = transcript_codex.find_tool_error(TRANSCRIPT, TURN_ID_FAILURE)

    assert len(calls) == 2
    assert sleeps == [transcript_codex._LOOKUP_RETRY_DELAY_S]
    assert info["ok"] is False
    assert info["exit_code"] == 9


def test_find_tool_error_gives_up_after_max_retries(monkeypatch):
    calls = []
    monkeypatch.setattr(transcript_codex, "_scan_for_tool_output",
                         lambda path, turn_id: calls.append(1) or None)
    monkeypatch.setattr(transcript_codex.time, "sleep", lambda s: None)

    info = transcript_codex.find_tool_error(TRANSCRIPT, TURN_ID_FAILURE)

    assert len(calls) == transcript_codex._LOOKUP_RETRY_ATTEMPTS
    assert info == {"exit_code": None, "error_text": "", "ok": None}


def test_find_tool_error_does_not_retry_when_record_found_but_no_exit_code(monkeypatch):
    """Другая причина "не знаю", чем гонка с диском: запись УЖЕ есть, но
    модель не упомянула exit_code в своём отчёте (см. TURN_ID_SUCCESS,
    test_successful_tool_call_without_exit_code_marker_is_unknown_not_ok) —
    повторные попытки её не найдут, поэтому их не делаем."""
    calls = []
    monkeypatch.setattr(transcript_codex.time, "sleep",
                         lambda s: calls.append(s))

    transcript_codex.find_tool_error(TRANSCRIPT, TURN_ID_SUCCESS)

    assert calls == []  # ни одной паузы — запись нашлась с первой попытки


def test_find_tool_error_without_turn_id_or_path_is_not_fatal():
    assert transcript_codex.find_tool_error("", None) == {
        "exit_code": None, "error_text": "", "ok": None,
    }
    assert transcript_codex.find_tool_error(TRANSCRIPT, None) == {
        "exit_code": None, "error_text": "", "ok": None,
    }
