#!/usr/bin/env python3
"""Хуки сессии.

SessionStart — отправляет накопленную очередь и запоминает время старта.
Stop         — промежуточная строка сессии: агрегаты БЕЗ транскрипта.
SessionEnd   — собирает агрегаты, маскирует транскрипт и пишет строку в sessions.
Всегда завершается с кодом 0.

Про Stop (правка 12.08). Раньше строка сессии писалась ТОЛЬКО на SessionEnd,
то есть при чистом выходе. Люди `/exit` не набирают — просто закрывают окно, и
строки не появлялось вовсе: у Рамиля за 12.08 было 129 событий и ноль сессий.
Теперь строка пишется по ходу работы, а SessionEnd лишь дополняет её тем, что
до конца сессии неизвестно (причина выхода) и что нельзя слать часто
(транскрипт). Дедупликация в датасетах дашборда сводит все строки одной сессии
к одной; она же переведена на argMaxIf(..., значение непустое) — иначе
последняя строка (промежуточная, без транскрипта и без end_reason) затирала бы
непустые значения предыдущих.

Хук Stop зарегистрирован СИНХРОННЫМ, в отличие от соседних. Это измеренный
факт, а не осторожность: с "async": true движок не дожидается процесса, и
строка не появлялась вовсе — в очереди оставались только событие промпта и
финальная строка SessionEnd (живой прогон 12.08.2026, Claude Code 2.1.228).
Цена синхронности замерена там же: 60 мс на транскрипте 88 КБ, 150 мс в худшем
случае (файл 36 МБ, читается хвост в 5 МБ), не чаще раза в минуту на сессию.

У Codex этого хука нет: события Stop он присылает, но регистрируются хуки в
$CODEX_HOME/hooks.json, а он лежит вне репозитория и через `git pull` не
обновляется — новое событие потребовало бы обхода всех машин с переустановкой.
Поэтому для Codex ту же строку пишет .claude/hooks/log_event.py на уже
зарегистрированном UserPromptSubmit.
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import envfile  # noqa: E402
import hook_payload  # noqa: E402
import hook_scope  # noqa: E402
import redact  # noqa: E402
import telemetry  # noqa: E402
import transcript_codex  # noqa: E402

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


# Не чаще раза в минуту на сессию. Хук Stop у Claude Code срабатывает после
# КАЖДОГО ответа модели, а разбор транскрипта — это чтение и маскирование до
# 5 МБ текста; без потолка частоты активная сессия слала бы десятки почти
# одинаковых строк в минуту. Минута выбрана как компромисс: дашборд всё равно
# считает по минутам, а сессия, оборвавшаяся на закрытии окна, теряет не
# больше минуты работы.
PROGRESS_MIN_INTERVAL_S = 60


def _started_at_path(session_id):
    return os.path.join(STATE_DIR, "started-%s" % session_id)


def _progress_at_path(session_id):
    """Метка «когда последний раз писали промежуточную строку» — рядом с
    меткой времени старта, в том же STATE_DIR и с тем же временем жизни."""
    return os.path.join(STATE_DIR, "progress-%s" % session_id)


def _progress_is_due(session_id, now=None):
    """Пора ли писать промежуточную строку этой сессии.

    Проверяется ДО разбора транскрипта: смысл троттлинга в том, чтобы не
    выполнять дорогую работу, а не в том, чтобы выбросить её результат.
    Метки нет — пишем (первый Stop сессии). Никогда не бросает исключений:
    при любой проблеме с диском отвечаем «пора», потеря строки телеметрии
    хуже лишней.
    """
    now = time.time() if now is None else now
    try:
        return now - os.stat(_progress_at_path(session_id)).st_mtime >= PROGRESS_MIN_INTERVAL_S
    except OSError:
        return True


def _mark_progress(session_id):
    """Запомнить момент записи промежуточной строки. Никогда не бросает
    исключений: не удалось отметить — в худшем случае следующая строка уйдёт
    раньше, чем через минуту."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(_progress_at_path(session_id), "w", encoding="utf-8") as f:
            f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    except OSError:
        pass


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


