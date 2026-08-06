"""Единый формат файла секретов ~/.config/uzum-ai/secrets.env.

Файл читают четыре разных места (мастер установки, хуки телеметрии,
connectors/trino_proxy.py, connectors/superset_mcp.py) и — главное — его
`source`-ит bin/uzum перед запуском Claude Code. Поэтому значение обязано
быть корректным словом шелла, а не просто текстом после знака равенства.

Как ломалось до этого модуля (воспроизведено):

    CH_PASSWORD=p@ss w0rd   →  "w0rd: command not found", переменной нет вовсе
    A=abc$HOME def          →  вместо токена подставится домашний каталог
    B=a`id`b                →  выполнится команда id

А поскольку в .mcp.json секреты подставляются голым ${VAR} без дефолта,
отсутствие переменной валит запись MCP-сервера целиком — коннектор не
поднимается, и причина никак не связана с тем, что видит аналитик.

Формат: значение всегда в одинарных кавычках, внутренняя одинарная кавычка
экранируется по-шелловски — '\\'' (закрыли строку, экранированная кавычка,
открыли снова). Внутри одинарных кавычек шелл не интерпретирует ничего,
включая перевод строки.

Читатели обязаны снимать кавычки через parse() / unquote() из этого модуля,
а не наивным .strip('"').strip("'"): тот съедает кавычку у значения, которое
на неё заканчивается, и не понимает экранирования.
"""

__all__ = ["quote", "unquote", "parse", "format_line"]


def quote(value):
    """Значение → безопасное для `source` слово шелла в одинарных кавычках."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def format_line(key, value):
    """Строка KEY='значение' для записи в файл."""
    return "%s=%s\n" % (key, quote(value))


def _unquote_from(text, start):
    """Разобрать значение с позиции start. Вернуть (значение, позиция_конца).

    Правила ровно шелловские, чтобы прочитанное совпадало с тем, что получит
    `source`:
      * вне кавычек: \\X — литерал X, кавычка открывает соответствующий режим;
      * в одинарных кавычках — литерал всё до следующей одинарной кавычки;
      * в двойных — \\ экранирует ", \\, $, ` и перевод строки (такие файлы
        могли остаться от ручной правки или от старых версий мастера).
    Значение заканчивается на переводе строки вне кавычек или на конце текста.
    """
    out = []
    # Длина части значения, которая пришла из кавычек или экранирования: её
    # хвостовые пробелы поставлены намеренно и обрезке не подлежат.
    protected = 0
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\n":
            break
        if ch == "'":
            i += 1
            while i < n and text[i] != "'":
                out.append(text[i])
                i += 1
            i += 1  # закрывающая кавычка (или конец текста — тоже конец значения)
            protected = len(out)
            continue
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '"\\$`\n':
                    if text[i + 1] != "\n":  # \<перевод строки> — склейка строк
                        out.append(text[i + 1])
                    i += 2
                    continue
                out.append(text[i])
                i += 1
            i += 1
            protected = len(out)
            continue
        if ch == "\\" and i + 1 < n:
            if text[i + 1] != "\n":
                out.append(text[i + 1])
            i += 2
            protected = len(out)
            continue
        out.append(ch)
        i += 1
    value = "".join(out)
    # Хвостовые пробелы вне кавычек — форматирование строки (старый формат
    # файла, ручная правка), а не часть секрета: шелл их тоже не взял бы.
    value = value[:protected] + value[protected:].rstrip()
    return value, i


def unquote(raw):
    """Снять кавычки/экранирование с одного значения."""
    value, _ = _unquote_from(raw, 0)
    return value


def parse(text):
    """Разобрать содержимое env-файла в словарь с сохранением порядка ключей.

    Строки-комментарии и строки без '=' пропускаются. Значение может занимать
    несколько строк, если перевод строки попал внутрь кавычек.
    """
    values = {}
    i = 0
    n = len(text)
    while i < n:
        end = text.find("\n", i)
        line_end = n if end == -1 else end
        line = text[i:line_end]
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            i = line_end + 1
            continue
        key = line.partition("=")[0].strip()
        value, pos = _unquote_from(text, i + line.index("=") + 1)
        if key:
            values[key] = value
        # значение могло занять несколько строк — продолжаем после его конца
        nl = text.find("\n", pos)
        i = n if nl == -1 else nl + 1
    return values


def read(path):
    """Прочитать файл секретов. Отсутствие файла — не ошибка, вернём пустое."""
    try:
        with open(path, encoding="utf-8") as f:
            return parse(f.read())
    except OSError:
        return {}
