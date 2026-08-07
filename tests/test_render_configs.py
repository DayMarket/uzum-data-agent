"""Тесты на единый источник описания коннекторов и его генераторы.

Проверяют поведение, не наличие функций: из одного registry.CONNECTORS
получаются оба конфига (Claude Code и Codex), они согласованы между собой
(один набор имён коннекторов, один набор переменных окружения на каждый —
с поправкой на то, что у clickhouse-wms/clickhouse-dwh Codex запускается
через отдельную обёртку, см. connectors/registry.py, "РЕШЁННАЯ НАХОДКА"),
ни в одном из форматов нет литерального секрета, и генератор не теряет
параметры коннектора (порт, признак защищённого соединения, дополнительные
переменные, аргументы запуска).

Секретов в этом файле нет — только имена переменных и выдуманные значения
для смоук-теста подстановки (см. test_fake_secret_value_never_appears_literally).
"""
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import render_configs  # noqa: E402
from connectors.registry import CONNECTORS, EnvVar, StaticEnv, ProjectScript  # noqa: E402

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - тесты гоняются под 3.12 (см. README)
    tomllib = None

EXPECTED_IDS = {"atlassian", "clickhouse-wms", "clickhouse-dwh", "trino", "superset",
                 "grafana", "openmetadata", "growthbook", "sheets"}


def _codex_spec(connector):
    """(command, args, env) — то, что реально пойдёт в Codex для этого
    коннектора: своя ветка (`connector.codex`), если она есть, иначе общая
    с Claude Code. Дублирует _codex_spec из render_configs.py по смыслу —
    тест должен знать, ЧТО именно должно оказаться в выводе, независимо от
    внутренней реализации генератора."""
    if connector.codex is not None:
        return connector.codex.command, connector.codex.args, connector.codex.env
    return connector.command, connector.args, connector.env


def _mcp_json_servers():
    return json.loads(render_configs.render_mcp_json(CONNECTORS))["mcpServers"]


def _codex_toml_text():
    return render_configs.render_codex_toml(CONNECTORS)


def _codex_servers():
    assert tomllib is not None, "тест требует Python 3.11+ (tomllib) — гоняй через uv run --python 3.12"
    return tomllib.loads(_codex_toml_text())["mcp_servers"]


def test_registry_has_nine_connectors_with_expected_ids():
    assert {c.id for c in CONNECTORS} == EXPECTED_IDS
    assert len(CONNECTORS) == 9


# ── Согласованность между форматами ─────────────────────────────────────

def test_same_connector_names_in_both_formats():
    mcp_ids = set(_mcp_json_servers())
    codex_ids = set(_codex_servers())
    assert mcp_ids == EXPECTED_IDS
    assert codex_ids == EXPECTED_IDS
    assert mcp_ids == codex_ids


def test_same_env_var_set_per_connector_in_both_formats():
    """Набор имён переменных окружения, которые реально дойдут до процесса,
    запущенного из args этого коннектора, должен совпадать между форматами
    — иначе один из движков молча недополучит параметр.

    Для clickhouse-wms/clickhouse-dwh "процесс, запущенный из args" — это
    сам пакет mcp-clickhouse у Claude Code, но обёртка clickhouse_proxy.py у
    Codex (см. connectors/registry.py) — поэтому ожидаемый набор имён для
    Codex берём из connector.codex, а не connector.env."""
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        expected_mcp_targets = {item.target if isinstance(item, EnvVar) else item.name
                                 for item in connector.env}
        mcp_env_keys = set(mcp_servers[connector.id].get("env", {}))
        assert mcp_env_keys == expected_mcp_targets, connector.id

        _, _, codex_env_items = _codex_spec(connector)
        expected_codex_targets = {item.target if isinstance(item, EnvVar) else item.name
                                   for item in codex_env_items}
        codex_server = codex_servers[connector.id]
        codex_env_keys = set(codex_server.get("env", {})) | set(codex_server.get("env_vars", []))
        assert codex_env_keys == expected_codex_targets, connector.id


def test_clickhouse_codex_env_vars_use_prefixed_names_not_generic_clickhouse_names():
    """Регрессия на найденную ревью коллизию: если бы Codex forward'ил
    'CLICKHOUSE_HOST' для обоих кластеров, они получили бы одно и то же
    значение (общее окружение codex на все дочерние MCP-серверы, см.
    connectors/registry.py). У обёртки нет этой проблемы, потому что она
    просит переслать CH_WMS__*/CH_DWH_* — разные имена, без пересечения."""
    codex_servers = _codex_servers()
    wms_vars = set(codex_servers["clickhouse-wms"]["env_vars"])
    dwh_vars = set(codex_servers["clickhouse-dwh"]["env_vars"])
    assert wms_vars == {"CH_WMS_HOST", "CH_WMS_PORT", "CH_WMS_USER", "CH_WMS_PASSWORD", "CH_WMS_SECURE"}
    assert dwh_vars == {"CH_DWH_HOST", "CH_DWH_PORT", "CH_DWH_USER", "CH_DWH_PASSWORD", "CH_DWH_SECURE"}
    assert wms_vars.isdisjoint(dwh_vars)
    assert "CLICKHOUSE_HOST" not in wms_vars | dwh_vars


