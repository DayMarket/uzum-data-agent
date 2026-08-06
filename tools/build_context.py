#!/usr/bin/env python3
"""Пересборка реестров из каталога данных.

Колонки «доверие» и «комментарий» — ручное суждение: генератор их сохраняет,
а не перетирает.
"""
import re
import sys

HEADER = ("# Реестр витрин\n\n"
          "> Пересобирается `tools/build_context.py`. Колонки «доверие» и\n"
          "> «комментарий» заполняются руками и при пересборке сохраняются.\n\n"
          "| Витрина | Домен | Владелец | Доверие | Комментарий |\n"
          "|---|---|---|---|---|\n")

ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*[^|]*\|\s*[^|]*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|$")


def read_trust(path):
    """Вытащить из существующего файла ручные колонки: {витрина: (доверие, коммент)}."""
    trust = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return trust
    for line in lines:
        match = ROW_RE.match(line.strip())
        if match and not match.group(1).startswith("-") and match.group(1) != "Витрина":
            trust[match.group(1)] = (match.group(2), match.group(3))
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