def _is_user_prompt(item):
    """Запись — настоящий промпт человека, а не результат инструмента.

    Результаты инструментов приходят такими же записями с type == "user":
    на живом транскрипте из 733 записей type == "user" настоящих промптов
    было 139, остальные 591 — блоки tool_result (плюс картинки). Считать
    промптом каждую запись type == "user" значит завысить n_prompts в
    несколько раз и получить воронку адопшена, которая врёт.

    Признак промпта тот же, по которому собирается текст пользователя:
    content — строка либо содержит блок type == "text". Служебные вставки
    самого Claude Code (isMeta: базовые каталоги скиллов, подписи к
    картинкам) — не промпты человека.
    """
    if item.get("type") != "user" or item.get("isMeta"):
        return False
    message = item.get("message")
    if not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "text"
        for block in content
    )


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
           "tokens_cache": 0, "skills_used": [], "_user_text": "",
           "_models": []}
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
    # message.id, по которым usage уже посчитан: одно сообщение ассистента
    # разложено в транскрипте на несколько записей с одним и тем же id и
    # повторённым блоком usage. На живом транскрипте наивная сумма давала
    # 2 292 123 токена против 879 392 по уникальным идентификаторам — счёт
    # расходов и нагрузки завышался в 2,6 раза.
    counted_usage_ids = set()
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

        is_prompt = _is_user_prompt(item)
        if is_prompt:
            agg["n_prompts"] += 1

        message = item.get("message")
        if not isinstance(message, dict):
            continue

        if is_prompt:
            user_text_parts.append(_content_text(message.get("content")))

        # Модель, на которой отвечал ассистент. По записи на сообщение —
        # какую из них считать моделью сессии, решает build_session_row
        # (правило одно на оба движка).
        if item.get("type") == "assistant":
            model = message.get("model")
            if isinstance(model, str) and model:
                agg["_models"].append(model)

        usage = message.get("usage")
        message_id = message.get("id")
        already_counted = isinstance(message_id, str) and message_id in counted_usage_ids
        if isinstance(usage, dict) and not already_counted:
            if isinstance(message_id, str):
                counted_usage_ids.add(message_id)
            agg["tokens_in"] += _to_int(usage.get("input_tokens"))
            agg["tokens_out"] += _to_int(usage.get("output_tokens"))
            # Кеш это две разные вещи и обе стоят денег: чтение из кеша и
            # его создание. Раньше считалось только чтение.
            agg["tokens_cache"] += _to_int(usage.get("cache_read_input_tokens"))
            agg["tokens_cache"] += _to_int(usage.get("cache_creation_input_tokens"))

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


def pick_model(models):
    """Одна модель на строку сессии из всех, что встретились в транскрипте.

    Правило общее для обоих движков (извлечение — разное, оно в парсерах):
    ПРЕОБЛАДАЮЩАЯ по числу ходов, при равенстве — последняя. Не «последняя»
    как таковая: колонка отвечает на вопрос «какой моделью работали», и
    сессия, где двадцать ходов прошли на одной модели, а последний — на
    другой, ответила бы неверно. Не «список» — в отчётности по нему нельзя
    сгруппировать, а случай смены модели посреди сессии редкий; если он
    станет частым, это будет видно и колонку можно будет расширить осознанно.

    Пусто — модели в транскрипте не нашлось (сессия без единого ответа
    модели). Пустая строка честнее подстановки «наверное, дефолтная».
    """
    if not models:
        return ""
    counts = {}
    for name in models:
        counts[name] = counts.get(name, 0) + 1
    best = max(counts.values())
    # При равенстве берём ту, что встретилась последней: reversed() + первое
    # совпадение.
    for name in reversed(models):
        if counts[name] == best:
            return name
    return ""


