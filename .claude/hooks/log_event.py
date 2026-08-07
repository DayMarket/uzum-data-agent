#!/usr/bin/env python3
"""Хук шагов: пишет вызовы инструментов и промпты в sandbox.ai_usage_events.

Всегда завершается с кодом 0 — телеметрия не имеет права мешать работе.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import hook_payload  # noqa: E402
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


def _claude_ok_and_error(event, norm, secrets):
    """Claude Code: PostToolUse при сбое инструмента не вызывается вовсе —
    единственный сигнал об ошибке — отдельное событие PostToolUseFailure,
    поле "error" (docs/codex-facts.md, раздел 3; lib/hook_payload.py уже
    прочитал его в norm["error_text"])."""
    ok = 0 if event == "PostToolUseFailure" else 1
    error_text = redact.redact(norm["error_text"], secrets)[:2000]
    return ok, error_text, norm["duration_ms"]


def _codex_ok_and_error(payload, secrets):
    """Codex: PostToolUse срабатывает и при успехе, и при сбое (в отличие от
    Claude Code), но текста ошибки/кода выхода в самом hook payload нет
    никогда (docs/codex-facts.md, раздел 3) — достаём из транскрипта по
    turn_id (lib/transcript_codex.find_tool_error(); там же — почему по
    turn_id, а не по tool_use_id: это разные, непересекающиеся id).

    duration_ms у Codex не приходит НИКОГДА — это не битое значение, поля
    нет в схеме события в принципе (lib/hook_payload.py). Схема колонки
    duration_ms в sandbox.ai_usage_events — UInt32, не Nullable (менять её
    вне рамок этой задачи — меняется только признак движка), поэтому здесь
    пишем 0 осознанно и в отличие от Claude Code это не деградация битого
    значения, а единственный доступный способ отразить «это поле Codex не
    передаёт» в существующей нечисловой-NULL колонке; отличить такие строки
    от настоящих «уложились в 0 мс» можно по engine == 'codex'.
    """
    turn_id = payload.get("turn_id")
    info = transcript_codex.find_tool_error(
        payload.get("transcript_path", ""), turn_id
    )
    if info["ok"] is False:
        ok = 0
        error_text = redact.redact(info["error_text"], secrets)[:2000]
    else:
        # ok is True, либо None (не удалось разобрать транскрипт) — в обоих
        # случаях НЕ отмечаем строку как отказ: у нас либо подтверждённый
        # успех, либо нет доказательств отказа, а не подставлять отказ там,
        # где мы просто не смогли проверить, — то же правило "не угадывай",
        # что и для остальных полей в этом модуле.
        ok = 1
        error_text = ""
    return ok, error_text, 0


def build_row(payload, secrets):
    event = payload.get("hook_event_name", "")
    if event not in TRACKED:
        return None

    norm = hook_payload.normalize(payload)
    tool_name = norm["tool_name"]

    if norm["engine"] == hook_payload.ENGINE_CODEX:
        ok, error_text, duration_ms = _codex_ok_and_error(payload, secrets)
    else:
        ok, error_text, duration_ms = _claude_ok_and_error(event, norm, secrets)

    return {
        "ts": telemetry.utc_now_str(milliseconds=True),
        "session_id": norm["session_id"],
        "user": os.environ.get("UZUM_USER", os.environ.get("USER", "")),
        "event_type": event,
        "tool_name": tool_name,
        "mcp_server": _mcp_server(tool_name),
        "duration_ms": duration_ms,
        "ok": ok,
        "error_text": error_text,
        "engine": norm["engine"],
    }


def main():
    try:
        payload = json.load(sys.stdin)
        # Файл секретов трогаем только для отслеживаемых событий — для
        # остальных (их большинство: хук дёргается почти на 30 типов
        # событий Claude Code, из них пишем 3) build_row всё равно вернёт
        # None, и чтение с диска на каждый шаг было бы лишней задержкой.
        if payload.get("hook_event_name", "") not in TRACKED:
            return 0
        row = build_row(payload, redact.load_secret_values(SECRETS_PATH))
        if row:
            telemetry.write("ai_usage_events", row)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
