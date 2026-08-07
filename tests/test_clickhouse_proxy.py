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


def test_the_other_clusters_secrets_do_not_leak_into_the_child_environment():
    """Находка ревью: реальная запускалка одним `source` секретов выставляет
    в окружение ОБА набора (CH_WMS_* и CH_DWH_*) разом — так уже устроено для
    Claude Code. Раньше build_env строил результат как `dict(source_env)` и
    ДОПИСЫВАЛ CLICKHOUSE_*, то есть весь необъяснённый мусор из source_env
    (включая CH_DWH_PASSWORD, когда собирали wms) утекал в дочерний процесс
    как есть. Дочерний процесс (mcp-clickhouse) читает только CLICKHOUSE_*,
    но секрет соседнего кластера всё равно оказывался в его os.environ —
    доступен через /proc, любой дочерний subprocess, дамп на падении и т.д.

    Правильное поведение: результат build_env строится с нуля, явным
    минимальным списком (CLICKHOUSE_* для СВОЕГО кластера плюс объяснённые
    системные вроде PATH/HOME) — ничего из source_env не должно попадать
    внутрь просто потому, что оно там было."""
    wms_env = clickhouse_proxy.build_env("wms", COMBINED_ENV)

    assert "CH_DWH_HOST" not in wms_env
    assert "CH_DWH_USER" not in wms_env
    assert "CH_DWH_PASSWORD" not in wms_env
    assert "CH_DWH_PORT" not in wms_env
    assert "CH_DWH_SECURE" not in wms_env
    # Свои собственные CH_WMS_*-имена (не переименованные в CLICKHOUSE_*)
    # тоже не должны задваиваться в результате.
    assert "CH_WMS_HOST" not in wms_env
    assert "CH_WMS_PASSWORD" not in wms_env
    # Посторонний мусор из source_env не должен просачиваться вообще.
    assert "UNRELATED" not in wms_env

    dwh_env = clickhouse_proxy.build_env("dwh", COMBINED_ENV)
    assert "CH_WMS_HOST" not in dwh_env
    assert "CH_WMS_PASSWORD" not in dwh_env
    assert "UNRELATED" not in dwh_env


def test_child_environment_is_limited_to_an_explicit_allowlist():
    """Дополнительная защита от регрессии: перечисляем ВСЕ ключи, которые
    вообще имеют право оказаться в результате, а не только отсутствие
    секретов соседа — иначе завтрашняя новая "системная" переменная могла бы
    молча начать копироваться так же, как раньше копировалось всё целиком."""
    allowed_keys = {
        "CLICKHOUSE_HOST", "CLICKHOUSE_USER", "CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_PORT", "CLICKHOUSE_SECURE",
        "MCP_TRANSPORT", "CLICKHOUSE_VERIFY", "CHDB_ENABLED",
    } | set(clickhouse_proxy.PASSTHROUGH_SYSTEM_VARS)

    env_with_system_vars = dict(COMBINED_ENV)
    env_with_system_vars["PATH"] = "/usr/bin:/bin"
    env_with_system_vars["HOME"] = "/Users/someone"
    env_with_system_vars["SOME_FUTURE_LEAK"] = "should-never-appear"

    result = clickhouse_proxy.build_env("wms", env_with_system_vars)
    assert set(result) <= allowed_keys, set(result) - allowed_keys
