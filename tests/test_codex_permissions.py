"""Codex: разрешения — то же самое, что и `.claude/settings.json`
(tests/test_settings.py), но выраженное в понятиях Codex, а не Claude Code.

У Codex нет списка правил `allow`/`deny` на конкретные MCP-инструменты —
только режим песочницы (`sandbox_mode`), политика подтверждений
(`approval_policy`) и — это ключевая находка задачи Codex-5, подтверждённая
живым запуском (`codex exec` с изолированным `CODEX_HOME` и фейковым
MCP-сервером, см. отчёт задачи) — **решение о том, требует ли конкретный
вызов MCP-инструмента подтверждения человека, принимается Codex ИСКЛЮЧИТЕЛЬНО
по полю `annotations.readOnlyHint` в описании инструмента, которое отдаёт сам
MCP-сервер**. Ни `sandbox_mode`, ни `approval_policy`, ни доверие проекту
(`[projects."<path>"] trust_level = "trusted"`) на это не влияют — проверено
живым запуском во всех комбинациях (`read-only`/`workspace-write`/
`danger-full-access` × `untrusted`/`on-request`/`never` × доверенный/
недоверенный проект): без `readOnlyHint: true` вызов инструмента ВСЕГДА
требует подтверждения, а в headless-режиме (`codex exec`, где спросить
некого) — автоматически отменяется («user cancelled MCP tool call»), если не
передан отдельный флаг `--dangerously-bypass-approvals-and-sandbox` (это
ДРУГОЙ флаг, чем `--dangerously-bypass-hook-trust` для хуков — их легко
перепутать, поэтому оба явно названы здесь). Проверено и обратное: свап
имени и аннотации между двумя инструментами ("run_query" с readOnlyHint и
"list_tables" без) показал, что решение следует за аннотацией, а не за
именем — то есть требование «тест проверяет возможности, а не имена»
выполняется тем же способом, что и в tests/test_settings.py, но для Codex
единица наблюдения — не имя в списке allow, а поле annotations рядом с
описанием инструмента в исходнике коннектора.

Из этого следует практический вывод для профиля: правило 1 (любой SQL
требует подтверждения) выполняется АВТОМАТИЧЕСКИ и надёжно для любого
MCP-инструмента, который не объявляет `readOnlyHint: true` — то есть
безопасное состояние по умолчанию, ничего не нужно включать. Правило 4
(перечисление без подтверждения) в Codex, наоборот, требует ЯВНОГО действия:
инструмент должен объявить `readOnlyHint: true` сам. Это возможно только для
коннекторов, чей исходный код нам принадлежит (trino_proxy.py,
superset_mcp.py — им и добавлены аннотации ниже; sheets_mcp.py отдельно не
трогаем: у него только create_sheet/append_rows, ни одного read-инструмента,
под подтверждением он и так в обоих движках). Для сторонних пакетов
(mcp-clickhouse — clickhouse-wms/clickhouse-dwh, mcp-atlassian, mcp-grafana,
mcp-openmetadata, growthbook mcp) аннотации не объявлены НИ У ОДНОГО
инструмента (проверено чтением исходников/схемы пакетов) — под Codex ЛЮБОЙ
их вызов, включая безобидное перечисление, требует подтверждения. Это не
дыра в безопасности (наоборот, безопасное состояние по умолчанию), но прямое
нарушение духа правила 4 для этих пяти коннекторов — тот же класс трения,
из-за которого не взлетел предыдущий пилот. Подробности и варианты решения —
в отчёте задачи Codex-5 (raздел "что не удалось выразить").

Отдельно, живым запуском проверено и то, что НЕ удалось выразить в принципе
через `.codex/config.toml`, и это НЕ покрывается тестами ниже: чтение
секретов (правило 2) и ограничение записи папкой work/ (правило 3) —
`sandbox_mode` управляет только правами ЗАПИСИ shell-команд, чтение файлов
(включая `.env` и `~/.config/uzum-ai/secrets.env`) им не ограничено ни в
одном режиме (`read-only` включительно — имя означает «без права записи», не
«ограниченное чтение»), а `approval_policy=untrusted` — самая строгая
документированная политика — по официальному описанию флага (`codex exec
--help`) сама объявляет `cat`/`sed`/`ls` доверенными командами без
подтверждения независимо от пути аргумента. Оба факта подтверждены живым
запуском (см. отчёт). Экспериментальная файловая ACL-система `[permissions]`
обнаружена в бинаре (поля filesystem/network с access read/write/deny), но
не документирована, помечена внутри как несовместимая с `sandbox_mode`, и
её точный TOML-синтaксис не установлен за разумное время проб через
`--strict-config` — закладывать в профиль непроверенный синтаксис для
требований безопасности значило бы имитировать защиту, а не создавать её.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - тесты гоняются под 3.12
    tomllib = None

import render_configs  # noqa: E402
from connectors.registry import CONNECTORS  # noqa: E402

TRINO_SRC = (REPO_ROOT / "connectors" / "trino_proxy.py").read_text(encoding="utf-8")
SUPERSET_SRC = (REPO_ROOT / "connectors" / "superset_mcp.py").read_text(encoding="utf-8")

SQL_EXECUTION_DESCRIPTION_RE = re.compile(r"(?i)execute\s+(a\s+)?sql\s+query")
READ_ONLY_HINT_RE = re.compile(r"readOnlyHint[\"']?\s*[:=]\s*True")


def _tool_windows(source_text):
    """Разбить исходник коннектора на окна «после объявления имени
    инструмента и до объявления следующего» — тот же приём, что и в
    tests/test_settings.py::_tool_names_with_sql_execution_capability, но
    здесь дополнительно возвращает сам текст окна, чтобы проверять не
    только факт SQL-возможности, но и наличие/отсутствие readOnlyHint рядом
    с КОНКРЕТНЫМ инструментом — не по имени, а по содержимому его
    собственного описания (annotations идут в том же вызове/словаре, что и
    description, то есть попадают в то же окно)."""
    name_pattern = re.compile(r'"name"\s*:\s*"([a-zA-Z0-9_]+)"|name\s*=\s*"([a-zA-Z0-9_]+)"')
    matches = list(name_pattern.finditer(source_text))
    windows = []
    for i, m in enumerate(matches):
        name = m.group(1) or m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else start + 4000
        windows.append((name, source_text[start:end]))
    return windows


def _sql_execution_tool_names(source_text):
    return [name for name, window in _tool_windows(source_text)
            if SQL_EXECUTION_DESCRIPTION_RE.search(window)]


def _readonly_hint_present_for(source_text, tool_name):
    for name, window in _tool_windows(source_text):
        if name == tool_name:
            return bool(READ_ONLY_HINT_RE.search(window))
    raise AssertionError(f"инструмент {tool_name!r} не найден в исходнике")


# ── Правило 1: произвольный SQL требует подтверждения ──────────────────────

def test_sql_execution_tools_are_found_by_capability_detector():
    """Бэкстоп для самого детектора: если регэксп перестанет находить хоть
    один SQL-инструмент — сломался детектор, а не коннектор, тест обязан
    заметить именно это, а не молча пройти с пустым списком (тот же приём,
    что и в test_settings.py)."""
    assert _sql_execution_tool_names(TRINO_SRC), "не нашли SQL-инструмент в trino_proxy.py"
    assert _sql_execution_tool_names(SUPERSET_SRC), "не нашли SQL-инструмент в superset_mcp.py"


def test_arbitrary_sql_tools_do_not_declare_readonly_hint():
    """Находка задачи Codex-5, проверенная живым запуском: инструмент,
    исполняющий произвольный SQL, не должен объявлять readOnlyHint=true —
    иначе Codex будет пропускать его вызовы без подтверждения человека,
    что прямо нарушает правило 1. Ищем инструмент по возможности (описание
    вида "Execute SQL query..."), а не по имени: переименование
    execute_query/sql_query не должно тихо снимать защиту."""
    for source_text, path in ((TRINO_SRC, "trino_proxy.py"), (SUPERSET_SRC, "superset_mcp.py")):
        for name in _sql_execution_tool_names(source_text):
            assert not _readonly_hint_present_for(source_text, name), (
                f"{path}:{name} объявляет readOnlyHint — Codex пропустит "
                "произвольный SQL без подтверждения"
            )

    # Бэкстоп по конкретным именам — даже если детектор по описанию сломают.
    for name in ("execute_query",):
        assert not _readonly_hint_present_for(TRINO_SRC, name), name
    for name in ("sql_query",):
        assert not _readonly_hint_present_for(SUPERSET_SRC, name), name


# ── Правило 4: перечисление без подтверждения — только там, где мы владеем кодом ──

# Тот же набор инструментов, что уже одобрен для Claude Code в
# .claude/settings.json (mcp__trino__*, mcp__superset__*) — Codex выражает
# «не спрашивать подтверждение» другим механизмом (readOnlyHint у самого
# инструмента, а не allow-список у движка), но целевой набор инструментов
# должен остаться тем же самым, чтобы поведение обоих движков не разъехалось.
TRINO_READ_ONLY_TOOLS = ("list_catalogs", "list_schemas", "list_tables", "describe_table")
SUPERSET_READ_ONLY_TOOLS = (
    "list_dashboards", "list_dashboard_charts", "list_charts", "list_datasets",
    "get_dashboard", "get_dashboard_layout_summary", "get_dashboard_screenshot",
    "get_chart_data", "get_chart_params_summary", "get_chart_screenshot",
    "get_dataset", "get_dataset_summary", "refresh_token",
)


def test_known_read_only_tools_declare_readonly_hint_in_trino_and_superset():
    for name in TRINO_READ_ONLY_TOOLS:
        assert _readonly_hint_present_for(TRINO_SRC, name), (
            f"trino_proxy.py:{name} должен объявлять readOnlyHint=true, "
            "иначе Codex будет спрашивать подтверждение на обычное перечисление"
        )
    for name in SUPERSET_READ_ONLY_TOOLS:
        assert _readonly_hint_present_for(SUPERSET_SRC, name), (
            f"superset_mcp.py:{name} должен объявлять readOnlyHint=true"
        )


def test_mutating_superset_tools_do_not_declare_readonly_hint():
    """Симметричная проверка: инструменты, которых нет в одобренном для
    Claude Code списке (create_*, update_*, delete, patch_*,
    normalize_dashboard_metrics), не должны внезапно стать readOnlyHint=true
    — иначе Codex начнёт пропускать изменения продовых дашбордов без
    подтверждения."""
    all_names = {name for name, _ in _tool_windows(SUPERSET_SRC)}
    mutating = all_names - set(SUPERSET_READ_ONLY_TOOLS) - {"sql_query", "superset-mcp"}
    assert mutating, "не нашли ни одного пишущего инструмента — сломан список сравнения"
    for name in mutating:
        assert not _readonly_hint_present_for(SUPERSET_SRC, name), (
            f"superset_mcp.py:{name} не должен быть readOnlyHint=true"
        )


# ── Общий профиль в .codex/config.toml: не должен быть ослаблен ────────────

def _codex_config():
    assert tomllib is not None, "тест требует Python 3.11+ (tomllib)"
    text = render_configs.render_codex_toml(CONNECTORS)
    return tomllib.loads(text)


def test_generated_codex_toml_matches_current_file_on_disk():
    current_path = REPO_ROOT / ".codex" / "config.toml"
    assert current_path.exists()
    current = tomllib.loads(current_path.read_text(encoding="utf-8"))
    generated = tomllib.loads(render_configs.render_codex_toml(CONNECTORS))
    assert generated == current


def test_sandbox_and_approval_defaults_are_not_dangerously_weak():
    """`sandbox_mode`/`approval_policy` не решают правила 2 и 3 (см. докстринг
    модуля) — но задают поведение shell-инструмента Codex (не MCP), и ослабить
    их до `danger-full-access`/`never` означало бы дополнительно снять даже ту
    защиту, что есть. Профиль обязан явно задавать оба поля, не полагаясь на
    дефолт Codex (который меняется от версии к версии)."""
    config = _codex_config()
    assert config.get("sandbox_mode") in ("read-only", "workspace-write"), config.get("sandbox_mode")
    assert config.get("sandbox_mode") != "danger-full-access"
    assert config.get("approval_policy") in ("untrusted", "on-request"), config.get("approval_policy")
    assert config.get("approval_policy") != "never"


def test_headless_bypass_flag_is_not_part_of_the_generated_profile():
    """`--dangerously-bypass-approvals-and-sandbox` (обход подтверждений в
    headless-режиме) и `--dangerously-bypass-hook-trust` (обход доверия
    хукам) — это флаги ЗАПУСКА для автоматизации без терминала, а не
    настройки профиля (см. докстринг модуля). Ни один из них не должен
    просочиться в сгенерированный config.toml как включённая по умолчанию
    настройка — иначе профиль тихо восстановил бы дыру, которую чинит
    правило 1."""
    raw = render_configs.render_codex_toml(CONNECTORS)
    assert "bypass_approvals" not in raw
    assert "bypass-approvals" not in raw
    assert "dangerously" not in raw.lower()
