import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".agents" / "skills"
CLAUDE_SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

REQUIRED = {"adhoc-export", "data-check", "dashboard-fix", "data-sanity",
            "fix-access", "clickhouse-sql", "trino-iceberg", "superset",
            "superset-fixer", "superset-certifier", "task", "sheets"}

NAME_RE = re.compile(r"^name:\s*(\S+)\s*$", re.MULTILINE)


def test_all_required_skills_exist():
    """Единственный источник скиллов — .agents/skills: Codex ищет их только
    там, Claude Code — через симлинк из .claude/skills."""
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    assert REQUIRED.issubset(present), REQUIRED - present


def test_every_skill_has_name_and_description_frontmatter():
    """Codex требует name явно во frontmatter — Claude Code его не требует,
    но лишнее поле ему не мешает."""
    for path in SKILLS_DIR.glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---"), path
        head = text.split("---")[1]
        assert "description:" in head, path
        match = NAME_RE.search(head)
        assert match, path
        assert match.group(1) == path.parent.name, (
            path, "name во frontmatter должен совпадать с именем каталога")


def test_every_claude_skills_entry_is_a_symlink_not_a_copy():
    """Копия вместо симлинка не сломает работу сразу, но разъедётся через
    месяц с оригиналом в .agents/skills, и заметить это будет некому."""
    entries = [p for p in CLAUDE_SKILLS_DIR.iterdir()
               if not p.name.startswith(".")]
    assert entries, "нечего проверять — .claude/skills пуст"
    for path in entries:
        assert path.is_symlink(), (path, "не симлинк")
        target = path.resolve()
        assert target.is_dir(), (path, "симлинк ведёт в никуда")
        assert target.parent == SKILLS_DIR.resolve(), (
            path, "симлинк ведёт не в .agents/skills")


def test_claude_skills_symlinks_are_relative():
    """Абсолютный путь сломается у всех, кроме того, кто его создал —
    репозиторий клонируют в разные места."""
    for path in CLAUDE_SKILLS_DIR.iterdir():
        if path.name.startswith("."):
            continue
        raw_target = path.readlink()
        assert not raw_target.is_absolute(), (path, raw_target)


def test_no_skill_exists_in_two_copies_on_disk():
    """Главная проверка: содержимое, доступное по обоим путям, — один и тот
    же файл на диске (совпадающий inode), а не два одинаковых файла, которые
    молча разъедутся после первой же правки через один из путей."""
    for path in CLAUDE_SKILLS_DIR.iterdir():
        if path.name.startswith("."):
            continue
        via_claude = path / "SKILL.md"
        via_agents = SKILLS_DIR / path.name / "SKILL.md"
        assert via_claude.exists(), via_claude
        assert via_agents.exists(), via_agents
        assert via_claude.samefile(via_agents), (
            path.name, "SKILL.md существует в двух экземплярах на диске")


def test_job_skills_require_data_sanity():
    for name in ("adhoc-export", "data-check", "dashboard-fix"):
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert "data-sanity" in text, name


def test_sheets_connector_is_reachable_from_a_skill():
    """Находка 15: коннектор Google Sheets настраивался мастером, но им ничего
    не пользовалось — скилла не было, adhoc-export про него не знал.
    Настроенный, но никому не известный коннектор — это вопрос в мастере, на
    который человек отвечает зря."""
    text = " ".join(p.read_text(encoding="utf-8") for p in SKILLS_DIR.glob("*/SKILL.md"))
    assert "mcp__sheets__create_sheet" in text
    assert "mcp__sheets__append_rows" in text


def test_adhoc_export_knows_where_to_put_the_result():
    text = (SKILLS_DIR / "adhoc-export" / "SKILL.md").read_text(encoding="utf-8")
    assert "sheets" in text


# Находка 4 финального ревью (дозакрытие): изначально сверку с реестром
# добавили только в adhoc-export/data-check/data-sanity — но data-sanity
# терминальна (блокирует показ результата человеку), а правило 1 запрещает
# зарплатные данные читать вообще, "ни выгрузок, ни агрегатов, ни просто
# посмотреть". Значит терминальной проверки недостаточно: dashboard-fix,
# superset-fixer, superset-certifier читали дашборд/чарт/скриншот раньше,
# чем звали data-sanity, а trino-iceberg/clickhouse-sql/superset вообще не
# упоминали реестр.
#
# ClickHouse разведён на два коннектора (clickhouse-wms, clickhouse-dwh) —
# детектор должен ловить оба новых имени, а не только старое "clickhouse":
# скилл, который упоминает любой из двух реальных коннекторов, обязан пройти
# ту же сверку с реестром, что и раньше.
DATA_TOUCHING_TOOL_RE = re.compile(
    r"mcp__(superset|trino|clickhouse-wms|clickhouse-dwh)__[a-zA-Z_]+"
    r"|`(superset|trino|clickhouse-wms|clickhouse-dwh)`"
)

