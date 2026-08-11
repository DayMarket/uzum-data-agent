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
import subprocess
import sys
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
        """Куда и чем писать телеметрию.

        По умолчанию — тот же складской ClickHouse (WMS), которым аналитик
        уже пользуется: таблицы sandbox.ai_usage_* живут именно там, и
        отдельного доступа для них не существует. Поэтому CH_WMS_* — не
        «запасной вариант», а обычный путь, и мастер установки ничего про
        телеметрию не спрашивает.

        TELEMETRY_CH_* остаются как исключение и перебивают WMS
        ПОКОЛОНОЧНО. Так задуман главный ожидаемый случай: у пилотной группы
        прав INSERT в sandbox может не оказаться, и запись пойдёт отдельной
        учёткой-писателем на том же хосте — тогда в secrets.env кладут
        только TELEMETRY_CH_USER/TELEMETRY_CH_PASSWORD, а хост, порт и схему
        соединения по-прежнему берут от WMS. Всё-или-ничего заставляло бы
        переписывать все четыре значения ради двух и разъезжаться с WMS при
        переезде кластера.
        """
        def value(name, fallback, default=""):
            return (os.environ.get(name)
                    or os.environ.get(fallback)
                    or default).strip()

        host = value("TELEMETRY_CH_HOST", "CH_WMS_HOST")
        state = os.environ.get(
            "UZUM_STATE_DIR",
            os.path.expanduser("~/.local/state/uzum-ai"),
        )
        return cls(
            host=host,
            # Пароль не .strip(): в нём пробел по краям — законный символ,
            # а не форматирование. Логин и хост чистим (их печатали руками
            # в мастере), пароль берём как есть.
            user=value("TELEMETRY_CH_USER", "CH_WMS_USER"),
            password=(os.environ.get("TELEMETRY_CH_PASSWORD")
                      or os.environ.get("CH_WMS_PASSWORD") or ""),
            database=os.environ.get("TELEMETRY_CH_DB", DEFAULT_DB),
            queue_dir=os.path.join(state, "queue"),
            enabled=bool(host) and os.environ.get("TELEMETRY_ENABLED", "1") != "0",
            secure=value("TELEMETRY_CH_SECURE", "CH_WMS_SECURE").lower() == "true",
            port=value("TELEMETRY_CH_PORT", "CH_WMS_PORT", DEFAULT_PORT) or DEFAULT_PORT,
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


def _insert_request(cfg, table, rows):
    """Собрать HTTP-запрос вставки — ровно тот, которым пишется телеметрия.

    Одно место на весь модуль: и настоящая отправка (`_post`), и живая
    проверка доступа (`check_write`) берут запрос отсюда. Иначе проверка была
    бы вторым экземпляром запроса и разошлась бы с настоящим при первой же
    правке — а именно так и появился дефект, из-за которого мастер установки
    объявлял рабочей телеметрию, которая писать не может.
    """
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
    return req


def _send(cfg, table, rows):
    """Выполнить вставку и вернуть (код ответа, текст ответа).

    Код None — до сервера не дошли вовсе (нет сети, не поднят Netbird, не
    отвечает хост). Текст — то, что ответил сервер: у ClickHouse это строка
    вида "Code: 164. DB::Exception: … (READONLY)", единственный источник
    правды о причине отказа. Никогда не бросает исключений.
    """
    try:
        with urllib.request.urlopen(_insert_request(cfg, table, rows),
                                    timeout=TIMEOUT_S) as resp:
            return resp.status, ""
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        return e.code, detail.strip()
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, e)


def _post(cfg, table, rows):
    """Отправить строки в ClickHouse. True — успех, False — нет."""
    status, _ = _send(cfg, table, rows)
    return status is not None and 200 <= status < 300


# Таблица, на которой проверяется доступ на запись. Та же, что и у самой
# дорогой строки телеметрии (сессия): права в ClickHouse выдаются на таблицу,
# и «пишется ли sessions» — это то, что человека спрашивают в отчётности.
CHECK_TABLE = "ai_usage_sessions"

