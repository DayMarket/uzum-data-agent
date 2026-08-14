import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
EXPECTED = {"atlassian", "clickhouse-wms", "clickhouse-dwh", "trino", "superset",
            "grafana", "openmetadata", "growthbook", "sheets"}

# Структурные значения — адреса/пути. Не секрет, поэтому дефолт через
# ${VAR:-значение} допустим: .mcp.json тогда работает "из коробки" без
# лишних warning в `claude mcp list`, пока человек ещё не заполнил secrets.env.
#
# CH_WMS_SECURE/CH_DWH_SECURE — тоже структурные значения (схема http/https
# для каждого из двух коннекторов ClickHouse — складского и общего DWH), а не
# секрет. Раньше (один коннектор, один CH_SECURE) CLICKHOUSE_SECURE был
# захардкожен литералом "true", хотя реальный эндпоинт отвечает по http на
# 8123 (тот же дефект чинили в lib/telemetry.py) — коннектор "успешно
# устанавливался", но не подключался в первой же сессии. Дефолт "false"
# отражает рабочую схему; setup.sh пишет в CH_WMS_SECURE/CH_DWH_SECURE
# результат своего же смоук-теста (какая схема реально ответила), а не гадает
# заранее.
STRUCTURAL_VARS = ("CH_WMS_HOST", "CH_WMS_SECURE", "CH_DWH_HOST", "CH_DWH_SECURE",
                    "JIRA_URL", "CONFLUENCE_URL")

# Настоящие секреты — пароли и токены. Дефолт здесь значит зашитый в git секрет
# (пример регрессии: "${CH_WMS_PASSWORD:-hardcoded-secret}" всё ещё подстановка
# переменной по форме, но с утекшим паролем внутри), поэтому подстановка
# обязана быть голой: ${VAR} без ":-значение". Новый коннектор — новый
# секретный env var — дописывай сюда, а не в STRUCTURAL_VARS. WMS и DWH —
# разные учётки на разных кластерах, поэтому у каждого свои CH_*_USER/
# CH_*_PASSWORD, а не общая пара на двоих.
SECRET_VARS = ("CH_WMS_USER", "CH_WMS_PASSWORD", "CH_DWH_USER", "CH_DWH_PASSWORD",
               "JIRA_TOKEN", "GRAFANA_TOKEN", "OMD_TOKEN", "GROWTHBOOK_TOKEN")

# Необязательные секреты (registry.EnvVar, required=False): значения нет и
# дефолта быть не может, но коннектор без них работает частично.
# CONFLUENCE_TOKEN — второй токен atlassian, отдельный от JIRA_TOKEN: PAT в
# Atlassian Server/DC действует только в том продукте, где создан (токен
# Jira на Confluence — 401, проверено живым запросом). Рендерится как
# ${VAR:-} — ПУСТОЙ дефолт: не значение, а способ не отдать процессу
# литерал `${VAR}`, когда переменной нет (см. tools/render_configs.py).
OPTIONAL_SECRET_VARS = ("CONFLUENCE_TOKEN",)


def test_all_nine_servers_present():
    assert set(CONFIG["mcpServers"]) == EXPECTED


def test_no_literal_secrets_in_config():
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    # (?i): ключи в .mcp.json — CLICKHOUSE_PASSWORD, GRAFANA_API_KEY и т.п. —
    # верхним регистром; регистрозависимый "password" их не ловил.
    for pattern in (r"sk-ant-", r"ghp_", r"Bearer [A-Za-z0-9]{12,}", r"(?i)password\"\s*:\s*\"[^$]"):
        assert not re.search(pattern, raw), pattern


def test_structural_values_allow_default():
    """CH_WMS_HOST/CH_DWH_HOST/JIRA_URL/CONFLUENCE_URL — адреса, дефолт через
    :- разрешён."""
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in STRUCTURAL_VARS:
        assert re.search(r"\$\{%s(:-[^}]*)?\}" % re.escape(var), raw), var


