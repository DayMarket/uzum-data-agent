# lib/telemetry.py
"""Запись телеметрии в ClickHouse через HTTP-интерфейс.

Только стандартная библиотека. Никогда не бросает исключений и не блокирует
работу: при любой проблеме строка уходит в локальную очередь и отправляется
при следующем запуске сессии.

Таблицы живут в базе `sandbox` (прав на CREATE DATABASE нет) с префиксом
`ai_usage_` — вызывающий код передаёт полное имя таблицы с префиксом,
например `write("ai_usage_events", row)`.

Контракт по времени (обязателен для задач 4/5): все колонки времени в
sql/schema.sql типизированы как DateTime('UTC') / DateTime64(3, 'UTC').
Любая строка времени, которая пишется в ai_usage.* (ts, started_at,
ended_at, drafted_at, verdict_at), должна быть посчитана в UTC —
`datetime.datetime.now(datetime.timezone.utc)` — и отформатирована БЕЗ
суффикса зоны тем же строковым форматом, что и раньше
('%Y-%m-%d %H:%M:%S' или с тремя знаками миллисекунд). Используйте
`utc_now_str()` из этого модуля, а не `datetime.now()`/`datetime.today()`:
наивное локальное время ноутбука аналитика (который не обязан быть в
Asia/Tashkent) даст рассинхрон с тем, что реально пишется в колонку.
"""
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_DB = "sandbox"
DEFAULT_PORT = "8123"
TIMEOUT_S = 4

# Верхние границы на один вызов flush(), чтобы большая очередь или медленная
# сеть не задерживали старт сессии аналитика дольше пары секунд.
FLUSH_TIME_BUDGET_S = 2.0
FLUSH_MAX_ROWS = 2000

# Потолки локальной очереди. Без них очередь росла бесследно: при недоступной
# базе каждая сессия кладёт файл (в строке сессии лежит транскрипт — до 5 МБ)
# и он остаётся навсегда. У аналитика, который месяц работает вне корпоративной
# сети, это гигабайты в ~/.local/state, о которых он никогда не узнает.
# При переполнении выбрасываем самые старые файлы: свежая телеметрия ценнее
# позапрошлогодней, а чинить ситуацию всё равно человеку — см. скилл
# fix-access, там написано, как посмотреть размер очереди.
QUEUE_MAX_BYTES = 50 * 1024 * 1024
QUEUE_MAX_AGE_S = 30 * 24 * 3600


class Config(object):
    def __init__(self, host, user, password, database, queue_dir, enabled,
                 secure=False, port=DEFAULT_PORT):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.queue_dir = queue_dir
        self.enabled = enabled
        # HTTPS/порт настраиваются отдельно от host, по образцу CLICKHOUSE_SECURE
        # / CLICKHOUSE_PORT из .mcp.json — чтобы у аналитика не было двух разных
        # имён одного и того же параметра.
        self.secure = secure
        self.port = port

    @classmethod
    def from_env(cls):
        host = os.environ.get("TELEMETRY_CH_HOST", "").strip()
        state = os.environ.get(
            "UZUM_STATE_DIR",
            os.path.expanduser("~/.local/state/uzum-ai"),
        )
        return cls(
            host=host,
            user=os.environ.get("TELEMETRY_CH_USER", ""),
            password=os.environ.get("TELEMETRY_CH_PASSWORD", ""),
            database=os.environ.get("TELEMETRY_CH_DB", DEFAULT_DB),
            queue_dir=os.path.join(state, "queue"),
            enabled=bool(host) and os.environ.get("TELEMETRY_ENABLED", "1") != "0",
            secure=os.environ.get("TELEMETRY_CH_SECURE", "").strip().lower() == "true",
            port=os.environ.get("TELEMETRY_CH_PORT", DEFAULT_PORT).strip() or DEFAULT_PORT,
        )


def _utc_now():
    """Текущий момент как aware datetime в UTC. Отдельная функция — чтобы
    тесты могли зафиксировать конкретное значение через monkeypatch."""
    return datetime.datetime.now(datetime.timezone.utc)