# Отказ по правам, а не поломка. 164 READONLY — учётка заведена только на
# чтение (так настроена вся пилотная группа), 497 ACCESS_DENIED — учётка не
# читает-только, но именно на INSERT в sandbox прав нет. Оба кода означают
# одно и то же для человека: «доступ есть, права на запись нет» — и лечатся
# заявкой, а не перезапуском мастера. Всё остальное (не отвечает хост, не тот
# пароль, нет таблицы) — это уже поломка, и говорить о ней надо иначе.
DENIED_MARKERS = ("Code: 164.", "Code: 497.", "Not enough privileges")


def _is_denied(detail):
    return any(marker in detail for marker in DENIED_MARKERS)


def check_write(cfg=None):
    """Живая проверка того, что телеметрия может ПИСАТЬ. Возвращает
    (исход, текст ответа сервера); исход — одно из:

      "ok"           — вставка принята, хуки будут писать;
      "denied"       — сервер отвечает отказом по правам (см. DENIED_MARKERS);
      "disabled"     — писать некуда/незачем: адреса нет или TELEMETRY_ENABLED=0;
      "error"        — всё остальное: не дошли до сервера, не тот пароль,
                       нет таблицы. Текст ответа — в втором элементе.

    Идёт ровно тем же путём, что и хуки: тот же `_insert_request` (тот адрес,
    та схема, та учётка, те настройки async_insert), та же база, та же
    таблица. Единственное отличие — строк ноль.

    Почему ноль строк, а не пробная строка. Права на INSERT ClickHouse
    проверяет при разборе запроса, до чтения тела: пустая вставка получает
    ровно тот же отказ, что и настоящая, — проверено живьём на боевом
    кластере обеими учётками (учётка-писатель: 200; учётка только на чтение:
    500, "Code: 164. DB::Exception: Cannot modify \'async_insert\' setting in
    readonly mode"). При этом в таблице не появляется ни одной строки —
    счётчик до и после проверки одинаковый, проверено обеими учётками.

    Отказ опознаётся по КОДУ (см. DENIED_MARKERS), а не по тексту: один и тот
    же READONLY приходит с разными формулировками — «Cannot execute query in
    readonly mode» на голом INSERT и «Cannot modify async_insert setting» на
    нашем, потому что мы задаём настройки запроса. Ловить по словам значило бы
    считать «нет прав» ошибкой связи при первой же смене формулировки. Это и есть честный способ без следа:
    мусорную строку в боевой таблице пришлось бы потом чистить (уже чистили —
    отладочные строки schema-check), а удаление требует ещё и прав на
    ALTER DELETE, которых у пилотной группы тем более нет.

    Чего эта проверка НЕ проверяет и не может: что строка правильной формы
    доедет до диска. Хуки пишут с wait_for_async_insert=0 — сервер отвечает
    200 до разбора тела, поэтому ошибку формата строки не увидит и настоящая
    вставка. Проверка честна ровно в том, что обещает: доступ и права.

    Никогда не бросает исключений.
    """
    try:
        cfg = Config.from_env() if cfg is None else cfg
        if not cfg.enabled or not cfg.user:
            return "disabled", ""
        status, detail = _send(cfg, CHECK_TABLE, [])
        if status is not None and 200 <= status < 300:
            return "ok", ""
        if _is_denied(detail):
            return "denied", detail
        return "error", detail or ("HTTP %s" % status)
    except Exception as e:
        return "error", "%s: %s" % (type(e).__name__, e)


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
    """Принять строку телеметрии. Только локально: файл в очереди, никакой
    сети. Никогда не бросает исключений.

    Раньше здесь стоял синхронный POST в ClickHouse, а очередь была
    запасным путём на случай отказа. Хук `log_event.py` висит на
    PostToolUse, то есть на КАЖДОМ вызове инструмента, — и этот POST
    добавлял 283-423 мс к каждому шагу аналитика (замер с рабочей машины,
    боевой кластер через Netbird), а при моргнувшей сети — ровно TIMEOUT_S,
    4 секунды, тоже на каждом. Ход из десяти вызовов инструментов стоил
    3-4,5 секунды ожидания, и это ощущалось как «инструмент тормозит».
    Правило «телеметрия не имеет права мешать работе» выполнялось только в
    части ошибок (наружу ничего не летит, код всегда 0), а про время не
    выполнялось вовсе — для человека это одно и то же.

    Теперь горячий путь — только запись файла рядом, это доли миллисекунды.
    Отправку берёт на себя отдельный, отвязанный процесс
    (flush_in_background) или следующий запуск сессии. Строка не теряется ни
    в одном из случаев: она уже на диске до того, как кто-либо пробует
    отправлять.

    Возвращает True, если строка принята в очередь (а НЕ «доставлена в
    ClickHouse» — доставку тут больше никто не ждёт).
    """
    try:
        cfg = Config.from_env()
        if not cfg.enabled:
            return False
        _enqueue(cfg, table, row)
        return True
    except Exception:
        return False