def test_secrets_forbid_default():
    """Пароли/токены — только голая ${VAR}, без :-значение по умолчанию.

    Дефолт у секрета — это буквально зашитый в git пароль/токен, даже если он
    формально всё ещё "подстановка переменной". Ловим регрессию отдельно от
    test_structural_values_allow_default, чтобы будущий копипаст дефолта с
    JIRA_URL на, скажем, CH_WMS_PASSWORD, ронял тест, а не проходил его.
    """
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in SECRET_VARS:
        assert re.search(r"\$\{%s\}" % re.escape(var), raw), f"{var}: нет голой подстановки ${{{var}}}"
        assert not re.search(r"\$\{%s:-" % re.escape(var), raw), f"{var}: секрету нельзя задавать дефолт"


def test_optional_secrets_default_to_empty_and_nothing_else():
    """Необязательный секрет — ровно ${VAR:-}, пустой дефолт. Любой символ
    после `:-` — это уже зашитое в git значение секрета, та же регрессия,
    которую ловит test_secrets_forbid_default."""
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in OPTIONAL_SECRET_VARS:
        assert re.search(r"\$\{%s:-\}" % re.escape(var), raw), (
            f"{var}: нет подстановки ${{{var}:-}} с пустым дефолтом")
        assert not re.search(r"\$\{%s:-[^}]" % re.escape(var), raw), (
            f"{var}: у необязательного секрета дефолт может быть только пустым")
        assert not re.search(r"\$\{%s\}" % re.escape(var), raw), (
            f"{var}: голая ${{{var}}} уехала бы процессу литералом, "
            "когда переменной нет")


def test_local_scripts_exist():
    for name in ("trino_proxy.py", "superset_mcp.py", "sheets_mcp.py",
                 "claude_env_bridge.py"):
        assert (REPO_ROOT / "connectors" / name).exists()


def _real_launch(server):
    """(команда, аргументы) настоящего процесса сервера — то, что стоит после
    `--` в вызове мостика claude_env_bridge (см. tools/render_configs.py::
    render_mcp_json: мостик пересобирает окружение из secrets.env и exec-ает
    эту команду; сессии, поднятые мимо bin/uzum — Claude Desktop, голый
    claude, — иначе получают литеральные `${VAR}` вместо значений)."""
    assert server["command"] == "python3"
    args = server["args"]
    assert args[0] == "${CLAUDE_PROJECT_DIR:-.}/connectors/claude_env_bridge.py"
    assert args[2] == "--"
    return args[3], args[4:]


def test_no_server_is_launched_from_a_nonexistent_pypi_package():
    """`uvx mcp-growthbook` — пакета с таким именем на PyPI нет (это
    npm-пакет, `@growthbook/mcp`, запускается через `npx`), а `uvx
    mcp-openmetadata` ставится, но не даёт исполняемого файла ("does not
    provide any executables") — нужен запуск модулем.

    mcp-grafana, наоборот, на PyPI ЕСТЬ: находка финального ревью перед
    раздачей репозитория, что здесь было наоборот записано неверно ("пакета
    нет, нужен brew install"). Проверено запуском: `uvx mcp-grafana
    --version` ставит пакет и печатает `v1.0.0` — тот же бинарь, что кладёт
    `brew install mcp-grafana` (тот же апстрим, grafana/mcp-grafana, та же
    версия). Поэтому grafana запускается как `uvx mcp-grafana`, так же, как
    остальные пакетные серверы — без обязательного `brew install`.
    """
    servers = CONFIG["mcpServers"]
    grafana_cmd, grafana_args = _real_launch(servers["grafana"])
    assert grafana_cmd == "uvx"
    assert grafana_args == ["mcp-grafana"]
    growthbook_cmd, growthbook_args = _real_launch(servers["growthbook"])
    assert growthbook_cmd == "npx"
    assert "@growthbook/mcp" in growthbook_args
    _, omd_args = _real_launch(servers["openmetadata"])
    assert omd_args[-2:] == ["-m", "mcp_openmetadata.server"], (
        "у mcp-openmetadata нет console script — только запуск модулем"
    )
    for name, server in servers.items():
        if name == "grafana":
            continue
        _, args = _real_launch(server)
        assert args[:1] != ["mcp-grafana"]
        assert args[:1] != ["mcp-growthbook"]


