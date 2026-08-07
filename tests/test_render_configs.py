"""Тесты на единый источник описания коннекторов и его генераторы.

Проверяют поведение, не наличие функций: из одного registry.CONNECTORS
получаются оба конфига (Claude Code и Codex), они согласованы между собой
(один набор имён коннекторов, один набор переменных окружения на каждый),
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


def _mcp_json_servers():
    return json.loads(render_configs.render_mcp_json(CONNECTORS))["mcpServers"]


def _codex_toml_text():
    return render_configs.render_codex_toml(CONNECTORS, repo_root=REPO_ROOT)


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
    """Набор имён переменных окружения, которые реально дойдут до процесса
    коннектора (target-имена), должен совпадать между форматами — иначе
    один из движков молча недополучит параметр."""
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        expected_targets = {item.target if isinstance(item, EnvVar) else item.name
                             for item in connector.env}

        mcp_env_keys = set(mcp_servers[connector.id].get("env", {}))
        assert mcp_env_keys == expected_targets, connector.id

        codex_server = codex_servers[connector.id]
        codex_env_keys = set(codex_server.get("env", {})) | set(codex_server.get("env_vars", []))
        assert codex_env_keys == expected_targets, connector.id


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
        server = servers[connector.id]
        literal_env = server.get("env", {})
        for item in connector.env_vars():
            if item.secret:
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
    защиту переименованием поля."""
    for connector in CONNECTORS:
        for item in connector.env_vars():
            if item.secret:
                assert item.default is None, (connector.id, item.target)


# ── Генератор не теряет параметры ──────────────────────────────────────────

def test_clickhouse_port_and_secure_flag_survive_both_formats():
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector_id, prefix in (("clickhouse-wms", "CH_WMS"), ("clickhouse-dwh", "CH_DWH")):
        mcp_env = mcp_servers[connector_id]["env"]
        assert mcp_env["CLICKHOUSE_PORT"] == "${%s_PORT:-8123}" % prefix
        assert mcp_env["CLICKHOUSE_SECURE"] == "${%s_SECURE:-false}" % prefix

        codex_env_names = set(codex_servers[connector_id].get("env", {})) | \
            set(codex_servers[connector_id].get("env_vars", []))
        assert "CLICKHOUSE_PORT" in codex_env_names
        assert "CLICKHOUSE_SECURE" in codex_env_names


def test_extra_static_env_vars_survive_both_formats():
    """MCP_TRANSPORT/CLICKHOUSE_VERIFY/CHDB_ENABLED — доп. параметры без
    источника в .env, теряются легче всего, потому что не выглядят как
    «секрет» или «структурное значение»."""
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector_id in ("clickhouse-wms", "clickhouse-dwh"):
        mcp_env = mcp_servers[connector_id]["env"]
        assert mcp_env["MCP_TRANSPORT"] == "stdio"
        assert mcp_env["CLICKHOUSE_VERIFY"] == "false"
        assert mcp_env["CHDB_ENABLED"] == "false"

        codex_env = codex_servers[connector_id].get("env", {})
        assert codex_env.get("MCP_TRANSPORT") == "stdio"
        assert codex_env.get("CLICKHOUSE_VERIFY") == "false"
        assert codex_env.get("CHDB_ENABLED") == "false"


def test_launch_args_survive_both_formats():
    mcp_servers = _mcp_json_servers()
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        mcp_args = mcp_servers[connector.id]["args"]
        codex_args = codex_servers[connector.id]["args"]
        assert len(mcp_args) == len(codex_args) == len(connector.args), connector.id
        for original, rendered_codex in zip(connector.args, codex_args):
            if isinstance(original, ProjectScript):
                assert rendered_codex == str(REPO_ROOT / original.path)
            else:
                assert rendered_codex == original
        assert mcp_servers[connector.id]["command"] == connector.command
        assert codex_servers[connector.id]["command"] == connector.command


def test_project_script_args_use_claude_project_dir_macro_in_mcp_json():
    mcp_servers = _mcp_json_servers()
    for connector in CONNECTORS:
        for item in connector.args:
            if isinstance(item, ProjectScript):
                assert f"${{CLAUDE_PROJECT_DIR:-.}}/{item.path}" in mcp_servers[connector.id]["args"]


def test_project_script_args_are_absolute_paths_in_codex_toml():
    """У Codex ${VAR} не подставляется (см. docs/codex-facts.md, раздел 4)
    — путь к локальному скрипту обязан быть абсолютным, посчитанным во время
    генерации, а не строкой-плейсхолдером с макросом, которого Codex не
    понимает."""
    codex_servers = _codex_servers()
    for connector in CONNECTORS:
        for item in connector.args:
            if isinstance(item, ProjectScript):
                absolute = str(REPO_ROOT / item.path)
                assert absolute in codex_servers[connector.id]["args"]
                assert "${" not in " ".join(codex_servers[connector.id]["args"])


# ── Сверка с текущим .mcp.json ──────────────────────────────────────────

def test_generated_mcp_json_matches_current_file_on_disk():
    """Главная проверка задачи: перенос в registry.py точный. Сравниваем как
    данные (не текстом), чтобы разница в порядке ключей не считалась
    расхождением — как и разрешено в задании."""
    current = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
    generated = json.loads(render_configs.render_mcp_json(CONNECTORS))
    assert generated == current
