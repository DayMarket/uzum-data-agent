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

Правила 2 (запрет чтения секретов/.env) и 3 (запись только в work/) решает
ДРУГОЙ, независимый рычаг: именованный профиль разрешений Codex
(`default_permissions` + `[permissions.<имя>]`), а не `sandbox_mode`. Первая
версия этой задачи пыталась выразить их через `sandbox_mode`/
`approval_policy` и не смогла — `sandbox_mode` управляет только правом
ЗАПИСИ shell-команд, чтение им не ограничено вовсе, а `approval_policy`
держит `cat`/`sed`/`ls` доверенными независимо от пути. Найдено (и
подтверждено официальным синтаксисом от координатора, перепроверено живым
запуском заново): у Codex есть именованные профили `[permissions.<имя>]` с
правилами `filesystem` (`read`/`write`/`deny` по пути), и `deny` там
блокирует именно чтение — но **Codex не даёт сочетать `sandbox_mode` с
именованным профилем: если в конфиге есть оба, профиль молча не
применяется**. Это и объясняет, почему прошлая версия конфига (с
`sandbox_mode`, без профиля) пропускала `cat .env` при любых настройках —
профиля там не было вовсе, а `sandbox_mode` чтение не ограничивает.

`.codex/config.toml` теперь задаёт `default_permissions = "uzum"` +
`[permissions.uzum]` (`extends = ":read-only"`, три правила filesystem —
deny на `~/.config/uzum-ai/**` и на `**/*.env` внутри репозитория, read на
весь репозиторий, write только на `work/**`) и НЕ задаёт `sandbox_mode`/
`[sandbox_workspace_write]` вовсе. Живым запуском (изолированный
`CODEX_HOME`, реальный `.codex/config.toml` этого репозитория)
проверено: `cat .env` и `cat ~/.config/uzum-ai/secrets.env` (с тестовым
значением) отказывают ДО исполнения команды (файл ни разу не создаётся/не
читается — проверено по факту на диске, не по тексту ответа модели);
`echo x > outside.txt` (запись вне work/) не создаёт файл ни из корня
репозитория, ни после `-C work`; `echo y > work/inside.txt` и чтение
вложенных файлов репозитория при этом проходят нормально (exit 0,
реальный вывод); MCP-инструменты (readOnlyHint) по-прежнему не сломаны.

**Находка про `extends`, не запланированная, найдена самопроверкой.**
Первая версия использовала `extends = ":workspace"` (как в примере
координатора) — прошла все проверки в изолированной НЕ-git песочнице, но
повторный прогон в НАСТОЯЩЕМ git-репозитории (эта же копия
`uzum-data-agent`) показал, что запись вне `work/` всё равно проходит:
`:workspace` даёт файлам ВНУТРИ git-репозитория неявное разрешение на
запись, и явные правила `read`/`write` профиля его не отменяют — отменяет
только `deny`, а `deny` на весь репозиторий убил бы и требуемое правилом 2
чтение. Фикс — `extends = ":read-only"` (не даёт НИКАКОЙ записи по
умолчанию), поверх которого `work/**` = write — единственный источник
права записи. Живым запуском подтверждено в настоящем git-репозитории (не
только в изолированной песочнице) — см. отчёт задачи Codex-5.

**Важная находка про способ записи путей.** Первая попытка профиля
использовала специальный ключ `:workspace_roots` (`{"work/**": "write"}`)
вместо абсолютных путей — все прямые проверки прошли, но `-C work` (флаг
"использовать эту папку как рабочий корень агента") пересчитывает
`:workspace_roots` от НОВОГО корня, и запись за пределы `work/` (в старый
корень репозитория) снова становится разрешённой: живой запуск, `echo z1 >
../outside.txt` после `-C work` выполнился (`exit 0`, файл реально
создан). С абсолютными путями (как сейчас в `tools/render_configs.py`) та
же попытка `-C work` ничего не создаёт — `operation not permitted`.
Абсолютный путь — цена этой устойчивости: он вычисляется в момент
генерации из расположения `tools/render_configs.py` на диске, то есть
`.codex/config.toml` перестаёт быть машинно-независимым для этого одного
поля (остальное — команды коннекторов — по-прежнему относительные пути) и
должен перегенерироваться на каждой новой машине (`setup.sh`), это
задокументировано в `tools/render_configs.py`.
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


def test_no_legacy_sandbox_mode_alongside_the_named_profile():
    """Находка ревью координатора, подтверждённая живым запуском: Codex не
    даёт сочетать `sandbox_mode`/`[sandbox_workspace_write]` с именованным
    профилем разрешений — если заданы оба, профиль МОЛЧА не применяется.
    Это и было причиной, почему первая версия задачи не могла выразить
    правила 2/3: в конфиге стоял sandbox_mode, а он чтение не ограничивает
    вовсе. Профиль обязан быть единственным источником политики
    файловой системы."""
    config = _codex_config()
    assert "sandbox_mode" not in config, config.get("sandbox_mode")
    assert "sandbox_workspace_write" not in config
    assert config.get("default_permissions") == render_configs.CODEX_PERMISSION_PROFILE_NAME


