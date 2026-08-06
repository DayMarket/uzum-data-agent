import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import build_context


def test_renders_markdown_table():
    out = build_context.render_marts([
        {"name": "golden.efficiency_mart", "domain": "OPH", "owner": "Ops",
         "trust": "доверяем", "note": ""},
    ])
    assert "| golden.efficiency_mart | OPH | Ops | доверяем |  |" in out
    assert out.startswith("# Реестр витрин")


def test_preserves_trust_from_existing_file(tmp_path):
    existing = tmp_path / "marts.md"
    existing.write_text(
        "# Реестр витрин\n\n| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n| a.b | X | Y | с оговоркой | дубли |\n",
        encoding="utf-8",
    )
    trust = build_context.read_trust(str(existing))
    assert trust["a.b"] == ("с оговоркой", "дубли")


def test_empty_rows_still_produce_header():
    assert "| Витрина |" in build_context.render_marts([])
