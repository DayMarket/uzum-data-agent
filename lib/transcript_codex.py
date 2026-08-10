# lib/transcript_codex.py
"""Второй разбор транскрипта — для Codex. Рядом с существующим разбором
Claude Code (.claude/hooks/log_session.py:read_transcript), не вместо него:
это осознанно два отдельных парсера с общим только на уровне "это JSONL,
читаем построчно" — см. docs/codex-facts.md, раздел 6 "Формат транскрипта":

    "Формат — разный настолько, что общий парсер транскрипта переиспользовать
    нельзя, только общий факт «это JSONL, читаем построчно»... Значит разбор
    транскрипта под sandbox.ai_usage_sessions придётся писать двумя
    отдельными парсерами с общим только на уровне «прочитать JSONL
    построчно», а не одним общим модулем с двумя конфигурациями полей."

Структура транскрипта Codex (docs/codex-facts.md, раздел 6, живой запуск
07.08.2026, файл `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-...jsonl`):
каждая строка — {"timestamp": ..., "type": <тип>, "payload": {...}}, где
type один из: session_meta, event_msg, response_item, world_state,
turn_context. Наблюдённые вложенные типы, использованные ниже:

  - event_msg / payload.type == "user_message" — один настоящий промпт
    человека на запись, с готовым текстом в payload["message"] (строка, не
    блоки контента, как у Claude Code). В отличие от response_item/message
    с role == "user", среди которых встречаются служебные вставки
    (<recommended_plugins> и т.п.) — их role тоже "user", но событие
    user_message на них не создаётся. Проверено на живом транскрипте с
    двумя реальными промптами и одной служебной user-вставкой: role=="user"
    даёт 3 совпадения, event_msg/user_message — ровно 2, то есть правильно.

    ВАЖНО (найдено на живых данных 10.08.2026, см. docs/codex-facts.md,
    раздел 6): такие записи пишет ТОЛЬКО `codex exec`. У интерактивного
    TUI — того самого режима, в котором работает аналитик, — промпт лежит
    иначе (см. следующий пункт). Одна и та же версия codex (0.147.0), два
    разных формата, различаются по session_meta.originator:
    "codex_exec" против "codex-tui".

  - event_msg / payload.type == "item_completed", payload.item.type ==
    "UserMessage" — промпт человека в ИНТЕРАКТИВНОМ режиме (TUI). Текст —
    не строка, а список блоков в item["content"]: {"type": "text",
    "text": ...} плюс, если промпт вызвал скилл, ещё и {"type": "skill",
    "name": ..., "path": ...}.

    Именно из-за этой ветки телеметрия показывала n_prompts = 0 у всех
    сессий Codex при непустых n_tools и токенах: разбор писался и
    проверялся по транскриптам `codex exec` (все живые проверки этого
    репозитория до сих пор шли через exec), а аналитик работает в TUI, где
    записи user_message не бывает вовсе.

    Считаем только item.type == "UserMessage". Промпты, вставленные
    хуками (у нас это сообщение SessionStart про git pull), — отдельный
    тип item'а "HookPrompt" (перечень типов виден в самом бинаре Codex
    рядом с UserMessage: UserMessage, HookPrompt, AgentMessage, Reasoning,
    DynamicToolCall, …), поэтому в n_prompts они не попадают, и это
    структурная гарантия, а не удача.

  - response_item / payload.type == "custom_tool_call" — вызов инструмента,
    отдельная запись верхнего уровня (не блок внутри message.content, как у
    Claude Code). Один вызов — одна запись, дедупликация не нужна (раздел 6,
    п.1: у Codex дедуплицировать нечего, токены и так агрегированы).

  - event_msg / payload.type == "token_count" — готовые АГРЕГИРОВАННЫЕ (не
    инкрементальные) токены на каждом шаге, total_token_usage.*. Берём
    последнюю такую запись в файле, а не суммируем все — иначе посчитали бы
    в разы больше (проверено на живом файле: 4 записи token_count с
    total_tokens 14307 → 28863 → 43592 → 58376, это один и тот же
    нарастающий счётчик, а не четыре независимых куска).

  - response_item / payload.type == "custom_tool_call_output" — результат
    вызова, отдельная запись. Ошибка/код выхода лежат тут — см.
    find_tool_error() ниже и docs/codex-facts.md, раздел 3.

Важная находка, СВЕРХ того, что зафиксировано в docs/codex-facts.md
(добавлено при подготовке этой задачи, живым запуском 07.08.2026): у
hook-события PostToolUse `tool_use_id` (например "exec-a2a9d50c-...") и у
записи custom_tool_call/custom_tool_call_output в транскрипте `call_id`
(например "call_AOeujqwtrityPNQZSMhfwUW0") — это РАЗНЫЕ идентификаторы из
непересекающихся пространств, сверка один-в-один по ним невозможна. Общий
ключ между hook payload и транскриптом — turn_id: он совпадает дословно и
в payload["turn_id"] хука, и в
response_item.payload.internal_chat_message_metadata_passthrough.turn_id
записи транскрипта. Проверено на реальной паре hook-событие/транскрипт
(один и тот же turn_id "019fdd22-355c-7721-a656-5e7416f795f2" в обоих
местах). Если в одном turn несколько вызовов инструментов, find_tool_error
не может различить их по turn_id — берёт последнюю запись с этим turn_id
(ближайшую по времени к моменту, когда сработал хук) и не притворяется, что
это гарантированно тот же самый вызов; это задокументированное ограничение,
а не тихая неточность.
"""
import json
import os
import re
import time