def _profile_filesystem_rules(repo_root):
    """Сгенерировать конфиг для ПРОИЗВОЛЬНОГО repo_root (временная папка
    теста, не обязательно текущая машина) и вернуть таблицу
    [permissions.<профиль>.filesystem] — так тест проверяет саму функцию
    генератора (её ПОВЕДЕНИЕ), а не единственное застывшее значение,
    привязанное к диску этой конкретной машины."""
    config = tomllib.loads(render_configs.render_codex_toml(CONNECTORS, repo_root=repo_root))
    profile_name = config["default_permissions"]
    return config["permissions"][profile_name]["filesystem"]


def test_secrets_home_and_dotenv_are_denied_in_the_permission_profile(tmp_path):
    """Правило 2. Возможность, не имя: ищем правило по ПАТТЕРНУ пути (папка
    секретов домашней директории / любой *.env внутри репозитория), а не по
    буквальному значению строки — так тест не пропустит, если кто-то поменяет
    формат пути, но продолжит ловить, если кто-то ослабит access с "deny" на
    "read"/"write" для того же самого пути."""
    rules = _profile_filesystem_rules(repo_root=tmp_path)

    home_secret_rules = [access for pattern, access in rules.items() if "uzum-ai" in pattern]
    assert home_secret_rules, "нет ни одного правила про ~/.config/uzum-ai — детектор сломан"
    assert all(access == "deny" for access in home_secret_rules), home_secret_rules

    env_rules = [access for pattern, access in rules.items() if pattern.endswith("*.env")]
    assert env_rules, "нет ни одного правила про *.env — детектор сломан"
    assert all(access == "deny" for access in env_rules), env_rules


def test_write_is_scoped_to_work_only(tmp_path):
    """Правило 3. Ищем ВСЕ правила с access="write" (возможность, не имя
    конкретного пути) и проверяем, что каждое из них ограничено `work/` —
    если кто-то добавит второе, более широкое правило write (например, на
    весь репозиторий), тест упадёт, даже если правило про work/ останется
    нетронутым."""
    rules = _profile_filesystem_rules(repo_root=tmp_path)

    write_patterns = [pattern for pattern, access in rules.items() if access == "write"]
    assert write_patterns, "нет ни одного правила write — детектор сломан"
    for pattern in write_patterns:
        assert pattern.rstrip("/*").endswith("work"), (
            f"правило write не ограничено work/: {pattern!r}"
        )

    repo_root_read_pattern = "%s/**" % str(tmp_path.resolve())
    assert rules.get(repo_root_read_pattern) == "read", rules.get(repo_root_read_pattern)


def test_permission_profile_does_not_extend_workspace():
    """Регрессия на самую опасную находку доработки: `extends = ":workspace"`
    даёт файлам ВНУТРИ git-репозитория неявное разрешение на запись, которое
    явные правила `read`/`write` в filesystem НЕ отменяют (отменяет только
    `deny`, а `deny` на весь репозиторий убил бы требуемое правилом 2
    чтение). Профиль, который проходил все проверки в изолированной
    не-git песочнице, молча пропускал запись вне work/ именно в НАСТОЯЩЕМ
    git-репозитории — там, где он и должен работать. Явных write-правил в
    filesystem это не касается (см. test_write_is_scoped_to_work_only) —
    поэтому нужна отдельная проверка на сам `extends`, а не только на
    список правил: без неё регрессия на `:workspace` осталась бы
    незамеченной юнит-тестом (её поймал только живой запуск в реальном
    репозитории, не изолированная песочница — см. отчёт задачи Codex-5)."""
    config = _codex_config()
    profile_name = config["default_permissions"]
    extends = config["permissions"][profile_name]["extends"]
    assert extends != ":workspace", extends
    assert extends == ":read-only", extends


def test_permission_profile_does_not_depend_on_workspace_roots_key():
    """Регрессия на конкретную находку: первая версия профиля использовала
    специальный ключ `:workspace_roots` для правила write на work/ — все
    прямые проверки проходили, но `-C work` (смена рабочего корня агента)
    пересчитывает `:workspace_roots` от НОВОГО корня, и запись за пределы
    work/ становится разрешена снова (живая проверка в отчёте: `-C work` +
    `echo z1 > ../outside.txt` создал файл при :workspace_roots и НЕ создал
    при абсолютных путях). Абсолютные пути этой уязвимости не имеют, поэтому
    :workspace_roots в правилах файловой системы использоваться не должен."""
    raw = render_configs.render_codex_toml(CONNECTORS)
    assert ":workspace_roots" not in raw


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
