#!/usr/bin/env python3
"""Хуки сессии.

SessionStart — отправляет накопленную очередь и запоминает время старта.
SessionEnd   — собирает агрегаты, маскирует транскрипт и пишет строку в sessions.
Всегда завершается с кодом 0.
"""
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import redact  # noqa: E402
import telemetry  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")
STATE_DIR = os.environ.get("UZUM_STATE_DIR", os.path.expanduser("~/.local/state/uzum-ai"))

# Известные префиксы Jira-проектов, с которыми реально работает команда.
# Дополнять по мере появления новых проектов. Без этого списка регэксп вида
# \b[A-Z][A-Z0-9]+-\d+\b матчит любой "код" в тексте — UTF-8, ISO-8601,
# HTTP-404 — и настоящий номер задачи теряется, если такой мусор встретится
# в транскрипте раньше.
JIRA_PROJECT_PREFIXES = ("OE", "MAD", "DATA")
JIRA_KEY_RE = re.compile(
    r"\b((?:%s)-\d+)\b" % "|".join(re.escape(p) for p in JIRA_PROJECT_PREFIXES)
)

# Потолок на объём читаемого транскрипта (в байтах исходного файла). Если
# файл больше — читаем только хвост, он важнее начала: там актуальный
# контекст сессии. Без потолка read_transcript тратит время и память сразу в
# нескольких местах на больших сессиях: readlines() всего файла, "".join()
# — вторая полная копия, и redact.redact(), который делает ещё по копии на
# каждый известный секрет (в secrets.env их может быть десяток-два). Таймаут
# хука — 30с, и внешний try/except в main() от таймаута не спасает. 5 МБ —
# это ещё доли секунды и десятки МБ памяти даже при паре десятков секретов,
# а транскрипт такого размера — это уже многочасовая аномальная сессия, а не
# обычный рабочий день аналитика.
TRANSCRIPT_MAX_BYTES = 5_000_000


def _started_at_path(session_id):
    return os.path.join(STATE_DIR, "started-%s" % session_id)


def _to_int(value):
    """Безопасно привести значение из транскрипта к int: битое значение
    (не число, список, объект) не должно ронять разбор всей строки."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _content_text(content):
    """Текст из поля content сообщения: оно бывает строкой либо списком
    блоков вида {"type": "text", "text": ...}."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


def _read_transcript_bytes(path):
    """Прочитать байты транскрипта, при необходимости — только хвост.
    Возвращает (bytes, исходный_размер, обрезан_ли)."""
    size = os.path.getsize(path)
    truncated = size > TRANSCRIPT_MAX_BYTES
    with open(path, "rb") as f:
        if truncated:
            f.seek(size - TRANSCRIPT_MAX_BYTES)
            f.readline()  # отбрасываем обрезанную по границе первую строку
        raw = f.read()
    return raw, size, truncated


def read_transcript(path, secrets):
    """Прочитать JSONL-лог сессии: замаскированный текст и агрегаты."""
    agg = {"n_prompts": 0, "n_tools": 0, "tokens_in": 0, "tokens_out": 0,
           "tokens_cache": 0, "skills_used": [], "_user_text": ""}
    try:
        raw_bytes, original_size, truncated = _read_transcript_bytes(path)
    except OSError:
        return "", agg

    # errors="replace": хвост после обрезки мог начаться посреди
    # многобайтового символа — одна испорченная строка не должна ронять
    # чтение остальных.
    raw_lines = raw_bytes.decode("utf-8", errors="replace").splitlines(keepends=True)

    user_text_parts = []
    skills_used = set()
    for line in raw_lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        # Строка транскрипта может быть валидным JSON, но не объектом
        # (список, null, число, строка) — тогда item.get(...) ниже упал бы
        # с AttributeError. Пропускаем такие строки, не роняя всю сессию.
        if not isinstance(item, dict):
            continue

        is_user = item.get("type") == "user"
        if is_user:
            agg["n_prompts"] += 1

        message = item.get("message")
        if not isinstance(message, dict):
            continue

        if is_user:
            user_text_parts.append(_content_text(message.get("content")))

        usage = message.get("usage")
        if isinstance(usage, dict):
            agg["tokens_in"] += _to_int(usage.get("input_tokens"))
            agg["tokens_out"] += _to_int(usage.get("output_tokens"))
            agg["tokens_cache"] += _to_int(usage.get("cache_read_input_tokens"))

        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                agg["n_tools"] += 1
                # Вызов скилла — запись tool_use с именем "Skill" и
                # параметром {"skill": "имя"} в input. Опираемся на
                # структуру, а не угадываем скиллы регэкспом по тексту:
                # текст транскрипта полон путей файловой системы и URL
                # (/Users/..., /api/..., ...) — они не имеют отношения к
                # скиллам, но выглядят похоже под "/слово".
                if block.get("name") == "Skill":
                    block_input = block.get("input")
                    skill = block_input.get("skill") if isinstance(block_input, dict) else None
                    if isinstance(skill, str) and skill:
                        skills_used.add(skill)

    agg["skills_used"] = sorted(skills_used)
    agg["_user_text"] = "\n".join(user_text_parts)

    text = "".join(raw_lines)
    if truncated:
        marker = (
            "[ТРАНСКРИПТ ОБРЕЗАН: показан хвост, исходный размер %d байт, "
            "потолок чтения %d байт]\n" % (original_size, TRANSCRIPT_MAX_BYTES)
        )
        text = marker + text
    return redact.redact(text, secrets), agg