def format_utc(dt, milliseconds=False):
    """Отформатировать aware datetime в UTC строкой для колонок DateTime('UTC') /
    DateTime64(3, 'UTC') — без суффикса зоны. Единственное место в репозитории,
    где определён этот формат: utc_now_str() и любой код, которому нужно
    отформатировать НЕ текущий момент (например, время начала сессии,
    прочитанное из файла), должны использовать эту функцию, а не
    переизобретать strftime на местах."""
    dt = dt.astimezone(datetime.timezone.utc)
    if milliseconds:
        return dt.strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (dt.microsecond // 1000)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def utc_now_str(milliseconds=False):
    """Текущее время в UTC строкой для колонок DateTime('UTC') /
    DateTime64(3, 'UTC') — без суффикса зоны. См. контракт в докстринге
    модуля."""
    return format_utc(_utc_now(), milliseconds)


def _base_url(cfg):
    scheme = "https" if cfg.secure else "http"
    return "%s://%s:%s/" % (scheme, cfg.host, cfg.port)


def _post(cfg, table, rows):
    """Отправить строки в ClickHouse. True — успех, False — нет."""
    query = "INSERT INTO %s.%s FORMAT JSONEachRow" % (cfg.database, table)
    # async_insert: без него на каждый вызов инструмента у каждого аналитика
    # приходится отдельная вставка — по куску на строку, и MergeTree потом
    # это всё сливает. wait_for_async_insert=0 — не ждём подтверждения записи
    # на диск: хук на PostToolUse висит в горячем пути работы аналитика, а
    # потеря последних секунд телеметрии при падении сервера нам не страшна.
    url = _base_url(cfg) + "?" + urllib.parse.urlencode({
        "query": query,
        "async_insert": "1",
        "wait_for_async_insert": "0",
    })
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("X-ClickHouse-User", cfg.user)
    req.add_header("X-ClickHouse-Key", cfg.password)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def queue_stats(queue_dir):
    """(число файлов, суммарный размер в байтах, возраст самого старого в
    секундах) — для диагностики (скилл fix-access). Никогда не падает."""
    files, total, oldest = 0, 0, 0
    now = time.time()
    try:
        for name in os.listdir(queue_dir):
            if not name.endswith(".jsonl"):
                continue
            try:
                st = os.stat(os.path.join(queue_dir, name))
            except OSError:
                continue
            files += 1
            total += st.st_size
            oldest = max(oldest, int(now - st.st_mtime))
    except OSError:
        pass
    return files, total, oldest


def _prune_queue(queue_dir):
    """Удержать очередь в потолке по объёму и возрасту. Сначала уходит самое
    старое — свежая телеметрия ценнее. Никогда не бросает исключений."""
    try:
        entries = []
        for name in os.listdir(queue_dir):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(queue_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            entries.append((st.st_mtime, st.st_size, path))
    except OSError:
        return

    now = time.time()
    kept = []
    total = 0
    for mtime, size, path in sorted(entries):
        if now - mtime > QUEUE_MAX_AGE_S:
            _remove(path)
            continue
        kept.append((mtime, size, path))
        total += size

    # сверху по объёму — выбрасываем самые старые из оставшихся
    for mtime, size, path in kept:
        if total <= QUEUE_MAX_BYTES:
            break
        _remove(path)
        total -= size


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _enqueue(cfg, table, row):
    try:
        os.makedirs(cfg.queue_dir, exist_ok=True)
        os.chmod(cfg.queue_dir, 0o700)
        _prune_queue(cfg.queue_dir)
        path = os.path.join(cfg.queue_dir, "%d-%d.jsonl" % (int(time.time()), os.getpid()))
        # 0600 на самом файле, а не только права каталога по умолчанию: в
        # строке сессии лежит полный транскрипт — тексты запросов аналитика,
        # ответы агента, имена витрин. Ставим до записи (os.open с mode), а не
        # chmod после — иначе между созданием и chmod есть окно, в котором
        # файл с транскриптом доступен на чтение всем.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"table": table, "row": row}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write(table, row):
    """Записать строку. Никогда не бросает исключений."""
    cfg = Config.from_env()
    if not cfg.enabled:
        return False
    try:
        if _post(cfg, table, [row]):
            return True
    except Exception:
        pass
    _enqueue(cfg, table, row)
    return False


def flush():
    """Отправить накопленное из очереди. Возвращает число отправленных строк.

    Никогда не бросает исключений — вся работа обёрнута в try/except.
    Ограничена по времени (FLUSH_TIME_BUDGET_S) и по числу строк
    (FLUSH_MAX_ROWS) за один вызов: при большой очереди или медленной сети
    остаток остаётся в очереди и уйдёт при следующем flush().

    Строки одной таблицы из одного файла очереди отправляются одним батчем
    (один HTTP-запрос на группу "таблица х файл"), но не крупнее остатка
    бюджета FLUSH_MAX_ROWS — если строк в группе больше, чем осталось от
    потолка, группа режется на кусок нужного размера и остаток дописывается
    обратно в очередь. Если батч не отправился, в файл очереди переписывается
    только неотправленный остаток — уже подтверждённые батчи повторно не
    шлются.
    """
    try:
        return _flush_impl()
    except Exception:
        return 0


def _flush_impl():
    cfg = Config.from_env()
    if not cfg.enabled:
        return 0
    try:
        if not os.path.isdir(cfg.queue_dir):
            return 0
        names = sorted(n for n in os.listdir(cfg.queue_dir) if n.endswith(".jsonl"))
    except Exception:
        return 0

    deadline = time.monotonic() + FLUSH_TIME_BUDGET_S
    sent = 0

    for name in names:
        path = os.path.join(cfg.queue_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                items = [json.loads(line) for line in f if line.strip()]
        except Exception:
            continue

        # Группируем строки файла по таблице, сохраняя порядок первого
        # появления таблицы — так одна группа = один батч = один запрос.
        # Записи с испорченной структурой (не dict) пропускаем, не роняя
        # весь flush().
        order = []
        groups = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            table = item.get("table")
            row = item.get("row")
            if table not in groups:
                groups[table] = []
                order.append(table)
            groups[table].append({"table": table, "row": row})

        remaining = []
        budget_exhausted = False
        for table in order:
            group_items = groups[table]

            if budget_exhausted or sent >= FLUSH_MAX_ROWS or time.monotonic() >= deadline:
                budget_exhausted = True
                remaining.extend(group_items)
                continue

            # Потолок по строкам должен соблюдаться и внутри файла: если
            # в группе больше строк, чем осталось от бюджета, отправляем
            # только кусок по размеру остатка, а хвост оставляем в очереди.
            room = FLUSH_MAX_ROWS - sent
            if room < len(group_items):
                chunk, leftover = group_items[:room], group_items[room:]
                budget_exhausted = True
            else:
                chunk, leftover = group_items, []

            rows = [gi["row"] for gi in chunk]
            try:
                ok = _post(cfg, table, rows)
            except Exception:
                ok = False
            if ok:
                sent += len(rows)
            else:
                leftover = chunk + leftover
            remaining.extend(leftover)

        try:
            if remaining:
                with open(path, "w", encoding="utf-8") as f:
                    for item in remaining:
                        f.write(json.dumps(item, ensure_ascii=False) + "\n")
            else:
                os.remove(path)
        except Exception:
            pass

        if budget_exhausted:
            break

    return sent
