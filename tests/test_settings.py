"""Пресет разрешений и правила, вкопанные в CLAUDE.md.

Находки 13 и 14 финального ревью: в .claude/settings.json были только хуки —
аналитик подтверждал бы каждый вызов коннектора и каждую запись файла в work/;
а пять правил безопасности лежали в context/rules.md, который агент может и не
прочитать.

Находка 1 финального ревью перед раздачей репозитория: пресет предодобрял
`mcp__trino__execute_query`, `mcp__superset__sql_query` и
`mcp__clickhouse__run_query` — свои коннекторы (trino_proxy.py,
superset_mcp.py) не ограничивают запрос до чтения, INSERT/ALTER/CREATE прошли
бы без подтверждения человека, а `mcp__clickhouse__run_query` из стороннего
пакета mcp-clickhouse тоже не в allow — решение владельца: подтверждение на
любой SQL остаётся для всех трёх одинаково, парсить SQL на «безопасный или
нет» не пытаемся. Заодно: `~/.config/uzum-ai/secrets.env` и `.env` в корне —
единственное место, где лежат пароли и токены в открытом виде (см.
connectors/ACCESS.md, README.md), поэтому Read их не видит.

ClickHouse разведён на два коннектора (`clickhouse-wms`, `clickhouse-dwh` —
складской и общий DWH, разные учётки). Политика та же для обоих: read-only
инструменты (`list_databases`/`list_tables`) в allow, `run_query` — нет.
"""
import re
from pathlib import Path

import json

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
ALLOW = SETTINGS.get("permissions", {}).get("allow", [])
DENY = SETTINGS.get("permissions", {}).get("deny", [])

SQL_EXECUTION_DESCRIPTION_RE = re.compile(r"(?i)execute\s+(a\s+)?sql\s+query")


def _tool_names_with_sql_execution_capability(source_text):
    """Найти в исходнике коннектора инструменты, которые выполняют
    произвольный SQL — по описанию вида "Execute SQL query..." рядом с
    объявлением инструмента, а не по захардкоженному имени: переименование
    инструмента не должно тихо снимать защиту."""
    names = []
    name_pattern = re.compile(r'\bname"?\s*[=:]\s*"([a-zA-Z0-9_]+)"')
    matches = list(name_pattern.finditer(source_text))
    for i, m in enumerate(matches):
        window_end = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 1000
        window = source_text[m.end():window_end]
        if SQL_EXECUTION_DESCRIPTION_RE.search(window):
            names.append(m.group(1))
    return names


def test_reading_and_working_in_work_are_preapproved():
    assert "Read" in ALLOW
    assert "Write(work/**)" in ALLOW
    assert "Edit(work/**)" in ALLOW


def test_read_only_connector_tools_are_preapproved():
    for tool in ("mcp__clickhouse-wms__list_tables",
                 "mcp__clickhouse-dwh__list_tables",
                 "mcp__trino__describe_table",
                 "mcp__superset__get_dashboard",
                 "mcp__atlassian__jira_get_issue",
                 "mcp__grafana__search_dashboards",
                 "mcp__openmetadata__get_table_by_fqn"):
        assert tool in ALLOW, tool


def test_nothing_destructive_is_preapproved():
    """Разрешено только чтение и работа в своей папке. Всё, что меняет
    продовые объекты, Jira или чужие таблицы, аналитик подтверждает руками —
    это прямое следствие правил 2-5 из CLAUDE.md."""
    forbidden_marks = ("create", "update", "delete", "patch", "normalize",
                       "append", "transition", "add_comment", "call_api")
    for rule in ALLOW:
        low = rule.lower()
        assert not any(mark in low for mark in forbidden_marks), rule
    # запись вне work/ и произвольный Bash не должны быть разрешены
    assert "Write" not in ALLOW
    assert "Edit" not in ALLOW
    assert "Bash" not in ALLOW
    for rule in ALLOW:
        if rule.startswith(("Write(", "Edit(")):
            assert rule.startswith(("Write(work/", "Edit(work/")), rule


