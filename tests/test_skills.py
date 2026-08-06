from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

REQUIRED = {"adhoc-export", "data-check", "dashboard-fix", "data-sanity",
            "fix-access", "clickhouse-sql", "trino-iceberg", "superset",
            "superset-fixer", "superset-certifier", "task"}


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
