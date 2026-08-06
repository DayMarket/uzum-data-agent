from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

REQUIRED = {"adhoc-export", "data-check", "dashboard-fix", "data-sanity",
            "fix-access", "clickhouse-sql", "trino-iceberg", "superset",
            "superset-fixer", "superset-certifier", "task", "sheets"}


def test_all_required_skills_exist():
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    assert REQUIRED.issubset(present), REQUIRED - present


def test_every_skill_has_description_frontmatter():
    for path in SKILLS_DIR.glob("*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        assert text.startswith("---"), path
        head = text.split("---")[1]
        assert "description:" in head, path


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
