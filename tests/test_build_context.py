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


def test_preserves_pipe_in_comment(tmp_path):
    """Вертикальная черта внутри комментария не должна быть разделителем."""
    existing = tmp_path / "marts.md"
    existing.write_text(
        "# Реестр витрин\n\n| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n"
        "| a.b | X | Y | доверяем | есть дубли \\| дубль-2 |\n",
        encoding="utf-8",
    )
    trust = build_context.read_trust(str(existing))
    # Kommentarii должен содержать весь текст с экранированной вертикальной чертой
    # (парсер сохраняет экранированный символ как |)
    assert trust["a.b"] == ("доверяем", "есть дубли | дубль-2")


def test_handles_extra_columns_in_comment(tmp_path, capsys):
    """Больше 5 колонок — лишние склеиваются в комментарий."""
    existing = tmp_path / "marts.md"
    existing.write_text(
        "# Реестр витрин\n\n| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n"
        "| a.b | X | Y | доверяем | часть1 | часть2 | часть3 |\n",
        encoding="utf-8",
    )
    trust = build_context.read_trust(str(existing))
    # Лишние части склеиваются через " | "
    assert trust["a.b"] == ("доверяем", "часть1 | часть2 | часть3")


def test_warns_about_malformed_rows(tmp_path, capsys):
    """Строка с недостаточным числом колонок должна вызвать предупреждение."""
    existing = tmp_path / "marts.md"
    existing.write_text(
        "# Реестр витрин\n\n| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n"
        "| a.b | X | Y |\n",
        encoding="utf-8",
    )
    trust = build_context.read_trust(str(existing))
    captured = capsys.readouterr()
    # Должно быть предупреждение в stderr
    assert "Warning" in captured.err
    assert "line 5" in captured.err
    # Строка не должна попасть в trust
    assert "a.b" not in trust


def test_skips_headers_of_other_registries(tmp_path):
    """Заголовки других реестров (Метрика, Дашборд) не попадают в данные."""
    existing = tmp_path / "marts.md"
    existing.write_text(
        "# Реестр витрин\n\n| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n"
        "| a.b | X | Y | доверяем | ok |\n"
        "\n# Реестр метрик\n\n| Метрика | Домен | Владелец | Доверие | Комментарий |\n"
        "|---|---|---|---|---|\n"
        "| Метрика | X | Y | поверим | это не витрина |\n",
        encoding="utf-8",
    )
    trust = build_context.read_trust(str(existing))
    # a.b должна быть
    assert "a.b" in trust
    # Метрика (как имя) не должна быть
    assert "Метрика" not in trust
