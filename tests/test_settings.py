"""Пресет разрешений и правила, вкопанные в CLAUDE.md.

Находки 13 и 14 финального ревью: в .claude/settings.json были только хуки —
аналитик подтверждал бы каждый вызов коннектора и каждую запись файла в work/;
а пять правил безопасности лежали в context/rules.md, который агент может и не
прочитать.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
ALLOW = SETTINGS.get("permissions", {}).get("allow", [])


def test_reading_and_working_in_work_are_preapproved():
    assert "Read" in ALLOW
    assert "Write(work/**)" in ALLOW
    assert "Edit(work/**)" in ALLOW


def test_read_only_connector_tools_are_preapproved():
    for tool in ("mcp__clickhouse__run_query",
                 "mcp__trino__execute_query",
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
