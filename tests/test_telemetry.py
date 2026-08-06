# tests/test_telemetry.py
import json
import os

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