import redact

# Тот же порядок величины, что и у Claude Code (log_session.py,
# TRANSCRIPT_MAX_BYTES) — для полного разбора сессии на SessionEnd: большой
# транскрипт читаем только хвостом, чтобы не упереться в таймаут хука (30с)
# и не тратить лишнюю память на redact.redact() по всему файлу.
TRANSCRIPT_MAX_BYTES = 5_000_000

# Для find_tool_error() потолок меньше и это осознанно: её вызывают на
# КАЖДЫЙ PostToolUse (в отличие от read_transcript(), которая — раз в
# сессию, на SessionEnd), а нужная запись всегда лежит у самого конца
# файла — транскрипт дописывается только что, хук стреляет сразу после
# завершения инструмента. 300 КБ — это с большим запасом больше, чем один
# ход разговора (skills_instructions и подобные крупные блоки пишутся один
# раз в начале файла, а не на каждый turn).
TAIL_LOOKUP_MAX_BYTES = 300_000

# Лучшее из доступного (не структурная гарантия, как у Claude Code, где
# вызов скилла — tool_use с name=="Skill"): у Codex своего события "вызвал
# скилл" нет, скилл читается тем же exec/shell-вызовом, что и любой другой
# файл (docs/codex-facts.md, раздел 5 — Codex сам делает
# `sed -n '1,240p' .agents/skills/<имя>/SKILL.md`). Ищем этот путь в
# аргументах вызова инструмента. Может пропустить скилл, прочитанный другим
# способом (не через sed/cat/иной шаблон пути) — это явное ограничение, а не
# попытка выдать эвристику за структурный факт.
_SKILL_PATH_RE = re.compile(r"\.agents/skills/([^/\s\"']+)/SKILL\.md")

# Резерв для _extract_exit_info(), когда блок вывода — не валидный JSON:
# на живом образце модель сама написала `text("exit_code=" + r.exit_code)`,
# что даёт голую строку вида "exit_code=6", без всякой JSON-обёртки.
_EXIT_CODE_RE = re.compile(r'exit_code["\s]*[:=]["\s]*(-?\d+)')


