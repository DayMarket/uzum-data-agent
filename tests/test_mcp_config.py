import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
EXPECTED = {"atlassian", "clickhouse", "trino", "superset", "grafana",
            "openmetadata", "growthbook", "sheets"}

# Структурные значения — адреса/пути. Не секрет, поэтому дефолт через
# ${VAR:-значение} допустим: .mcp.json тогда работает "из коробки" без
# лишних warning в `claude mcp list`, пока человек ещё не заполнил secrets.env.
#
# CH_SECURE — тоже структурное значение (схема http/https для рабочего
# коннектора clickhouse), а не секрет. Раньше CLICKHOUSE_SECURE был
# захардкожен литералом "true", хотя реальный эндпоинт отвечает по http на
# 8123 (тот же дефект чинили в lib/telemetry.py) — коннектор "успешно
# устанавливался", но не подключался в первой же сессии. Дефолт "false"
# отражает рабочую схему; setup.sh пишет в CH_SECURE результат своего же
# смоук-теста (какая схема реально ответила), а не гадает заранее.
STRUCTURAL_VARS = ("CH_HOST", "CH_SECURE", "JIRA_URL", "CONFLUENCE_URL")

# Настоящие секреты — пароли и токены. Дефолт здесь значит зашитый в git секрет
# (пример регрессии: "${CH_PASSWORD:-hardcoded-secret}" всё ещё подстановка
# переменной по форме, но с утекшим паролем внутри), поэтому подстановка
# обязана быть голой: ${VAR} без ":-значение". Новый коннектор — новый
# секретный env var — дописывай сюда, а не в STRUCTURAL_VARS.
SECRET_VARS = ("CH_USER", "CH_PASSWORD", "JIRA_TOKEN", "GRAFANA_TOKEN",
               "OMD_TOKEN", "GROWTHBOOK_TOKEN")


def test_all_eight_servers_present():
    assert set(CONFIG["mcpServers"]) == EXPECTED


def test_no_literal_secrets_in_config():
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    # (?i): ключи в .mcp.json — CLICKHOUSE_PASSWORD, GRAFANA_API_KEY и т.п. —
    # верхним регистром; регистрозависимый "password" их не ловил.
    for pattern in (r"sk-ant-", r"ghp_", r"Bearer [A-Za-z0-9]{12,}", r"(?i)password\"\s*:\s*\"[^$]"):
        assert not re.search(pattern, raw), pattern


def test_structural_values_allow_default():
    """CH_HOST/JIRA_URL/CONFLUENCE_URL — адреса, дефолт через :- разрешён."""
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in STRUCTURAL_VARS:
        assert re.search(r"\$\{%s(:-[^}]*)?\}" % re.escape(var), raw), var


def test_secrets_forbid_default():
    """Пароли/токены — только голая ${VAR}, без :-значение по умолчанию.

    Дефолт у секрета — это буквально зашитый в git пароль/токен, даже если он
    формально всё ещё "подстановка переменной". Ловим регрессию отдельно от
    test_structural_values_allow_default, чтобы будущий копипаст дефолта с
    JIRA_URL на, скажем, CH_PASSWORD, ронял тест, а не проходил его.
    """
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in SECRET_VARS:
        assert re.search(r"\$\{%s\}" % re.escape(var), raw), f"{var}: нет голой подстановки ${{{var}}}"
        assert not re.search(r"\$\{%s:-" % re.escape(var), raw), f"{var}: секрету нельзя задавать дефолт"


def test_local_scripts_exist():
    for name in ("trino_proxy.py", "superset_mcp.py", "sheets_mcp.py"):
        assert (REPO_ROOT / "connectors" / name).exists()


def test_clickhouse_secure_is_not_a_hardcoded_literal():
    """Регрессия на находку ревью задачи 9: CLICKHOUSE_SECURE был захардкожен
    `"true"`, хотя реальный ClickHouse-эндпоинт отвечает по http на 8123, а не
    по https — коннектор считался бы настроенным и не подключался в первой же
    сессии. Схема должна приходить из CH_SECURE (результат смоук-теста
    setup.sh), а не быть вкопанной в файл строкой.
    """
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    assert '"CLICKHOUSE_SECURE": "true"' not in raw
    assert '"CLICKHOUSE_SECURE": "false"' not in raw
    assert re.search(r'"CLICKHOUSE_SECURE":\s*"\$\{CH_SECURE(:-[^}]*)?\}"', raw)
