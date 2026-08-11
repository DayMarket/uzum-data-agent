#!/usr/bin/env python3
"""Мостик source→target: кладёт переменные под теми именами, которых ждут
процессы коннекторов, и запускает Codex.

ЗАЧЕМ. `connectors/registry.py` знает про каждую переменную две вещи:
`source` — имя в `.env`/окружении аналитика, и `target` — имя, под которым
её ждёт сам процесс коннектора (контракт стороннего пакета, мы его не
выбираем), плюс `default` для структурных. Рендерер `.mcp.json` применяет
это прямо в конфиге: `"JIRA_PERSONAL_TOKEN": "${JIRA_TOKEN}"` — подстановку
делает Claude Code. Рендерер `.codex/config.toml` так не может: у Codex нет
`${VAR}`-подстановки (docs/codex-facts.md, раздел 4), единственный рабочий
механизм — `env_vars = ["ИМЯ", ...]`, плоский список имён, который Codex
пробрасывает из СВОЕГО окружения в дочерний процесс ПОД ТЕМ ЖЕ ИМЕНЕМ.

То есть Codex умеет только «переслать», но не «переименовать» и не
«подставить дефолт». Значит и то, и другое обязано случиться раньше — в
окружении процесса, который запускает `codex`. Пока этого не делал никто,
шесть коннекторов из девяти под Codex стартовали без адреса и без токена и
не отдавали ни одного инструмента (найдено на живой приёмке: `bin/uzum`
подгружает `~/.config/uzum-ai/secrets.env` как есть, а там лежит
`JIRA_TOKEN`, тогда как `uvx mcp-atlassian` ждёт `JIRA_PERSONAL_TOKEN`).

ПОЧЕМУ ЗДЕСЬ, А НЕ В ГЕНЕРАТОРЕ КОНФИГА. Записать значения прямо в
`config.toml` (`[mcp_servers.<id>.env]`) технически можно — но это положило
бы токены открытым текстом в файл, чего вся схема секретов этого
репозитория избегает: в конфиг попадают только ИМЕНА, значения живут в
`~/.config/uzum-ai/secrets.env` (права 600) и в окружении процессов. Модель
«секрет в окружении дочернего процесса» уже принята и не пересматривается —
`.mcp.json` делает ровно это.

ПОЧЕМУ ОТДЕЛЬНЫЙ ПРОЦЕСС, А НЕ ПАРА СТРОК В `bin/uzum`. Мостик должен
пережить любое значение: пароль с пробелом, `$`, апострофом, обратной
кавычкой, переводом строки. Всё, что проходит через шелл (подстановка
команды, `eval`, `export "$line"`), — это ещё один слой кавычек и ещё один
способ тихо испортить значение; плюс `$(...)` в bash молча съедает нулевые
байты и хвостовые переводы строк. Здесь значения вообще не пересекают
границу шелла: они читаются из `os.environ` и уходят в `os.execvpe`
третьим аргументом (envp) — не через argv, поэтому в `ps` их не видно, как
и раньше. После execvpe процесс замещается процессом `codex` в том же PID —
тот же приём и та же причина, что у `connectors/clickhouse_proxy.py`.

ГРАНИЦЫ. Claude Code этот мостик не касается вообще: `bin/uzum` зовёт его
только в ветке Codex, у Claude Code подстановка своя и работает.
`clickhouse_proxy.py` свою часть переименования (CH_WMS_*/CH_DWH_* →
CLICKHOUSE_*) делает сам, внутри своего процесса; мостик его не дублирует и
не отменяет — для clickhouse-wms/clickhouse-dwh он читает Codex-ветку
реестра, где target == source, то есть просто передаёт CH_*_* как есть (и
подставляет дефолты порта/схемы). Никакого CLICKHOUSE_* в общем окружении
Codex не появляется — именно поэтому два кластера и не сталкиваются.

Использование:

    python3 connectors/codex_env_bridge.py -- codex -p uzum [аргументы]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    # Файл запускается напрямую (`python3 connectors/codex_env_bridge.py`):
    # Python кладёт в sys.path каталог скрипта (connectors/), а не корень
    # репозитория, и `import connectors.registry` иначе не находится.
    sys.path.insert(0, str(_REPO_ROOT))

from connectors.registry import CONNECTORS, EnvVar  # noqa: E402


class ConflictingTargets(Exception):
    """Два коннектора требуют одно имя target из разных источников.

    Не бывает в сегодняшнем реестре и не должно появиться: окружение у
    процесса `codex` одно на все MCP-серверы, поэтому две разные величины
    под одним именем — это молча проигравший один из коннекторов (ровно тот
    класс поломки, из-за которого clickhouse-wms/clickhouse-dwh получили
    отдельную Codex-ветку). Лучше громкая ошибка при запуске, чем сессия,
    которая выглядит рабочей.
    """


def codex_env_overlay(source_env, connectors=None) -> dict:
    """Что дописать в окружение перед запуском Codex: {target: значение}.

    Значения берутся ТОЛЬКО из `source_env` и никогда из уже собранного
    результата — иначе переменная, чей target совпадает с чужим source,
    получила бы значение по цепочке, а порядок коннекторов в реестре стал
    бы частью контракта. Функция чистая: ничего не пишет в os.environ,
    возвращает новый словарь — применяет его вызывающий код.

    Пусто (нет в source_env или пустая строка) — берётся `default`. Нет и
    его — имя не выставляется вовсе: пустой токен в окружении означал бы
    «доступ есть, но не работает» вместо «доступа нет». У секретов
    `default` невозможен по построению (registry.EnvVar это проверяет), так
    что выдумать значение секрета тут неоткуда.

    `connectors=None`, а не `=CONNECTORS`: умолчание-выражение вычисляется
    один раз при импорте, и прибитый в сигнатуре список игнорировал бы
    подмену `codex_env_bridge.CONNECTORS`. Здесь это не прятало дефекта —
    реестр берётся из исходников репозитория, а не с машины, и тесты, у
    которых свой набор, передают его явным `connectors=` (так и надо), —
    но форма ровно та же, что стоила круга в log_session.clickhouse_users.
    Ловушку убираем, пока в неё не наступили.
    """
    connectors = CONNECTORS if connectors is None else connectors
    overlay = {}
    origin = {}  # target -> (id коннектора, source, default)
    for connector in connectors:
        _, _, env_items = connector.codex_spec()
        for item in env_items:
            if not isinstance(item, EnvVar):
                continue  # StaticEnv пишется прямо в config.toml, ему тут нечего делать
            seen = origin.get(item.target)
            if seen is not None and seen[1:] != (item.source, item.default):
                raise ConflictingTargets(
                    "%s: коннектор %s ждёт значение из %s, коннектор %s — из %s; "
                    "окружение у codex одно на всех, оба сразу выставить нельзя"
                    % (item.target, seen[0], seen[1], connector.id, item.source)
                )
            origin[item.target] = (connector.id, item.source, item.default)

            value = source_env.get(item.source)
            if not value:
                value = item.default
            if value is None:
                continue
            overlay[item.target] = value
    return overlay


def main(argv) -> int:
    if len(argv) < 3 or argv[1] != "--":
        sys.stderr.write(
            "Использование: %s -- <команда> [аргументы]\n" % os.path.basename(argv[0])
        )
        return 2
    command = argv[2:]

    env = dict(os.environ)
    env.update(codex_env_overlay(os.environ))
    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        # Сюда попадаем, только если запустить не удалось: при успехе
        # execvpe не возвращается вовсе.
        sys.stderr.write("Не удалось запустить %s: %s\n" % (command[0], exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