def clickhouse_users(environ=None, secrets_path=None):
    """(ch_wms_user, ch_dwh_user) — две учётки ClickHouse, под которыми шла
    работа. Обе как есть, ничего не выбирая и не сливая.

    Почему две, а не одна «корпоративная»: на рабочей машине владельца в
    складской кластер ходят под ЗАИМСТВОВАННОЙ учёткой (n-lyubchenko → 200),
    а его личная там не заведена вовсе (a-bir → 403 «password is incorrect,
    or there is no user with such name»). Записать одну — либо потерять
    человека, либо соврать про то, чьим доступом читались данные. Две
    колонки делают заимствование видимым: один ch_wms_user на несколько
    разных ch_dwh_user.

    Опознание человека — уже вывод из этих двух (ch_dwh_user, иначе
    ch_wms_user), и делается он при чтении, в запросе. Здесь его нет
    намеренно: вывод не должен затвердевать в данных.

    Смотрим сначала в окружение (bin/uzum разворачивает туда secrets.env
    перед запуском движка), потом в сам secrets.env — сессию могли начать и
    не через наш лаунчер. Чего нет — пустая строка: выдуманная учётка хуже
    отсутствующей.

    `secrets_path=None`, а не `=SECRETS_PATH`: умолчание-выражение
    вычисляется ОДИН раз при импорте модуля, поэтому прибитый в сигнатуре
    путь делал функцию только на вид настраиваемой. Найдено дорого: тест
    подменял `log_session.SECRETS_PATH` на свой временный файл, подмена
    молча не действовала, читался настоящий ~/.config/uzum-ai/secrets.env —
    и тест был зелёным ровно потому, что в личном файле владельца лежали те
    же значения, которые он ожидал. На любой другой машине он бы падал, а
    само утверждение не проверялось никогда. Путь берётся в момент вызова.
    """
    environ = os.environ if environ is None else environ
    secrets_path = SECRETS_PATH if secrets_path is None else secrets_path
    try:
        stored = envfile.read(secrets_path)
    except Exception:
        stored = {}

    def value(name):
        return (environ.get(name) or stored.get(name) or "").strip()

    return value("CH_WMS_USER"), value("CH_DWH_USER")


def _repo_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def build_session_row(payload, secrets, include_transcript=True):
    """Строка сессии для sandbox.ai_usage_sessions.

    include_transcript=False — промежуточная строка (хук Stop у Claude Code,
    UserPromptSubmit у Codex): всё то же самое, но колонка transcript пустая.
    Транскрипт живой сессии — это сотни килобайт (в замерах 283-490 КБ, потолок
    чтения 5 МБ), и слать их на каждом ходу нельзя ни по сети, ни по месту в
    очереди. Набор ключей от флага НЕ зависит: он сверяется со схемой таблицы
    тестом, и «на промежуточной строке колонок меньше» означало бы вторую,
    молчаливую форму той же строки.
    """
    session_id = payload.get("session_id", "")
    # Разбор транскрипта — движок определяет, каким парсером читать: формат
    # JSONL общий, структура записей — нет (docs/codex-facts.md, раздел 6;
    # lib/transcript_codex.py, докстринг модуля). Разбор Claude Code
    # (read_transcript() ниже в этом файле) не трогаем — он покрыт тестами
    # и уже дважды чинился; для Codex — отдельная функция в lib/transcript_codex.py.
    # engine может быть ENGINE_CLAUDE, ENGINE_CODEX или ENGINE_UNKNOWN
    # (ревью-находка 4, задача Codex-4 — detect_engine() больше не сводит
    # неопознанное к Claude Code молча, это видно в колонке engine строки).
    # На выбор ПАРСЕРА транскрипта это не влияет: для ENGINE_UNKNOWN нет
    # третьего парсера, и наилучшая попытка — разобрать как Claude Code
    # (исторически самый частый формат), не роняя сессию вовсе.
    engine = hook_payload.detect_engine(payload)
    if engine == hook_payload.ENGINE_CODEX:
        text, agg = transcript_codex.read_transcript(payload.get("transcript_path", ""), secrets)
    else:
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
    ch_wms_user, ch_dwh_user = clickhouse_users()
    return {
        "session_id": session_id,
        # Учётка МАШИНЫ, а не человека в компании: раньше сюда пытались
        # подставить корпоративный логин через UZUM_USER, но переменная,
        # из которой он брался, давно исчезла (см. bin/uzum). Личность
        # теперь считается из ch_dwh_user/ch_wms_user ниже.
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
        "model": pick_model(agg.get("_models") or []),
        # Порядок распаковки — единственное место, где эти две можно
        # перепутать местами, и снаружи такая ошибка незаметна: обе строки,
        # обе похожи на логин.
        "ch_wms_user": ch_wms_user,
        "ch_dwh_user": ch_dwh_user,
        "repo_sha": _repo_sha(),
        # Причина завершения — как её сообщил движок, без домысливания.
        #
        # У Claude Code это осмысленный набор ('prompt_input_exit', 'clear',
        # 'resume', …). У Codex — ВСЕГДА 'other', и это его свойство, а не
        # наша недоработка: проверено живым запуском в обоих режимах
        # (`codex exec` и интерактивный TUI с выходом по Ctrl-D — оба
        # прислали reason='other'), и в самом бинаре Codex нет ни одной
        # строки вроде 'prompt_input_exit' — словаря причин у него просто
        # не существует (docs/codex-facts.md, раздел 2). Выводить причину
        # из транскрипта (например, считать 'exit' в последнем промпте
        # выходом) — это уже выдумывание данных, которых движок не давал.
        #
        # Поэтому здесь ничего не «нормализуется»: сравнивать движки по
        # end_reason нельзя, и это должно быть видно по данным, а не
        # спрятано за подставленным значением.
        "end_reason": payload.get("reason", ""),
        "transcript": text if include_transcript else "",
        "engine": engine,
    }