def flush_in_background():
    """Запустить отправку очереди отдельным процессом и сразу вернуться.

    Процесс отвязан от сессии полностью: свой сеанс (start_new_session) и
    все три потока в /dev/null. Оба свойства обязательны, и второе — не
    гигиена, а условие работоспособности: движок читает stdout процесса
    хука, и потомок, унаследовавший эту трубу, держал бы её открытой после
    завершения самого хука — движок ждал бы EOF ровно столько, сколько
    живёт «фоновая» отправка, то есть фон перестал бы быть фоном (такой
    сирота с унаследованной трубой у нас уже был найден в сторожевом
    таймере setup.sh — второй заводить не будем).

    Никогда не бросает исключений и ничего не ждёт. Если отправка уже идёт
    (замок в очереди) — второй процесс не запускается: смысла нет, а
    одновременные отправки одного и того же файла очереди — это ещё и
    дубли строк в ClickHouse.
    """
    try:
        cfg = Config.from_env()
        if not cfg.enabled:
            return False
        if _flush_lock_held(cfg.queue_dir):
            return False
        subprocess.Popen(
            [sys.executable, "-c", _FLUSH_SNIPPET, os.path.dirname(os.path.abspath(__file__))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except Exception:
        return False


# Тело фонового процесса. Отдельной строкой, а не файлом-скриптом: файл
# пришлось бы держать в синхроне с расположением lib/ и правами на запуск, а
# путь к самому lib/ и так передаётся аргументом.
_FLUSH_SNIPPET = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "import telemetry; telemetry.flush()"
)


# Замок «отправка уже идёт». Нужен не ради экономии процессов, а ради
# корректности: два флашера, читающие один и тот же файл очереди, отправят
# одни и те же строки дважды — в ClickHouse это дубли, которые потом никто
# не отличит от настоящих повторов. Протухший замок (процесс убит, машина
# уснула) забирается по возрасту: сама отправка ограничена
# FLUSH_TIME_BUDGET_S ≈ 2 с, поэтому минута — заведомо больше любого
# честного удержания.
FLUSH_LOCK_STALE_S = 60


def _flush_lock_path(queue_dir):
    return os.path.join(queue_dir, ".flush.lock")


def _flush_lock_held(queue_dir):
    """Есть ли живой замок. Никогда не бросает исключений."""
    try:
        age = time.time() - os.stat(_flush_lock_path(queue_dir)).st_mtime
    except OSError:
        return False
    return age <= FLUSH_LOCK_STALE_S


def _acquire_flush_lock(queue_dir):
    """Взять замок. True — взяли, False — отправка уже идёт."""
    path = _flush_lock_path(queue_dir)
    for attempt in (1, 2):
        try:
            os.makedirs(queue_dir, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            if attempt == 2 or _flush_lock_held(queue_dir):
                return False
            _remove(path)  # протух — забираем и пробуем ровно один раз
        except Exception:
            return False
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

    Отправка одна на машину за раз (см. FLUSH_LOCK_STALE_S): если другой
    процесс уже отправляет, возвращает 0, ничего не трогая — очередь
    никуда не денется, её дочитает тот, кто держит замок, или следующий
    вызов.
    """
    cfg = None
    try:
        cfg = Config.from_env()
        if not cfg.enabled:
            return 0
        if not _acquire_flush_lock(cfg.queue_dir):
            return 0
    except Exception:
        return 0
    try:
        return _flush_impl(cfg)
    except Exception:
        return 0
    finally:
        try:
            _remove(_flush_lock_path(cfg.queue_dir))
        except Exception:
            pass


def _flush_impl(cfg):
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
