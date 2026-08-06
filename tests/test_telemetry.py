# tests/test_telemetry.py
import datetime
import json
import os
import time

import telemetry


def test_config_disabled_when_host_missing(monkeypatch):
    monkeypatch.delenv("TELEMETRY_CH_HOST", raising=False)
    assert telemetry.Config.from_env().enabled is False


def test_config_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "denis-platon")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))
    cfg = telemetry.Config.from_env()
    assert cfg.enabled is True
    assert cfg.database == "sandbox"
    assert cfg.queue_dir == str(tmp_path / "queue")
    assert cfg.secure is False  # дефолт: http, как у CLICKHOUSE_SECURE в .mcp.json
    assert cfg.port == "8123"


def test_post_uses_http_by_default_and_https_when_secure_true(monkeypatch, tmp_path):
    """Реальный прод-эндпоинт — http://...:8123 (CLICKHOUSE_SECURE=false в
    .mcp.json). _post раньше хардкодил https:// и вставка не проходила бы.
    Схема и порт должны собираться из TELEMETRY_CH_SECURE/TELEMETRY_CH_PORT."""
    _enable(monkeypatch, tmp_path)
    captured = {}

    class FakeResponse(object):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        return FakeResponse()

    monkeypatch.setattr(telemetry.urllib.request, "urlopen", fake_urlopen)

    # дефолт: TELEMETRY_CH_SECURE не задан -> http, порт из TELEMETRY_CH_PORT
    # тоже не задан -> дефолтный 8123
    cfg_http = telemetry.Config.from_env()
    assert telemetry._post(cfg_http, "ai_usage_events", [{"n": 1}]) is True
    assert captured["url"].startswith("http://ch.example.uz:8123/?")

    # TELEMETRY_CH_SECURE=true -> https, порт из TELEMETRY_CH_PORT
    monkeypatch.setenv("TELEMETRY_CH_SECURE", "true")
    monkeypatch.setenv("TELEMETRY_CH_PORT", "8443")
    cfg_https = telemetry.Config.from_env()
    assert telemetry._post(cfg_https, "ai_usage_events", [{"n": 1}]) is True
    assert captured["url"].startswith("https://ch.example.uz:8443/?")