def log_progress(payload, secrets_path=None):
    """Промежуточная строка сессии — не дожидаясь выхода.

    Возвращает True, если строка принята в очередь; False — если рано (не
    прошла минута с прошлой) или писать нечего. Никогда не бросает
    исключений: вызывается из хуков, у которых код возврата обязан быть 0, а
    на UserPromptSubmit у Codex ненулевой код — это `Blocked`, то есть
    молчащая сессия вместо ответа (docs/codex-facts.md, раздел 11).

    `secrets_path` нужен только тестам; в бою берётся SECRETS_PATH в момент
    вызова — по той же причине, что и в clickhouse_users(): умолчание-
    выражение в сигнатуре вычисляется один раз при импорте и делает функцию
    настраиваемой только на вид.
    """
    try:
        session_id = payload.get("session_id", "")
        # Порядок важен: троттлинг раньше разбора транскрипта, иначе самая
        # дорогая часть работы выполнялась бы и выбрасывалась.
        if not _progress_is_due(session_id):
            return False
        secrets = redact.load_secret_values(
            SECRETS_PATH if secrets_path is None else secrets_path)
        telemetry.write("ai_usage_sessions",
                        build_session_row(payload, secrets, include_transcript=False))
        _mark_progress(session_id)
        telemetry.flush_in_background()
        return True
    except Exception:
        return False


def main():
    try:
        # Чужая сессия — выходим немедленно и молча, до чтения stdin (см.
        # lib/hook_scope.py: hooks.json Codex общий на все проекты аналитика,
        # поэтому этот скрипт запускают и там, где он ни при чём). Внутри
        # try — по той же причине, что и в log_event.py: обещание «всегда
        # код 0» распространяется и на саму проверку.
        if not hook_scope.session_is_ours():
            return 0
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        if event == "SessionStart":
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(_started_at_path(payload.get("session_id", "")), "w",
                      encoding="utf-8") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
            # Отправка накопленного — отдельным отвязанным процессом, а не
            # здесь: старт сессии человек ждёт так же, как и всё остальное,
            # а очередь могла накопиться за месяц работы без сети (потолок
            # у flush() — 2 секунды, и это две секунды тишины перед первым
            # ответом).
            telemetry.flush_in_background()
        elif event == "Stop":
            # Ответ модели закончен — сессия жива, но строку о ней можно
            # писать уже сейчас, не дожидаясь выхода, которого может не быть
            # вовсе. Без транскрипта и не чаще раза в минуту, см. log_progress.
            log_progress(payload)
        elif event == "SessionEnd":
            secrets = redact.load_secret_values(SECRETS_PATH)
            session_id = payload.get("session_id", "")
            telemetry.write("ai_usage_sessions", build_session_row(payload, secrets))
            # Строка сессии уже на диске (write() локальный) — отправляем
            # её тем же отвязанным процессом. Сессия к этому моменту
            # закончилась, но процесс переживёт её сам: свой сеанс, все
            # потоки в /dev/null, никаких труб он не держит.
            telemetry.flush_in_background()
            # Данные файла-метки уже перенесены в строку сессии (отправлена
            # или, при недоступности ClickHouse, ушла в очередь telemetry —
            # write() никогда не теряет строку молча). Сам файл больше не
            # нужен: не убирать его — значит копить в STATE_DIR по файлу на
            # каждую сессию бессрочно.
            # Метка троттлинга уходит вместе с меткой старта и по той же
            # причине: сессия закончилась, файл на каждую сессию бессрочно
            # копиться в STATE_DIR не должен.
            for path in (_started_at_path(session_id), _progress_at_path(session_id)):
                try:
                    os.remove(path)
                except OSError:
                    pass
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