def test_arbitrary_sql_tools_are_not_preapproved():
    """Находка 1 финального ревью: substring-фильтр выше (create/update/...)
    не ловил sql_query/execute_query/run_query — ни одно из этих слов не
    входит в forbidden_marks. Здесь проверяем возможность, а не ярлык: ищем
    в самих коннекторах инструменты, которые исполняют произвольный SQL, и
    убеждаемся, что они не в allow — вне зависимости от того, как их назвали.
    """
    trino_src = (REPO_ROOT / "connectors" / "trino_proxy.py").read_text(encoding="utf-8")
    superset_src = (REPO_ROOT / "connectors" / "superset_mcp.py").read_text(encoding="utf-8")

    trino_sql_tools = _tool_names_with_sql_execution_capability(trino_src)
    superset_sql_tools = _tool_names_with_sql_execution_capability(superset_src)

    # Если регэксп перестал находить хоть один такой инструмент — сломался
    # сам детектор, а не коннектор: тест обязан это заметить, а не молча
    # пройти с пустым списком.
    assert trino_sql_tools, "не нашли ни одного инструмента с произвольным SQL в trino_proxy.py"
    assert superset_sql_tools, "не нашли ни одного инструмента с произвольным SQL в superset_mcp.py"

    for name in trino_sql_tools:
        assert f"mcp__trino__{name}" not in ALLOW, name
    for name in superset_sql_tools:
        assert f"mcp__superset__{name}" not in ALLOW, name

    # clickhouse-wms/clickhouse-dwh — сторонний пакет mcp-clickhouse (не наш
    # исходник в этом репо), детектором по тексту не достать, для обоих
    # коннекторов один и тот же пакет под разными переменными окружения.
    # Инструмент run_query подтверждён живым запуском (`uvx --with pyarrow
    # --from mcp-clickhouse python -c "import mcp_clickhouse; ..."`):
    # выполняет произвольный SQL, write-защита включается отдельной
    # переменной окружения CLICKHOUSE_ALLOW_WRITE_ACCESS, а не самой
    # MCP-схемой инструмента — то есть полагаться на "он и так read-only"
    # нельзя, политика для всех четырёх инструментов (два коннектора x
    # run_query) одна: подтверждение человека на каждый вызов.
    assert "mcp__clickhouse-wms__run_query" not in ALLOW
    assert "mcp__clickhouse-dwh__run_query" not in ALLOW

    # Бэкстоп: даже если детектор выше сломают, эти конкретные имена не
    # должны вернуться в allow.
    for tool in ("mcp__trino__execute_query", "mcp__superset__sql_query",
                 "mcp__clickhouse-wms__run_query", "mcp__clickhouse-dwh__run_query",
                 "mcp__clickhouse__run_query"):
        assert tool not in ALLOW, tool


def test_secrets_paths_are_denied_to_read():
    """Находка 1: Read был разрешён безусловно, а секреты лежат открытым
    текстом в ~/.config/uzum-ai/secrets.env (канонический файл) и в .env в
    корне репозитория (README: "хранит пароли в открытом виде"). Deny должен
    закрывать оба, иначе Read → WebFetch на произвольный адрес — рабочая
    цепочка утечки."""
    assert "Read(~/.config/uzum-ai/**)" in DENY
    assert "Read(.env)" in DENY


def test_hooks_survived_next_to_permissions():
    assert set(SETTINGS["hooks"]) >= {
        "SessionStart", "SessionEnd", "UserPromptSubmit",
        "PostToolUse", "PostToolUseFailure"}


def test_five_rules_are_in_claude_md_itself():
    """Правило, до которого агент может не дойти, правилом не является."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for mark in ("Зарплатные данные", "продовые витрины", "без ревью аналитика",
                 "Jira не меняешь", "Чужие дашборды"):
        assert mark in text, mark
    assert "context/rules.md" in text  # развёрнутая версия на месте
