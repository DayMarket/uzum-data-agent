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
    assert cfg.database == "ai_usage"
    assert cfg.queue_dir == str(tmp_path / "queue")


def test_write_queues_when_send_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)

    assert telemetry.write("events", {"user": "denis", "ok": 1}) is False

    queued = list((tmp_path / "queue").glob("*.jsonl"))
    assert len(queued) == 1
    row = json.loads(queued[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["table"] == "events"
    assert row["row"]["user"] == "denis"


def test_flush_sends_queue_and_clears_it(monkeypatch, tmp_path):
    monkeypatch.setenv("TELEMETRY_CH_HOST", "ch.example.uz")
    monkeypatch.setenv("TELEMETRY_CH_USER", "u")
    monkeypatch.setenv("TELEMETRY_CH_PASSWORD", "test-token-xxx")
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))

    monkeypatch.setattr(telemetry, "_post", lambda *a, **k: False)
    telemetry.write("events", {"user": "denis"})

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
    assert telemetry.write("events", {"user": "denis"}) is False


def test_disabled_config_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("TELEMETRY_CH_HOST", raising=False)
    monkeypatch.setenv("UZUM_STATE_DIR", str(tmp_path))
    assert telemetry.write("events", {"user": "denis"}) is False
    assert not (tmp_path / "queue").exists()