def test_clickhouse_codex_launches_the_proxy_wrapper_with_the_right_cluster_arg():
    codex_servers = _codex_servers()
    assert codex_servers["clickhouse-wms"]["command"] == "uv"
    assert codex_servers["clickhouse-wms"]["args"] == ["run", "connectors/clickhouse_proxy.py", "wms"]
    assert codex_servers["clickhouse-dwh"]["args"] == ["run", "connectors/clickhouse_proxy.py", "dwh"]


# ── Секреты ───────────────────────────────────────────────────────────────

def test_no_literal_secrets_in_mcp_json():
    raw = render_configs.render_mcp_json(CONNECTORS)
    for connector in CONNECTORS:
        for item in connector.env_vars():
            if item.secret:
                # Секрет обязан быть голой подстановкой ${SOURCE}, без дефолта.
                assert re.search(r"\$\{%s\}" % re.escape(item.source), raw), (connector.id, item.target)
                assert f'"{item.target}": "{item.source}"' not in raw


def test_no_literal_secrets_in_codex_toml():
    """У Codex секрет обязан идти через env_vars (список имён), не через
    литеральный env-table — иначе значение легло бы в config.toml текстом."""
    servers = _codex_servers()
    for connector in CONNECTORS:
        _, _, codex_env_items = _codex_spec(connector)
        literal_env = servers[connector.id].get("env", {})
        for item in codex_env_items:
            if isinstance(item, EnvVar) and item.secret:
                assert item.target not in literal_env, (
                    f"{connector.id}.{item.target}: секрет не может лежать в "
                    "литеральном env — должен идти через env_vars"
                )


def test_fake_secret_value_never_appears_literally_in_either_format():
    """Смоук-тест подстановки: если бы кто-то по ошибке подставил значение
    секрета прямо в реестр (вместо имени переменной-источника), это всплыло
    бы тут — генератор работает с именами, не со значениями, поэтому
    выдуманное значение никогда не должно попасть в вывод буквально."""
    fake_secret_value = "sk-totally-fake-secret-value-12345"
    for text in (render_configs.render_mcp_json(CONNECTORS), _codex_toml_text()):
        assert fake_secret_value not in text


def test_secret_env_vars_have_no_default():
    """Регистр-уровневая гарантия: EnvVar.__post_init__ роняет секрет с
    дефолтом, но проверим и сам реестр — регрессия не должна тихо обойти
    защиту переименованием поля. Проверяем обе ветки — общую и codex-only."""
    for connector in CONNECTORS:
        for item in connector.env_vars():
            if item.secret:
                assert item.default is None, (connector.id, item.target)
        if connector.codex is not None:
            for item in connector.codex.env:
                if isinstance(item, EnvVar) and item.secret:
                    assert item.default is None, (connector.id, "codex", item.target)


# ── Генератор не теряет параметры ──────────────────────────────────────────

def test_clickhouse_port_and_secure_flag_survive_both_formats():
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector_id, prefix in (("clickhouse-wms", "CH_WMS"), ("clickhouse-dwh", "CH_DWH")):
        mcp_env = mcp_servers[connector_id]["env"]
        assert mcp_env["CLICKHOUSE_PORT"] == "${%s_PORT:-8123}" % prefix
        assert mcp_env["CLICKHOUSE_SECURE"] == "${%s_SECURE:-false}" % prefix

        codex_env_names = set(codex_servers[connector_id].get("env_vars", []))
        assert "%s_PORT" % prefix in codex_env_names
        assert "%s_SECURE" % prefix in codex_env_names


