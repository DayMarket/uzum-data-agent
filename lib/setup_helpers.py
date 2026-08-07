"""Помощники установщика: запись секретов, список включённых серверов и
чтение файла доступов `.env` (задача 14 — заполнение мастера файлом).

Только стандартная библиотека — этот модуль подключается из setup.sh (мастер
установки), который должен работать до того, как в системе появится хоть один
внешний пакет.
"""
import json
import os
import stat

import envfile


def write_env(path, values):
    """Дописать переменные в env-файл, заменяя существующие. Права 600.

    Значения пишутся в одинарных кавычках (envfile.quote): файл `source`-ит
    bin/uzum перед запуском Claude Code, и без кавычек пароль с пробелом,
    долларом или бэктиком либо не доезжал до переменной вообще, либо
    подставлял в себя чужое значение, либо выполнял команду.

    Каталог, в котором лежит файл (обычно ~/.config/uzum-ai), тоже переводится
    на 700: секреты внутри и так закрыты правами файла, но сам каталог до
    этой правки создавался с правами по умолчанию (обычно 755) — то есть
    любой процесс того же пользователя мог хотя бы увидеть список файлов
    внутри (имена, размеры, mtime), даже не имея доступа к их содержимому.
    """
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    os.chmod(dirpath, 0o700)
    existing = envfile.read(path)  # порядок ключей сохраняется
    existing.update(values)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(envfile.format_line(key, value))
    os.chmod(path, 0o600)


def read_dotenv(path):
    """Прочитать файл доступов `.env`, заполненный человеком вручную.

    Тот же разборщик, что и для secrets.env (envfile.read) — второго парсера
    нарочно нет, чтобы значение с пробелом/`$`/бэктиком/кавычкой не сломалось
    здесь так же, как это уже однажды случилось с secrets.env (см.
    tests/test_envfile.py). `.env` лежит внутри рабочей папки, а не в
    ~/.config/uzum-ai — тот же класс данных (пароли в открытом виде), поэтому
    права проверяются и ужимаются до 600 точно так же, как каталог секретов
    ужимается до 700 в write_env.

    Возвращает (значения, было_ли_право_сужено). Отсутствие файла — не
    ошибка, как и для envfile.read.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return {}, False
    tightened = False
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        os.chmod(path, 0o600)
        tightened = True
    return envfile.read(path), tightened


def missing_keys(values, required):
    """Обязательные ключи, которых нет в `values` или которые пусты.

    Порядок из `required` сохраняется. Пустая строка считается "не
    заполнено" — ровно та же граница, на которой мастер в интерактивном
    режиме переспрашивает значение, а не молча принимает пустой ввод.
    """
    return [key for key in required if not values.get(key)]


def write_enabled_servers(path, servers):
    """Записать список одобренных MCP-серверов, не трогая прочие настройки."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            data = {}
    data["enabledMcpjsonServers"] = sorted(set(servers))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
