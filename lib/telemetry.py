# lib/telemetry.py
"""Запись телеметрии в ClickHouse через HTTP-интерфейс.

Только стандартная библиотека. Никогда не бросает исключений и не блокирует
работу: при любой проблеме строка уходит в локальную очередь и отправляется
при следующем запуске сессии.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_DB = "ai_usage"
TIMEOUT_S = 4


class Config(object):
    def __init__(self, host, user, password, database, queue_dir, enabled):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.queue_dir = queue_dir
        self.enabled = enabled

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
        )


def _post(cfg, table, rows):
    """Отправить строки в ClickHouse. True — успех, False — нет."""
    query = "INSERT INTO %s.%s FORMAT JSONEachRow" % (cfg.database, table)
    url = "https://%s/?%s" % (cfg.host, urllib.parse.urlencode({"query": query}))
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in rows).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("X-ClickHouse-User", cfg.user)
    req.add_header("X-ClickHouse-Key", cfg.password)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _enqueue(cfg, table, row):
    try:
        os.makedirs(cfg.queue_dir, exist_ok=True)
        path = os.path.join(cfg.queue_dir, "%d-%d.jsonl" % (int(time.time()), os.getpid()))
        with open(path, "a", encoding="utf-8") as f:
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
    """Отправить накопленное из очереди. Возвращает число отправленных строк."""
    cfg = Config.from_env()
    if not cfg.enabled or not os.path.isdir(cfg.queue_dir):
        return 0
    sent = 0
    for name in sorted(os.listdir(cfg.queue_dir)):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(cfg.queue_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                items = [json.loads(line) for line in f if line.strip()]
        except Exception:
            continue
        ok = True
        for item in items:
            try:
                if not _post(cfg, item["table"], [item["row"]]):
                    ok = False
                    break
            except Exception:
                ok = False
                break
            sent += 1
        if ok:
            try:
                os.remove(path)
            except OSError:
                pass
        else:
            break
    return sent