def test_utc_now_str_reflects_utc_not_local_time(monkeypatch):
    """Контракт: время, которое формирует telemetry.py для колонок
    DateTime('UTC')/DateTime64(3, 'UTC'), должно быть в UTC, а не в
    локальном часовом поясе машины аналитика.

    Это тест форматирования: _utc_now() подменена фиксированным значением,
    так что он проверяет только strftime-логику utc_now_str(). Он НЕ ловит
    регресс внутри самой _utc_now() (например, откат на
    datetime.datetime.now() без зоны) — для этого есть отдельный тест ниже,
    test_utc_now_returns_utc_aware_datetime, который вызывает _utc_now()
    без подмены."""
    fixed = datetime.datetime(2026, 8, 6, 12, 34, 56, 789000, tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(telemetry, "_utc_now", lambda: fixed)

    assert telemetry.utc_now_str() == "2026-08-06 12:34:56"
    assert telemetry.utc_now_str(milliseconds=True) == "2026-08-06 12:34:56.789"


def test_format_utc_formats_arbitrary_datetime_not_just_now():
    """format_utc() — единственное место в репозитории, где определён формат
    строк времени для DateTime('UTC'): utc_now_str() форматирует "сейчас"
    через него же. Он нужен и коду, которому надо отформатировать НЕ текущий
    момент (например, log_session.py форматирует started_at — время начала
    сессии, прочитанное из файла, а не "сейчас")."""
    dt = datetime.datetime(2020, 1, 2, 3, 4, 5, 6000, tzinfo=datetime.timezone.utc)
    assert telemetry.format_utc(dt) == "2020-01-02 03:04:05"
    assert telemetry.format_utc(dt, milliseconds=True) == "2020-01-02 03:04:05.006"


def test_format_utc_converts_non_utc_aware_datetime_to_utc():
    """Aware datetime в другом поясе должен быть приведён к UTC перед
    форматированием, а не отформатирован "как есть"."""
    tz = datetime.timezone(datetime.timedelta(hours=5))  # Asia/Tashkent, UTC+5
    dt = datetime.datetime(2020, 1, 2, 8, 4, 5, tzinfo=tz)
    assert telemetry.format_utc(dt) == "2020-01-02 03:04:05"


def test_utc_now_returns_utc_aware_datetime():
    """Регрессионный тест на саму _utc_now() — без подмены. Если её тело
    откатить к datetime.datetime.now() (наивное локальное время машины,
    исходный баг с расхождением в час между сервером и клиентом в разных
    поясах), эта проверка падает: у наивного datetime utcoffset() is None,
    а не timedelta(0); дальше сравнение naive/aware datetime вообще
    бросает TypeError, что тоже валит тест."""
    before = datetime.datetime.now(datetime.timezone.utc)
    now = telemetry._utc_now()
    after = datetime.datetime.now(datetime.timezone.utc)

    assert now.utcoffset() == datetime.timedelta(0)
    # не просто "какая-то UTC-метка", а реально близкая к текущему моменту —
    # чтобы не пропустить, например, захардкоженную константу с tzinfo=utc.
    assert before - datetime.timedelta(seconds=5) <= now <= after + datetime.timedelta(seconds=5)


def test_write_queues_when_send_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)

    assert telemetry.write("ai_usage_events", {"user": "denis", "ok": 1}) is False

    queued = list((tmp_path / "queue").glob("*.jsonl"))
    assert len(queued) == 1
    row = json.loads(queued[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["table"] == "ai_usage_events"
    assert row["row"]["user"] == "denis"


def test_flush_sends_queue_and_clears_it(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))

    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)
    telemetry.write("ai_usage_events", {"user": "denis"})

    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: True)
    assert telemetry.flush() == 1
    assert list((tmp_path / "queue").glob("*.jsonl")) == []


def test_write_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))

    def boom(*a, **k):
        raise RuntimeError("сеть отвалилась")

    monkeypatch.setattr(telemetry, "_post", boom)
    assert telemetry.write("ai_usage_events", {"user": "denis"}) is False


def test_disabled_config_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEMETRY_CH_HOST", raising=False)
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))
    assert telemetry.write("ai_usage_events", {"user": "denis"}) is False
    assert not (tmp_path / "queue").exists()


# --- Регрессионные тесты на находки ревью (дубли, потолок, исключения) ---


def _enable(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))


def test_flush_does_not_resend_succeeded_group_after_partial_failure(monkeypatch, tmp_path):
    """Файл очереди содержит две таблицы: events отправляется успешно,
    sessions — нет. Повторный flush() не должен снова слать events."""
    _enable(monkeypatch, tmp_path)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    entries = [
        {"table": "ai_usage_events", "row": {"user": "denis", "n": 1}},
        {"table": "ai_usage_events", "row": {"user": "denis", "n": 2}},
        {"table": "ai_usage_sessions", "row": {"user": "denis", "n": 3}},
    ]
    (queue_dir / "1-1.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
        encoding="utf-8",
    )

    calls = []

    def fake_post(cfg, table, rows):
        calls.append((table, len(rows)))
        return table == "ai_usage_events"

    monkeypatch.setattr(telemetry, "_post", fake_post)
    sent = telemetry.flush()

    assert sent == 2  # только события events, sessions не отправлены
    # events ушли одним батчем на 2 строки (а не по одной строке за запрос)
    assert ("ai_usage_events", 2) in calls
    assert ("ai_usage_sessions", 1) in calls
    assert len(calls) == 2

    remaining_files = list(queue_dir.glob("*.jsonl"))
    assert len(remaining_files) == 1
    left = [
        json.loads(line)
        for line in remaining_files[0].read_text(encoding="utf-8").splitlines()
    ]
    assert left == [{"table": "ai_usage_sessions", "row": {"user": "denis", "n": 3}}]

    # Повторный flush не должен снова слать events — их уже нет в очереди.
    calls.clear()
    monkeypatch.setattr(
        telemetry, "_post", lambda cfg, table, rows: calls.append(table) or True
    )
    sent2 = telemetry.flush()

    assert sent2 == 1
    assert calls == ["ai_usage_sessions"]
    assert list(queue_dir.glob("*.jsonl")) == []


