"""Тесты на connectors/clickhouse_proxy.py — обёртку для Codex, которая
раскладывает CH_WMS_*/CH_DWH_* в CLICKHOUSE_* перед запуском mcp-clickhouse.

Ключевая проверка: WMS и DWH, читая ОДИН общий словарь окружения (именно так
Codex его и передаёт — общий процесс, общее окружение, только разные имена
переменных в env_vars двух коннекторов), получают РАЗНЫЕ CLICKHOUSE_* — то
есть каждый видит своё, а не значение соседа."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "connectors"))

import clickhouse_proxy  # noqa: E402


COMBINED_ENV = {
    "CH_WMS_HOST": "wms.example.internal",
    "CH_WMS_USER": "wms-user",
    "CH_WMS_PASSWORD": "wms-pass",
    "CH_WMS_PORT": "8123",
    "CH_WMS_SECURE": "false",
    "CH_DWH_HOST": "dwh.example.internal",
    "CH_DWH_USER": "dwh-user",
    "CH_DWH_PASSWORD": "dwh-pass",
    "CH_DWH_PORT": "9440",
    "CH_DWH_SECURE": "true",
    # Мусор, который не должен попасть в результат как CLICKHOUSE_*
    "UNRELATED": "should-not-matter",
}


def test_wms_and_dwh_resolve_to_different_clickhouse_env_from_one_shared_source():
    wms_env = clickhouse_proxy.build_env("wms", COMBINED_ENV)
    dwh_env = clickhouse_proxy.build_env("dwh", COMBINED_ENV)

    assert wms_env["CLICKHOUSE_HOST"] == "wms.example.internal"
    assert dwh_env["CLICKHOUSE_HOST"] == "dwh.example.internal"
    assert wms_env["CLICKHOUSE_HOST"] != dwh_env["CLICKHOUSE_HOST"]

    assert wms_env["CLICKHOUSE_USER"] == "wms-user"
    assert dwh_env["CLICKHOUSE_USER"] == "dwh-user"
    assert wms_env["CLICKHOUSE_PASSWORD"] == "wms-pass"
    assert dwh_env["CLICKHOUSE_PASSWORD"] == "dwh-pass"

    assert wms_env["CLICKHOUSE_PORT"] == "8123"
    assert dwh_env["CLICKHOUSE_PORT"] == "9440"
    assert wms_env["CLICKHOUSE_SECURE"] == "false"
    assert dwh_env["CLICKHOUSE_SECURE"] == "true"


def test_port_and_secure_default_when_absent():
    minimal = {"CH_WMS_HOST": "h", "CH_WMS_USER": "u", "CH_WMS_PASSWORD": "p"}
    env = clickhouse_proxy.build_env("wms", minimal)
    assert env["CLICKHOUSE_PORT"] == "8123"
    assert env["CLICKHOUSE_SECURE"] == "false"


def test_static_constants_present():
    env = clickhouse_proxy.build_env("wms", COMBINED_ENV)
    assert env["MCP_TRANSPORT"] == "stdio"
    assert env["CLICKHOUSE_VERIFY"] == "false"
    assert env["CHDB_ENABLED"] == "false"


def test_missing_required_var_fails_fast_with_clear_message():
    incomplete = {"CH_WMS_HOST": "h"}  # нет USER/PASSWORD
    try:
        clickhouse_proxy.build_env("wms", incomplete)
        assert False, "должен был упасть"
    except SystemExit as e:
        assert "CH_WMS_USER" in str(e)
        assert "CH_WMS_PASSWORD" in str(e)


def test_unknown_cluster_rejected():
    try:
        clickhouse_proxy.build_env("prod", COMBINED_ENV)
        assert False, "должен был упасть"
    except SystemExit as e:
        assert "prod" in str(e)


def test_source_vars_of_the_other_cluster_are_not_required():
    """WMS-only окружение (аналитик не заполнил DWH) не должно требовать
    CH_DWH_* — коннекторы независимы, как и раньше в .mcp.json."""
    wms_only = {"CH_WMS_HOST": "h", "CH_WMS_USER": "u", "CH_WMS_PASSWORD": "p"}
    env = clickhouse_proxy.build_env("wms", wms_only)
    assert env["CLICKHOUSE_HOST"] == "h"