def find_jira_key(text):
    """Первый ключ задачи среди известных префиксов проектов
    (JIRA_PROJECT_PREFIXES). Ничего не нашли — пустая строка, это нормально."""
    match = JIRA_KEY_RE.search(text or "")
    return match.group(1) if match else ""


def _repo_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def build_session_row(payload, secrets):
    session_id = payload.get("session_id", "")
    text, agg = read_transcript(payload.get("transcript_path", ""), secrets)
    now = datetime.datetime.now(datetime.timezone.utc)
    started = now
    try:
        with open(_started_at_path(session_id), encoding="utf-8") as f:
            started = datetime.datetime.fromisoformat(f.read().strip())
    except Exception:
        pass
    # Ключ задачи ищем в первую очередь среди сообщений пользователя: иначе
    # первое совпадение по всему тексту транскрипта может оказаться
    # упоминанием какой-то задачи в рассуждениях ассистента, а не тем, с чем
    # реально пришёл аналитик. Только если в сообщениях пользователя ключа
    # нет, ищем по всему тексту.
    jira_key = find_jira_key(agg.get("_user_text", "")) or find_jira_key(text)
    return {
        "session_id": session_id,
        "user": os.environ.get("UZUM_USER", os.environ.get("USER", "")),
        # Единственное место, где определён формат времени для колонок
        # DateTime('UTC') — telemetry.format_utc()/utc_now_str(). "started"
        # и "now" — один и тот же объект "now", что используется и в
        # duration_s, поэтому ended_at форматируем через format_utc(now), а
        # не отдельным вызовом utc_now_str() (это был бы лишний, чуть более
        # поздний вызов часов).
        "started_at": telemetry.format_utc(started),
        "ended_at": telemetry.format_utc(now),
        "duration_s": max(0, int((now - started).total_seconds())),
        "jira_key": jira_key,
        "skills_used": agg["skills_used"],
        "n_prompts": agg["n_prompts"],
        "n_tools": agg["n_tools"],
        "tokens_in": agg["tokens_in"],
        "tokens_out": agg["tokens_out"],
        "tokens_cache": agg["tokens_cache"],
        "cost_usd": 0,
        "repo_sha": _repo_sha(),
        "end_reason": payload.get("reason", ""),
        "transcript": text,
    }


def main():
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        if event == "SessionStart":
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(_started_at_path(payload.get("session_id", "")), "w",
                      encoding="utf-8") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
            telemetry.flush()
        elif event == "SessionEnd":
            secrets = redact.load_secret_values(SECRETS_PATH)
            session_id = payload.get("session_id", "")
            telemetry.write("ai_usage_sessions", build_session_row(payload, secrets))
            # Данные файла-метки уже перенесены в строку сессии (отправлена
            # или, при недоступности ClickHouse, ушла в очередь telemetry —
            # write() никогда не теряет строку молча). Сам файл больше не
            # нужен: не убирать его — значит копить в STATE_DIR по файлу на
            # каждую сессию бессрочно.
            try:
                os.remove(_started_at_path(session_id))
            except OSError:
                pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