# Скиллы, которые заведомо не выбирают данные сами — детектор ниже их не
# найдёт (и не должен): sheets только записывает то, что ему уже передали;
# task только маршрутизирует на другие скиллы; fix-access — диагностика
# доступов, а не данных.
NOT_DATA_TOUCHING = {"sheets", "task", "fix-access"}

# adhoc-export и data-check уже требуют сверку явным текстом на первом
# шаге, но говорят обобщённо «витрина», не называя коннектор — детектор по
# упоминанию коннектора их не поймает, поэтому держим отдельно, вместе с
# data-sanity (терминальная проверка, которая тоже должна знать о пометке).
ALWAYS_CHECK_REGISTRY = {"adhoc-export", "data-check", "data-sanity"}


def test_all_data_touching_skills_check_the_registry_before_reading():
    """Ищем в тексте каждого скилла упоминание mcp__superset__*/
    mcp__trino__*/mcp__clickhouse__* или ссылку на коннектор
    `superset`/`trino`/`clickhouse` — не хардкодим список руками: если
    новый скилл появится и упомянет любой из этих коннекторов, тест обязан
    заметить и потребовать ту же сверку с реестром, а не полагаться на то,
    что кто-то не забудет обновить список тут."""
    detected = set()
    for path in SKILLS_DIR.glob("*/SKILL.md"):
        name = path.parent.name
        if name in NOT_DATA_TOUCHING:
            continue
        text = path.read_text(encoding="utf-8")
        if DATA_TOUCHING_TOOL_RE.search(text):
            detected.add(name)

    checked = detected | ALWAYS_CHECK_REGISTRY

    # Бэкстоп: если детектор сломают (например, скилл вместо `superset`
    # начнёт писать "коннектор Superset" без бэктиков), тест всё равно
    # должен упасть, а не молча сократить список проверяемых скиллов.
    expected_minimum = {"adhoc-export", "data-check", "data-sanity",
                         "dashboard-fix", "superset-fixer", "superset-certifier",
                         "trino-iceberg", "clickhouse-sql", "superset"}
    assert expected_minimum <= checked, expected_minimum - checked

    for name in checked:
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert "запрещено" in text, name
        assert "правило 1" in text, name


# Первый инструмент/шаг, который реально читает дашборд/чарт/датасет —
# именно ОН должен идти после сверки, не просто "сверка упомянута где-то в
# файле". "запрещено" в самом конце шагов, после того как всё уже прочитано,
# формально прошёл бы предыдущий тест, но не защитил бы данные.
FIRST_DATA_READ_RE = re.compile(
    r"mcp__superset__(get_dashboard\b|get_dashboard_screenshot|get_chart_data|"
    r"get_chart_screenshot|get_dashboard_layout_summary|sql_query)"
    r"|Воспроизведи запрос|Открой дашборд"
)


def test_forbidden_check_comes_before_first_data_read_in_step_based_skills():
    """Находка 4 (дозакрытие): порядок имеет значение. data-sanity
    блокировала показ результата, а не чтение — к моменту её вызова
    дашборд/чарт уже прочитаны. Здесь проверяем позицию текста, а не только
    факт упоминания: сверка с реестром обязана стоять раньше первого чтения
    дашборда/чарта в файле скилла."""
    for name in ("dashboard-fix", "superset-fixer", "superset-certifier"):
        text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        forbidden_pos = text.index("запрещено")
        read_match = FIRST_DATA_READ_RE.search(text)
        assert read_match, name
        assert forbidden_pos < read_match.start(), (
            name, "сверка стоит после первого чтения данных")


def test_readme_documents_how_to_run_the_suite_without_warnings():
    """Набор гоняется через uv с современным интерпретатором: зависимости
    коннекторов объявлены для `uv run`, а не для системного python3. Раньше
    прогон давал три предупреждения из google-auth в системном Python 3.9 —
    причина была не в нашем коде, а в том, чем его запускали."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "uv run --python 3.12" in readme
    assert "--with pytest" in readme
    # предупреждения не глушим конфигом: это спрятало бы сигнал
    for name in ("pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini"):
        cfg = REPO_ROOT / name
        if cfg.exists():
            assert "filterwarnings" not in cfg.read_text(encoding="utf-8"), name
