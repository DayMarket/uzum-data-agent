#!/usr/bin/env python3
"""Пересборка реестров из каталога данных.

Колонки «доверие» и «комментарий» — ручное суждение: генератор их сохраняет,
а не перетирает.
"""
import sys

HEADER = ("# Реестр витрин\n\n"
          "> Пересобирается `tools/build_context.py`. Колонки «доверие» и\n"
          "> «комментарий» заполняются руками и при пересборке сохраняются.\n\n"
          "| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
          "|---|---|---|---|---|\n")


def split_table_row(line):
    """Разбить строку таблицы по вертикальным чертам, с учётом экранирования.

    Экранированный символ \\| считается частью текста, не разделителем.
    """
    cells = []
    current = []
    i = 0
    while i < len(line):
        if line[i] == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            # Экранированная чёрточка — добавляем саму чёрточку
            current.append("|")
            i += 2
        elif line[i] == "|":
            # Разделитель
            cells.append("".join(current).strip())
            current = []
            i += 1
        else:
            current.append(line[i])
            i += 1

    if current or len(cells) > 0:
        cells.append("".join(current).strip())

    return cells


def read_trust(path):
    """Вытащить из существующего файла ручные колонки: {витрина: (доверие, коммент)}.

    Разбирает таблицу корректно, не теряя данные молча. Если строка похожа на
    строку таблицы, но не разобралась, выводит предупреждение.
    """
    trust = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return trust

    saw_separator = False
    for line_num, line in enumerate(lines, 1):
        line_stripped = line.strip()

        # Проверяем, это ли строка-разделитель
        if line_stripped.startswith("|") and "-" in line_stripped and all(c in "| -" for c in line_stripped):
            saw_separator = True
            continue

        # Только строки после разделителя — это данные
        if not saw_separator or not line_stripped.startswith("|") or not line_stripped.endswith("|"):
            continue

        try:
            # Разбираем таблицу
            cells = split_table_row(line_stripped)

            # Убираем первый и последний элементы (пусто вне таблицы)
            if len(cells) > 0 and cells[0] == "":
                cells = cells[1:]
            if len(cells) > 0 and cells[-1] == "":
                cells = cells[:-1]

            if len(cells) < 4:
                print(f"Warning: line {line_num} has {len(cells)} columns, expected at least 4: {line_stripped}", file=sys.stderr)
                continue

            name = cells[0]

            # Пропускаем заголовки других реестров (по имени, не по жёсткому слову)
            if name.startswith("-") or name in ("Витрина", "Метрика", "Дашборд"):
                continue

            trust_val = cells[3].strip()
            # Остальные колонки склеиваем в комментарий (может быть несколько полей с |)
            comment = " | ".join(cells[4:]).strip() if len(cells) > 4 else ""

            trust[name] = (trust_val, comment)
        except Exception as e:
            print(f"Warning: couldn't parse line {line_num}: {line_stripped}. Error: {e}", file=sys.stderr)
            continue

    return trust


def render_marts(rows, trust=None):
    trust = trust or {}
    out = [HEADER]
    for row in rows:
        saved_trust, saved_note = trust.get(row["name"], (row.get("trust", ""),
                                                          row.get("note", "")))
        out.append("| %s | %s | %s | %s | %s |\n" % (
            row["name"], row.get("domain", ""), row.get("owner", ""),
            saved_trust, saved_note))
    return "".join(out)


if __name__ == "__main__":
    # Источник строк — выгрузка из OpenMetadata; здесь только рендер.
    print(render_marts([], read_trust(sys.argv[1] if len(sys.argv) > 1 else "")))