def test_connector_env_var_names_match_the_servers_we_actually_run():
    """Имя переменной — часть контракта конкретного сервера, а не наше
    соглашение: grafana/mcp-grafana читает GRAFANA_SERVICE_ACCOUNT_TOKEN
    (GRAFANA_API_KEY он игнорирует), а настройки mcp-openmetadata собраны
    с env_prefix="OPENMETADATA_" и полем `uri` — то есть OPENMETADATA_URI,
    не OPENMETADATA_URL (иначе pydantic роняет сервер на старте).
    """
    servers = CONFIG["mcpServers"]
    assert servers["grafana"]["env"]["GRAFANA_SERVICE_ACCOUNT_TOKEN"] == "${GRAFANA_TOKEN}"
    assert "GRAFANA_API_KEY" not in servers["grafana"]["env"]
    assert servers["openmetadata"]["env"]["OPENMETADATA_URI"] == "${OMD_URL}"
    assert "OPENMETADATA_URL" not in servers["openmetadata"]["env"]
    assert servers["growthbook"]["env"]["GB_API_KEY"] == "${GROWTHBOOK_TOKEN}"


def test_clickhouse_keeps_the_battle_tested_parameter_set():
    """Рабочий (проверенный боем) конфиг mcp-clickhouse запускается как
    `uvx --with pyarrow mcp-clickhouse` и передаёт MCP_TRANSPORT, порт,
    CLICKHOUSE_VERIFY и CHDB_ENABLED. Порт особенно важен: без него
    коннектор идёт на дефолтный 8443, хотя мастер спрашивает порт и
    проверяет им доступ. Оба коннектора (WMS и DWH) — тот же набор
    параметров, каждый со своим префиксом переменных.
    """
    for name, prefix in (("clickhouse-wms", "CH_WMS"), ("clickhouse-dwh", "CH_DWH")):
        ch = CONFIG["mcpServers"][name]
        ch_cmd, ch_args = _real_launch(ch)
        assert ch_cmd == "uvx"
        assert ch_args == ["--with", "pyarrow", "mcp-clickhouse"]
        env = ch["env"]
        assert env["MCP_TRANSPORT"] == "stdio"
        assert env["CLICKHOUSE_PORT"] == "${%s_PORT:-8123}" % prefix
        assert env["CLICKHOUSE_VERIFY"] == "false"
        assert env["CHDB_ENABLED"] == "false"


def test_clickhouse_secure_is_not_a_hardcoded_literal():
    """Регрессия на находку ревью задачи 9: CLICKHOUSE_SECURE был захардкожен
    `"true"`, хотя реальный ClickHouse-эндпоинт отвечает по http на 8123, а не
    по https — коннектор считался бы настроенным и не подключался в первой же
    сессии. Схема должна приходить из CH_WMS_SECURE/CH_DWH_SECURE (результат
    смоук-теста setup.sh для каждого кластера отдельно), а не быть вкопанной
    в файл строкой.
    """
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    assert '"CLICKHOUSE_SECURE": "true"' not in raw
    assert '"CLICKHOUSE_SECURE": "false"' not in raw
    matches = re.findall(r'"CLICKHOUSE_SECURE":\s*"\$\{(CH_\w+_SECURE)(?::-[^}]*)?\}"', raw)
    assert set(matches) == {"CH_WMS_SECURE", "CH_DWH_SECURE"}
