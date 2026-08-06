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


# --- находки 8 и 9 финального ревью ----------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTEXT = REPO_ROOT / "context"


def _data_rows(path):
    rows, after_separator = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("|") and set(line) <= set("|-: "):
            after_separator = True
            continue
        if after_separator and line.startswith("|"):
            rows.append(build_context.split_table_row(line)[1:-1])
    return rows


def test_registries_are_not_empty():
    """Находка 8: CLAUDE.md объявляет реестр витрин обязательным чтением,
    adhoc-export требует выбрать витрину оттуда, а data-sanity на каждой
    выгрузке писал бы «витрины нет в реестре» — потому что реестры были пустые.
    """
    assert len(_data_rows(CONTEXT / "marts.md")) > 100
    assert len(_data_rows(CONTEXT / "dashboards.md")) > 20


def test_trust_column_is_left_for_humans():
    """Колонка «доверие» — человеческое суждение: генератор её не выдумывает.
    Заполнены только те строки, что вели руками до этого."""
    rows = _data_rows(CONTEXT / "marts.md")
    filled = [r for r in rows if len(r) > 3 and r[3]]
    assert len(filled) <= 5, [r[0] for r in filled]
    assert all(not r[3] for r in _data_rows(CONTEXT / "dashboards.md") if len(r) > 3)


def test_manually_curated_rows_survived_the_rebuild():
    """Пересборка реестра не должна терять ручные колонки предыдущей версии."""
    trust = build_context.read_trust(str(CONTEXT / "marts.md"))
    assert trust["golden.efficiency_mart"][0] == "доверяем"
    assert trust["golden.hrops_main_metrics"] == (
        "с оговоркой", "всегда дедуплицируй по worker_id")


def test_generator_has_no_main_that_can_wipe_a_registry():
    """Находка 9: у генератора был __main__, который рендерил из пустого
    списка. Первый, кто выполнил бы обещание из шапки буквально, с
    перенаправлением в файл, обнулил бы реестр."""
    source = (REPO_ROOT / "tools" / "build_context.py").read_text(encoding="utf-8")
    assert 'if __name__' not in source


def test_registry_headers_do_not_promise_a_weekly_rebuild():
    """Шапки обещали еженедельную пересборку генератором, которого нет."""
    for name in ("marts.md", "dashboards.md", "metrics.md"):
        text = (CONTEXT / name).read_text(encoding="utf-8")
        assert "раз в неделю" not in text, name