def test_flush_never_raises_when_queue_dir_unreadable(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()

    def boom(path):
        raise OSError("права на каталог изменились")

    monkeypatch.setattr(telemetry.os, "listdir", boom)

    assert telemetry.flush() == 0


def test_flush_respects_row_cap_per_call(monkeypatch, tmp_path):
    """Две очереди по 3 строки, потолок — 3 строки за вызов: первый flush()
    отправляет только первый файл, второй остаётся нетронутым."""
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry, "FLUSH_MAX_ROWS", 3)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    for i in range(2):
        rows = [
            {"table": "ai_usage_events", "row": {"n": i * 10 + j}} for j in range(3)
        ]
        (queue_dir / ("%d-1.jsonl" % i)).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(telemetry, "_post", lambda cfg, table, rows: True)

    sent = telemetry.flush()
    assert sent == 3
    assert len(list(queue_dir.glob("*.jsonl"))) == 1  # второй файл дождётся следующего flush()

    sent2 = telemetry.flush()
    assert sent2 == 3
    assert list(queue_dir.glob("*.jsonl")) == []


def test_flush_respects_row_cap_within_single_group(monkeypatch, tmp_path):
    """Потолок должен работать и внутри одного файла/одной группы: если
    _enqueue сложил много строк одной таблицы в один файл (всплеск записи
    при недоступной сети в пределах одной секунды), flush() всё равно не
    должен отправить больше FLUSH_MAX_ROWS за один вызов."""
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry, "FLUSH_MAX_ROWS", 2)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()

    rows = [{"table": "ai_usage_events", "row": {"n": i}} for i in range(10)]
    (queue_dir / "1-1.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )

    sent_ns = []

    def fake_post(cfg, table, batch):
        sent_ns.extend(r["n"] for r in batch)
        return True

    monkeypatch.setattr(telemetry, "_post", fake_post)

    sent1 = telemetry.flush()
    assert sent1 == 2  # не больше потолка за один вызов
    assert sent_ns == [0, 1]  # ушёл ровно первый кусок, по порядку

    remaining = list(queue_dir.glob("*.jsonl"))
    assert len(remaining) == 1
    left = [json.loads(l) for l in remaining[0].read_text(encoding="utf-8").splitlines()]
    assert [item["row"]["n"] for item in left] == [2, 3, 4, 5, 6, 7, 8, 9]

    sent_ns.clear()
    sent2 = telemetry.flush()
    assert sent2 == 2
    assert sent_ns == [2, 3]  # остаток отправляется следующим вызовом, без повторов и без потерь

    left2 = [
        json.loads(l)
        for l in (queue_dir / "1-1.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["row"]["n"] for item in left2] == [4, 5, 6, 7, 8, 9]


def test_flush_respects_time_budget(monkeypatch, tmp_path):
    """Как только истёк отведённый бюджет времени, flush() прекращает
    обработку очереди, не трогая ещё не начатые файлы."""
    _enable(monkeypatch, tmp_path)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    for i in range(2):
        (queue_dir / ("%d-1.jsonl" % i)).write_text(
            json.dumps({"table": "ai_usage_events", "row": {"n": i}}) + "\n",
            encoding="utf-8",
        )

    # 1-й вызов monotonic() — расчёт дедлайна (t=0, дедлайн=2.0)
    # 2-й вызов — проверка перед файлом 0 (0.5 < 2.0 → обрабатываем)
    # 3-й вызов — проверка перед файлом 1 (3.0 >= 2.0 → бюджет исчерпан, стоп)
    fake_times = iter([0.0, 0.5, 3.0])
    monkeypatch.setattr(telemetry.time, "monotonic", lambda: next(fake_times))
    monkeypatch.setattr(telemetry, "_post", lambda cfg, table, rows: True)

    sent = telemetry.flush()

    assert sent == 1
    assert len(list(queue_dir.glob("*.jsonl"))) == 1  # второй файл не тронут


# --- находки 10, 11 и «права на файлы очереди» финального ревью -------------


def test_insert_url_asks_for_async_insert(monkeypatch, tmp_path):
    """Находка 10: без async_insert на каждый вызов инструмента у каждого
    аналитика приходится отдельная вставка в MergeTree."""
    _enable(monkeypatch, tmp_path)
    captured = {}

    class FakeResponse(object):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        telemetry.urllib.request, "urlopen",
        lambda req, timeout=None: (captured.update(url=req.full_url), FakeResponse())[1],
    )
    telemetry._post(telemetry.Config.from_env(), "ai_usage_events", [{"n": 1}])
    assert "async_insert=1" in captured["url"]
    assert "wait_for_async_insert=0" in captured["url"]


def test_queue_file_is_readable_only_by_owner(monkeypatch, tmp_path):
    """В файле очереди лежит полный транскрипт сессии — права 0600, и каталог
    очереди 0700, как у каталога секретов."""
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)
    telemetry.write("ai_usage_sessions", {"transcript": "секретный текст"})
    queue_dir = tmp_path / "queue"
    assert oct(queue_dir.stat().st_mode)[-3:] == "700"
    files = list(queue_dir.glob("*.jsonl"))
    assert files
    assert oct(files[0].stat().st_mode)[-3:] == "600"


def test_queue_drops_oldest_files_over_the_size_cap(monkeypatch, tmp_path):
    """Находка 11: при недоступной базе очередь росла без потолка — каждая
    сессия кладёт файл до 5 МБ и он остаётся навсегда."""
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry, "QUEUE_MAX_BYTES", 3000)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    base = int(time.time()) - 400  # свежие файлы: потолок по возрасту ни при чём
    names = []
    for i in range(4):
        p = queue_dir / ("%d-1.jsonl" % (base + i))
        p.write_text("x" * 1000, encoding="utf-8")
        os.utime(p, (base + i, base + i))
        names.append(p.name)

    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)
    telemetry.write("ai_usage_events", {"n": 1})

    left = sorted(p.name for p in queue_dir.glob("*.jsonl"))
    files, total, _ = telemetry.queue_stats(str(queue_dir))
    assert total <= telemetry.QUEUE_MAX_BYTES + 200  # новый файл маленький
    assert files == len(left)
    assert names[0] not in left  # самый старый ушёл первым
    assert names[3] in left      # свежий остался


def test_queue_drops_files_older_than_the_age_cap(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path)
    monkeypatch.setattr(telemetry, "QUEUE_MAX_AGE_S", 60)
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    old = queue_dir / "old.jsonl"
    old.write_text("{}\n", encoding="utf-8")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    fresh = queue_dir / "fresh.jsonl"
    fresh.write_text("{}\n", encoding="utf-8")

    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)
    telemetry.write("ai_usage_events", {"n": 1})

    assert not old.exists()
    assert fresh.exists()


def test_queue_stats_reports_size_for_diagnostics(tmp_path):
    (tmp_path / "a.jsonl").write_text("x" * 10, encoding="utf-8")
    (tmp_path / "b.jsonl").write_text("x" * 5, encoding="utf-8")
    (tmp_path / "не-очередь.txt").write_text("x", encoding="utf-8")
    files, total, oldest = telemetry.queue_stats(str(tmp_path))
    assert (files, total) == (2, 15)
    assert oldest >= 0


def test_queue_stats_on_missing_dir():
    assert telemetry.queue_stats("/nope/queue") == (0, 0, 0)
