#!/usr/bin/env python3
"""Хук шагов: пишет вызовы инструментов и промпты в sandbox.ai_usage_events.

Всегда завершается с кодом 0 — телеметрия не имеет права мешать работе.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import hook_payload  # noqa: E402
import hook_scope  # noqa: E402
import redact  # noqa: E402
import telemetry  # noqa: E402
import transcript_codex  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")
TRACKED = ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure")


def _mcp_server(tool_name):
    """mcp__clickhouse__run_query → clickhouse; нативный тул → пустая строка."""
    if not tool_name.startswith("mcp__"):
        return ""
    parts = tool_name.split("__")
    return parts[1] if len(parts) > 2 else ""


def _claude_outcome(event, norm, secrets):
    """Claude Code: PostToolUse при сбое инструмента не вызывается вовсе —
    единственный сигнал об ошибке — отдельное событие PostToolUseFailure,
    поле "error" (docs/codex-facts.md, раздел 3; lib/hook_payload.py уже
    прочитал его в norm["error_text"]). Claude Code даёт исход всегда
    ДЕТЕРМИНИРОВАННО — "unknown" тут в принципе не бывает: PostToolUse
    значит успех, PostToolUseFailure значит отказ, третьего не дано.

    Возвращает (outcome, error_text, duration_ms)."""
    outcome = "failed" if event == "PostToolUseFailure" else "ok"
    error_text = redact.redact(norm["error_text"], secrets)[:2000]
    return outcome, error_text, norm["duration_ms"]


def _codex_outcome(payload, secrets):
    """Codex, событие PostToolUse (вызывающий код уже проверил тип события
    — см. build_row(), ревью-находка 1). Текста ошибки/кода выхода в самом
    hook payload нет никогда (docs/codex-facts.md, раздел 3) — достаём из
    транскрипта по turn_id (lib/transcript_codex.find_tool_error(); там же
    — почему по turn_id, а не по tool_use_id: это разные, непересекающиеся
    id).

    Три исхода, не два: "failed" — транскрипт подтвердил ненулевой код
    выхода; "ok" — подтвердил нулевой; "unknown" — не удалось прочитать
    транскрипт вовремя (гонка хук/запись на диск, см. отчёт задачи и
    lib/transcript_codex.py) или модель не упомянула exit_code в своём
    ответе. "unknown" — ОТДЕЛЬНОЕ состояние, не синоним успеха (ревью-
    находка 3): раньше оно писалось как ok=1/error_text="", неотличимо от
    настоящего успеха, и метрика "где падает" была смещена в оптимистичную
    сторону, никак не помечая, что часть строк — не про надёжность, а про
    то, что мы не успели проверить.

    duration_ms у Codex не приходит НИКОГДА — это не битое значение, поля
    нет в схеме события в принципе (lib/hook_payload.py). Схема колонки
    duration_ms в sandbox.ai_usage_events — UInt32, не Nullable (менять её
    вне рамок этой задачи — меняется только признак движка и исход), поэтому
    здесь пишем 0 осознанно и в отличие от Claude Code это не деградация
    битого значения, а единственный доступный способ отразить «это поле
    Codex не передаёт» в существующей нечисловой-NULL колонке; отличить
    такие строки от настоящих «уложились в 0 мс» можно по engine == 'codex'.

    Возвращает (outcome, error_text, duration_ms)."""
    turn_id = payload.get("turn_id")
    info = transcript_codex.find_tool_error(
        payload.get("transcript_path", ""), turn_id
    )
    if info["ok"] is False:
        return "failed", redact.redact(info["error_text"], secrets)[:2000], 0
    if info["ok"] is True:
        return "ok", "", 0
    return "unknown", "", 0


def _event_outcome(event, payload, norm, secrets):
    """Единая точка, где решается исход строки — (outcome, error_text,
    duration_ms). outcome — одно из "ok"/"failed"/"unknown".

    РЕВЬЮ-НАХОДКА 1 (критично, исправлено): раньше _codex_ok_and_error()
    вызывался безусловно для ЛЮБОГО отслеживаемого события Codex, включая
    UserPromptSubmit — а не только для PostToolUse, для которого он и
    писан (собственный докстринг это утверждал, код — расходился). У
    UserPromptSubmit тот же turn_id, что и у следующего за ним вызова
    инструмента, но в момент отправки промпта сам ход ещё не начался —
    записи о вызове в транскрипте физически быть не может. Поэтому все три
    (тогда) попытки retry отрабатывали вхолостую ГАРАНТИРОВАННО, а не
    иногда: измерено ревьюером — 322 мс на каждое событие вместо долей
    миллисекунды, то есть треть секунды задержки на каждом промпте
    аналитика в Codex безо всякого шанса что-то найти. Теперь поиск в
    транскрипте запускается СТРОГО при event == "PostToolUse"."""
    engine = norm["engine"]

    if engine == hook_payload.ENGINE_UNKNOWN:
        # Не опознали движок (см. lib/hook_payload.detect_engine(), ревью-
        # находка 4) — не пытаемся применить логику ни одного из двух
        # известных движков: это была бы та же угадайка, только на уровень
        # ниже. Честно "не знаем" и про исход тоже.
        return "unknown", "", 0

    if engine == hook_payload.ENGINE_CLAUDE:
        return _claude_outcome(event, norm, secrets)

    # engine == ENGINE_CODEX
    if event != "PostToolUse":
        # UserPromptSubmit и подобные — тул не вызывался, разбирать
        # транскрипт не на что (и не нужно, см. докстринг выше).
        return "ok", "", 0
    return _codex_outcome(payload, secrets)


def build_row(payload, secrets):
    event = payload.get("hook_event_name", "")
    if event not in TRACKED:
        return None

    norm = hook_payload.normalize(payload)
    tool_name = norm["tool_name"]

    outcome, error_text, duration_ms = _event_outcome(event, payload, norm, secrets)
    # ok сохранён для обратной совместимости с существующими запросами:
    # 0 означает ТОЛЬКО подтверждённый отказ, как и раньше для Claude Code
    # (там третьего состояния не бывает). Для новых запросов, которым нужно
    # отличать "не удалось проверить" от настоящего успеха (ревью-находка
    # 3), — колонка outcome: 'ok'/'failed'/'unknown'. "Сколько вызовов
    # упало" — countIf(outcome = 'failed'); "какая доля непроверяема" —
    # countIf(outcome = 'unknown') — а не смешивать их через ok.
    ok = 0 if outcome == "failed" else 1

    return {
        "ts": telemetry.utc_now_str(milliseconds=True),
        "session_id": norm["session_id"],
        "user": os.environ.get("UZUM_USER", os.environ.get("USER", "")),
        "event_type": event,
        "tool_name": tool_name,
        "mcp_server": _mcp_server(tool_name),
        "duration_ms": duration_ms,
        "ok": ok,
        "outcome": outcome,
        "error_text": error_text,
        "engine": norm["engine"],
    }


def main():
    try:
        # Чужая сессия — выходим немедленно и молча, до чтения stdin и до
        # любого обращения к диску. В $CODEX_HOME/hooks.json этот скрипт
        # прописан абсолютным путём, а сам файл — один на ВСЕ проекты
        # аналитика, поэтому нас запустят и в чужом проекте; телеметрия чужой
        # работы нам не нужна, а ненулевой код возврата у Codex блокирует
        # промпт целиком (lib/hook_scope.py, docs/codex-facts.md, раздел 11).
        #
        # ВНУТРИ try, а не перед ним (находка повторного ревью): обещание из
        # заголовка файла — «всегда завершается с кодом 0» — должно
        # распространяться и на саму проверку. Сама она исключений не бросает,
        # но полагаться на это как на единственную защиту нельзя: любой путь
        # с ненулевым кодом на UserPromptSubmit — это `Blocked`, то есть
        # молчащая сессия Codex вместо ответа.
        if not hook_scope.session_is_ours():
            return 0
        payload = json.load(sys.stdin)
        # Файл секретов трогаем только для отслеживаемых событий — для
        # остальных (их большинство: хук дёргается почти на 30 типов
        # событий Claude Code, из них пишем 3) build_row всё равно вернёт
        # None, и чтение с диска на каждый шаг было бы лишней задержкой.
        if payload.get("hook_event_name", "") not in TRACKED:
            return 0
        row = build_row(payload, redact.load_secret_values(SECRETS_PATH))
        if row:
            # write() — только локально, файл в очереди рядом (доли мс).
            # Отправку в ClickHouse забирает отвязанный процесс, который
            # ничего не держит и ничего не ждёт: этот хук висит на КАЖДОМ
            # вызове инструмента, и синхронный POST отсюда стоил аналитику
            # 283-423 мс на шаг, а при моргнувшей сети — 4 секунды (замер,
            # см. докстринг telemetry.write). Строка не потеряется, даже
            # если отправка не удастся: она уже на диске.
            telemetry.write("ai_usage_events", row)
            telemetry.flush_in_background()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