def _read_tail_bytes(path, max_bytes):
    """Прочитать байты файла, при необходимости — только хвост.
    Возвращает (bytes, обрезан_ли). Никогда не бросает исключений наружу
    из-за отсутствующего/недоступного файла."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return b"", False
    truncated = size > max_bytes
    try:
        with open(path, "rb") as f:
            if truncated:
                f.seek(size - max_bytes)
                f.readline()  # отбрасываем обрезанную по границе первую строку
            raw = f.read()
    except OSError:
        return b"", False
    return raw, truncated


def _iter_records(raw_bytes):
    """Построчно распарсить JSONL, пропуская битые/не-объектные строки —
    одна испорченная строка не должна ронять разбор всего транскрипта."""
    text = raw_bytes.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            yield item


def _user_message_text(content):
    """Текст промпта из item["content"] интерактивной записи UserMessage.

    Список блоков; берём только текстовые. Рядом с ними встречается блок
    {"type": "skill", "name": ..., "path": ...} — это не текст человека, а
    разметка вызванного скилла, в _user_text ей делать нечего (по этому
    тексту ищется ключ задачи Jira). Тип блока сверяем без учёта регистра:
    у UserMessage он "text", у соседнего AgentMessage — "Text", и полагаться
    на то, что дальше так и останется, не на чем."""
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if str(block.get("type", "")).lower() != "text":
            continue
        text = block.get("text")
        if isinstance(text, str) and text:
            parts.append(text)
    return "\n".join(parts)


def _extract_exit_info(output):
    """Разобрать payload["output"] записи custom_tool_call_output.

    ВАЖНАЯ НАХОДКА, СВЕРХ docs/codex-facts.md (обнаружено при подготовке
    этой задачи, живым запуском 07.08.2026): этот блок — НЕ фиксированная
    схема протокола, а текст, который модель САМА формирует своим JS-кодом
    внутри custom_tool_call.input (Codex открывает инструмент exec как
    `const r = await tools.exec_command({...}); text(...)` — именно
    "..." пишет модель). На двух реальных вызовах в одной и той же сессии
    (tests/fixtures/codex/) модель написала РАЗНЫЙ код: для простого успеха
    — `text(r.output)` (голый stdout, без упоминания exit_code), а для
    вызова, который я явно попросил "зафиксировать результат" — уже
    `text(JSON.stringify({exit_code:r.exit_code,output:r.output}))`. То
    есть наличие exit_code в выводе — не гарантия протокола, а то, что
    модель посчитала нужным сообщить в данном конкретном ответе. На
    практике падения чаще явно объясняются моделью (значит exit_code чаще
    находится), а тихие успехи — реже, но это наблюдение по двум образцам,
    не доказанная закономерность.

    На живых образцах (docs/codex-facts.md, раздел 3, и живая проверка при
    подготовке этой задачи) видел ТРИ РАЗНЫХ формата одного и того же по
    смыслу текста, потому что это не фиксированная схема, а результат
    JS-кода, который каждый раз заново пишет модель (см. докстринг выше):
      1. {"chunk_id":...,"exit_code":8,...,"output":"..."} — модель обернула
         весь объект результата в JSON целиком (`text(r)`).
      2. {"exit_code":9,"output":""} — модель сама собрала укороченный JSON
         (`text(JSON.stringify({exit_code:r.exit_code,output:r.output}))`).
      3. "exit_code=6" — модель просто склеила строку (`text("exit_code="
         + r.exit_code)`), это уже НЕ валидный JSON вовсе.
    Поэтому сначала пробуем распарсить блок как JSON с ключом exit_code
    (форматы 1 и 2 — оттуда же берём "output", если он есть); если это не
    JSON — ищем regexp'ом голое упоминание exit_code=N/"exit_code": N в
    сыром тексте (формат 3). Если не нашли ни там, ни там — честно
    возвращаем "не нашли", а не подставляем 0 или угадываем успех: модель
    может вообще не упомянуть exit_code, особенно на настоящем успехе
    (голый stdout без всякой обёртки — тоже реальный, живой случай)."""
    if not isinstance(output, list):
        return None, ""
    for block in reversed(output):
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("exit_code"), int):
            return parsed["exit_code"], str(parsed.get("output", "") or "")
        match = _EXIT_CODE_RE.search(text)
        if match:
            return int(match.group(1)), text
    return None, ""


# КРУПНАЯ ЖИВАЯ НАХОДКА сверх docs/codex-facts.md (07.08.2026, при
# подготовке этой задачи; НЕ окончательно решена — см. отчёт задачи, раздел
# "сомнения"). Между тем, как срабатывает хук PostToolUse, и тем, как запись
# custom_tool_call_output для ЭТОГО ЖЕ вызова физически попадает в файл
# транскрипта на диске — есть гонка. Измерено живьём, end-to-end (реальный
# `codex exec`, реальные хуки этого репозитория, реальный mock ClickHouse —
# не юнит-тест), несколько раз подряд, разброс большой и НЕПРЕДСКАЗУЕМЫЙ:
# 146 мс, 369 мс, 1271 мс, ~2.0 с, ~4.5 с — на разных вызовах, в том числе
# на одиночных `codex exec` без каких-либо предыдущих ходов в сессии (то
# есть не объясняется одним лишь ростом контекста). Отдельно проверил и
# отверг гипотезу самоблокировки ("Codex пишет запись только ПОСЛЕ
# завершения хука, поэтому чем дольше ждём — тем позже она появится"):
# прогон с retry, обрезанным до одной попытки без паузы вообще, всё равно
# показал разрыв ~144 мс между вызовом и записью — то есть задержка не
# зависит от того, сколько сам хук ждал, она просто есть, её масштаб не
# предсказать заранее. Похоже на то, что Codex сериализует/дописывает
# записи транскрипта не синхронно с самим выполнением тула, а отдельным,
# не всегда быстрым внутренним шагом.
#
# ВАЖНО: find_tool_error() вызывается на КАЖДЫЙ PostToolUse Codex, а не
# только на подозрении на сбой (ok/error нельзя определить иначе — см.
# _codex_ok_and_error() в .claude/hooks/log_event.py) — то есть цена
# retry-бюджета ниже ложится на КАЖДЫЙ вызов инструмента, успешный тоже, а
# не только на падения. Поднимать бюджет до величин, покрывающих худшие
# измеренные разрывы (наблюдал ~2-4.5 с, см. выше), означало бы добавлять
# эти секунды к КАЖДОМУ шагу аналитика в Codex — это уже конфликт с
# глобальным правилом "хук не задерживает работу", а не мелкий компромисс.
# Выбор здесь — сознательно НЕБОЛЬШОЙ бюджет (3 попытки по 150 мс, до ~450
# мс сверху): ловит быстрый конец измеренного разброса (146-369 мс — так
# было в части живых прогонов), но заведомо пропускает медленный (наблюдал
# устойчиво и у 1.3, и у 2.0, и у 3.5, и у 4.5 секунд — в нескольких
# отдельных живых end-to-end прогонах, не единичный выброс). Итог, который
# нужно проговорить прямо, а не спрятать за цифрой таймаута: колонка "где
# падает" для Codex сегодня работает НЕ НАДЁЖНО — часть настоящих отказов
# в проверках этой задачи осталась с ok=1/error_text="" именно из-за этой
# гонки, задокументировано в отчёте задачи, раздел "сомнения". Это не
# гипотетический риск, а наблюдаемое поведение на реальных прогонах.
_LOOKUP_RETRY_ATTEMPTS = 3
_LOOKUP_RETRY_DELAY_S = 0.15


def _scan_for_tool_output(transcript_path, turn_id):
    """Одна попытка: прочитать хвост транскрипта и найти ПОСЛЕДНЮЮ запись
    custom_tool_call_output с этим turn_id. None, если такой записи в файле
    пока нет вовсе (см. _LOOKUP_RETRY_ATTEMPTS про то, почему "пока нет" —
    реальный, измеренный случай, а не гипотетический)."""
    raw, _truncated = _read_tail_bytes(transcript_path, TAIL_LOOKUP_MAX_BYTES)
    if not raw:
        return None
    match = None
    for item in _iter_records(raw):
        if item.get("type") != "response_item":
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict) or payload.get("type") != "custom_tool_call_output":
            continue
        meta = payload.get("internal_chat_message_metadata_passthrough")
        if isinstance(meta, dict) and meta.get("turn_id") == turn_id:
            match = payload  # берём последнюю подходящую — см. докстринг модуля выше
    return match


def find_tool_error(transcript_path, turn_id):
    """Достать текст ошибки и код выхода инструмента из транскрипта — в
    hook payload Codex их нет никогда (docs/codex-facts.md, раздел 3):
    PostToolUse у Codex срабатывает и при сбое, но tool_response в этом
    случае — пустая строка, без exit_code/is_error/stderr.

    Повторяет чтение до _LOOKUP_RETRY_ATTEMPTS раз с паузой
    _LOOKUP_RETRY_DELAY_S — см. комментарий там же про гонку между хуком и
    записью транскрипта на диск, измеренную живьём. Повтор — только пока
    запись с нужным turn_id вообще не найдена в файле; если запись УЖЕ
    есть, но в ней нет опознаваемого exit_code (модель не упомянула его в
    своём отчёте о вызове — см. _extract_exit_info()), повторные попытки не
    помогут и не делаются, это другая причина "не знаю".

    Возвращает dict {"exit_code": int|None, "error_text": str,
    "ok": True|False|None}. ok is None означает "не удалось определить" —
    это НЕ то же самое, что ok=True: вызывающий код (.claude/hooks/
    log_event.py) должен не отмечать такую строку как отказ, только потому
    что не смог её проверить, иначе успешный вызов с пустым stdout стал бы
    ложным "падением"."""
    if not turn_id or not transcript_path:
        return {"exit_code": None, "error_text": "", "ok": None}

    match = None
    for attempt in range(_LOOKUP_RETRY_ATTEMPTS):
        match = _scan_for_tool_output(transcript_path, turn_id)
        if match is not None:
            break
        if attempt < _LOOKUP_RETRY_ATTEMPTS - 1:
            time.sleep(_LOOKUP_RETRY_DELAY_S)

    if match is None:
        return {"exit_code": None, "error_text": "", "ok": None}

    exit_code, output_text = _extract_exit_info(match.get("output"))
    if exit_code is None:
        return {"exit_code": None, "error_text": "", "ok": None}
    if exit_code == 0:
        return {"exit_code": 0, "error_text": "", "ok": True}

    error_text = "exit code %d" % exit_code
    if output_text:
        error_text += ": " + output_text
    return {"exit_code": exit_code, "error_text": error_text, "ok": False}


def read_transcript(path, secrets):
    """Прочитать JSONL-транскрипт Codex целиком (с потолком по размеру, как
    у Claude Code — TRANSCRIPT_MAX_BYTES) — замаскированный текст и агрегаты.
    Тот же контракт возврата, что у .claude/hooks/log_session.py:
    read_transcript(): (text, agg), agg содержит n_prompts, n_tools,
    tokens_in, tokens_out, tokens_cache, skills_used, _user_text."""
    agg = {"n_prompts": 0, "n_tools": 0, "tokens_in": 0, "tokens_out": 0,
           "tokens_cache": 0, "skills_used": [], "_user_text": "",
           "_models": []}

    try:
        original_size = os.path.getsize(path)
    except OSError:
        return "", agg

    raw, truncated = _read_tail_bytes(path, TRANSCRIPT_MAX_BYTES)
    if not raw:
        return "", agg

    user_text_parts = []
    skills_used = set()
    last_token_usage = None  # берём последнюю запись token_count, не сумму

    for item in _iter_records(raw):
        item_type = item.get("type")
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_type = payload.get("type")

        if item_type == "event_msg" and payload_type == "user_message":
            # Промпт в режиме `codex exec` — см. докстринг модуля.
            agg["n_prompts"] += 1
            message = payload.get("message")
            if isinstance(message, str):
                user_text_parts.append(message)

        elif item_type == "event_msg" and payload_type == "item_completed":
            # Промпт в интерактивном режиме (TUI) — другая запись, другой
            # формат текста. Именно её отсутствие давало n_prompts = 0 у
            # живых сессий аналитиков. Обе ветки живут рядом, а не одна
            # вместо другой: exec никуда не делся (мастер установки
            # проверяет им доверие хукам), и оба формата встречаются на
            # одной и той же версии Codex. Ни на одном наблюдённом
            # транскрипте оба вида записей одновременно не встречались —
            # каждый режим пишет свой (проверено на четырёх живых файлах:
            # два exec, два TUI).
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "UserMessage":
                agg["n_prompts"] += 1
                text = _user_message_text(item.get("content"))
                if text:
                    user_text_parts.append(text)

        elif item_type == "event_msg" and payload_type == "token_count":
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if isinstance(usage, dict):
                last_token_usage = usage

        elif item_type == "turn_context":
            # Модель этого хода. Единственное место, где Codex её называет:
            # в session_meta ключа model НЕТ вовсе (там только
            # model_provider) — проверено на живых файлах 0.147.0. Пишем по
            # записи на ход, а какую из них считать моделью сессии, решает
            # один общий для обоих движков код (см. log_session.py).
            model = payload.get("model")
            if isinstance(model, str) and model:
                agg["_models"].append(model)

        elif item_type == "response_item" and payload_type == "custom_tool_call":
            agg["n_tools"] += 1
            command_text = payload.get("input")
            if isinstance(command_text, str):
                skill_match = _SKILL_PATH_RE.search(command_text)
                if skill_match:
                    skills_used.add(skill_match.group(1))

    if last_token_usage:
        def _to_int(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        agg["tokens_in"] = _to_int(last_token_usage.get("input_tokens"))
        agg["tokens_out"] = _to_int(last_token_usage.get("output_tokens"))
        # Кеш — тоже сумма чтения и создания, как у Claude Code (см.
        # .claude/hooks/log_session.py) — двух разных вещей, обе стоят денег.
        agg["tokens_cache"] = (
            _to_int(last_token_usage.get("cached_input_tokens"))
            + _to_int(last_token_usage.get("cache_write_input_tokens"))
        )

    agg["skills_used"] = sorted(skills_used)
    agg["_user_text"] = "\n".join(user_text_parts)

    text = raw.decode("utf-8", errors="replace")
    if truncated:
        marker = (
            "[ТРАНСКРИПТ ОБРЕЗАН: показан хвост, исходный размер %d байт, "
            "потолок чтения %d байт]\n" % (original_size, TRANSCRIPT_MAX_BYTES)
        )
        text = marker + text
    return redact.redact(text, secrets), agg