def test_extra_static_env_vars_survive_in_mcp_json_and_inside_the_proxy_wrapper():
    """MCP_TRANSPORT/CLICKHOUSE_VERIFY/CHDB_ENABLED — доп. параметры без
    источника в .env. В .mcp.json они по-прежнему литералы в env. Под Codex
    их расставляет сама обёртка clickhouse_proxy.py (проверено юнит-тестом
    tests/test_clickhouse_proxy.py::test_static_constants_present) — в
    config.toml им уже незачем быть, потому что запущен не сам
    mcp-clickhouse, а обёртка перед ним."""
    mcp_servers = _mcp_json_servers()
    for connector_id in ("clickhouse-wms", "clickhouse-dwh"):
        mcp_env = mcp_servers[connector_id]["env"]
        assert mcp_env["MCP_TRANSPORT"] == "stdio"
        assert mcp_env["CLICKHOUSE_VERIFY"] == "false"
        assert mcp_env["CHDB_ENABLED"] == "false"

    connectors_dir = str(REPO_ROOT / "connectors")
    if connectors_dir not in sys.path:
        sys.path.insert(0, connectors_dir)
    import clickhouse_proxy  # noqa: E402

    built = clickhouse_proxy.build_env("wms", {"CH_WMS_HOST": "h", "CH_WMS_USER": "u", "CH_WMS_PASSWORD": "p"})
    assert built["MCP_TRANSPORT"] == "stdio"
    assert built["CLICKHOUSE_VERIFY"] == "false"
    assert built["CHDB_ENABLED"] == "false"


def test_launch_args_survive_both_formats():
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        codex_command, codex_raw_args, _ = _codex_spec(connector)

        mcp_args = mcp_servers[connector.id]["args"]
        assert len(mcp_args) == len(connector.args), connector.id

        codex_args = codex_servers[connector.id]["args"]
        assert len(codex_args) == len(codex_raw_args), connector.id
        for original, rendered_codex in zip(codex_raw_args, codex_args):
            if isinstance(original, ProjectScript):
                assert rendered_codex == original.path
            else:
                assert rendered_codex == original

        assert mcp_servers[connector.id]["command"] == connector.command
        assert codex_servers[connector.id]["command"] == codex_command


def test_project_script_args_use_claude_project_dir_macro_in_mcp_json():
    mcp_servers = _mcp_json_servers()
    for connector in CONNECTORS:
        for item in connector.args:
            if isinstance(item, ProjectScript):
                assert f"${{CLAUDE_PROJECT_DIR:-.}}/{item.path}" in mcp_servers[connector.id]["args"]


def test_project_script_args_are_relative_paths_in_codex_toml():
    """Относительный путь в args резолвится Codex относительно рабочего
    каталога, из которого запущен сам `codex` — проверено живым
    MCP-рукопожатием (см. отчёт задачи Codex-3): `uv run
    connectors/trino_proxy.py` с относительным путём, запущенный из корня
    репозитория, вернул реальный список инструментов trino. Раньше этот тест
    требовал абсолютный путь — это было основано на непроверенной догадке по
    аналогии с фактом про ${VAR} (который действительно не подставляется),
    а не на факте про args; догадка оказалась неверной."""
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        _, codex_raw_args, _ = _codex_spec(connector)
        for item in codex_raw_args:
            if isinstance(item, ProjectScript):
                assert item.path in codex_servers[connector.id]["args"]
                assert "${" not in " ".join(codex_servers[connector.id]["args"])
                for a in codex_servers[connector.id]["args"]:
                    assert not a.startswith("/"), (connector.id, a)


# ── TOML: экранирование управляющих символов ────────────────────────────

def test_toml_writer_escapes_control_characters_and_round_trips():
    """Значение с переводом строки не должно давать невалидный TOML.
    Сегодня таких значений в реестре нет (см. registry.py) — тест защищает
    от будущей регрессии дёшево, напрямую через внутренний _toml_string."""
    tricky = 'line1\nline2\ttabbed"quoted"\\backslash\\'
    rendered = render_configs._toml_string(tricky)
    assert tomllib is not None
    parsed = tomllib.loads("value = %s" % rendered)
    assert parsed["value"] == tricky


# ── Сверка с текущим .mcp.json / .codex/config.toml ─────────────────────

def test_generated_mcp_json_matches_current_file_on_disk():
    """Главная проверка задачи: перенос в registry.py точный. Сравниваем как
    данные (не текстом), чтобы разница в порядке ключей не считалась
    расхождением — как и разрешено в задании."""
    current = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    generated = json.loads(render_configs.render_mcp_json(CONNECTORS))
    assert generated == current


def test_generated_codex_toml_matches_current_file_on_disk():
    """Тот же принцип, что и для .mcp.json, теперь применим и к Codex: с
    относительными путями config.toml больше не привязан к диску конкретного
    аналитика (см. .gitignore — файл больше не исключён), поэтому его можно
    держать в git и ловить расхождение с реестром так же."""
    current_path = REPO_ROOT / ".codex" / "config.toml"
    assert current_path.exists(), ".codex/config.toml должен быть сгенерирован и закоммичен"
    current = tomllib.loads(current_path.read_text(encoding="utf-8"))
    generated = tomllib.loads(render_configs.render_codex_toml(CONNECTORS))
    assert generated == current
