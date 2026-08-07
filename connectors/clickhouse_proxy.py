#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "mcp-clickhouse",
#     "pyarrow",
# ]
# ///
"""Обёртка вокруг стороннего пакета mcp-clickhouse — только для Codex.

Зачем нужна. У Codex единственный проверенный способ передать значение из
окружения аналитика дочернему MCP-процессу — `env_vars` в config.toml:
плоский список ИМЁН, без переименования (проверено запуском, docs/
codex-facts.md, раздел 4, и живой проверкой при ревью задачи Codex-3: TOML с
`env_vars` в виде таблицы {ЦЕЛЬ = "ИСТОЧНИК"} не парсится, схема требует
"sequence"). Пакет mcp-clickhouse при этом ждёт фиксированные имена —
CLICKHOUSE_HOST/PORT/USER/PASSWORD/SECURE — одинаковые что для складского
кластера (WMS), что для общего DWH: у нас два коннектора (clickhouse-wms,
clickhouse-dwh), которым нужны РАЗНЫЕ значения под ОДНИМ и тем же именем.
Без переименования где-то между источником (.env: CH_WMS_HOST/CH_DWH_HOST)
и потребителем (CLICKHOUSE_HOST) второй кластер в Codex недоступен молча —
132 витрины из реестра (см. context/marts.md), которые живут только на DWH,
были бы не видны.

Claude Code эта обёртка не касается — его .mcp.json остаётся как был
(command uvx, args mcp-clickhouse напрямую). Там переименование делает сам
файл: `"CLICKHOUSE_HOST": "${CH_WMS_HOST}"` — это подстановка ВНУТРИ
env-словаря ОДНОГО процесса, коннекторы clickhouse-wms и clickhouse-dwh —
два независимых процесса с двумя независимыми env-словарями, делить им
нечего, коллизии никогда не было. Вводить лишний процесс там, где и так
работает — не тот случай, когда "то же самое для обоих движков" помогает
(см. отчёт задачи Codex-3, раздел про выбор).

Приём — тот же, что уже применяют connectors/trino_proxy.py, superset_mcp.py
и sheets_mcp.py: свой процесс между движком и внешним миром. Отличие здесь в
том, что сам MCP-сервер — чужой пакет (mcp-clickhouse, PyPI), а не наша
реализация протокола, поэтому обёртка не говорит по MCP сама: она только
раскладывает переменные под именами, которых ждёт пакет, и передаёт
управление через os.execvpe. После execvpe процесс обёртки замещается
процессом mcp-clickhouse в том же PID — stdio (то, через что говорит
MCP-протокол) остаётся тем же файловым дескриптором, Codex подмены не
замечает.

Использование: `clickhouse_proxy.py wms` или `clickhouse_proxy.py dwh` —
единственный позиционный аргумент выбирает префикс источника (CH_WMS_* или
CH_DWH_*). Codex передаёт его третьим элементом args, после "run" и пути к
этому файлу — см. connectors/registry.py, ветка codex_override у
clickhouse-wms/clickhouse-dwh.
"""
from __future__ import annotations

import os
import sys

REQUIRED = ("HOST", "USER", "PASSWORD")  # без дефолта — как и в .mcp.json
OPTIONAL_DEFAULTS = {"PORT": "8123", "SECURE": "false"}


def _prefix_for(cluster: str) -> str:
    cluster = cluster.strip().lower()
    if cluster not in ("wms", "dwh"):
        raise SystemExit(
            "clickhouse_proxy.py: неизвестный кластер %r, ожидался 'wms' или 'dwh'" % cluster
        )
    return "CH_%s_" % cluster.upper()


def build_env(cluster: str, source_env: dict) -> dict:
    """Собрать CLICKHOUSE_*-переменные из CH_<CLUSTER>_*-источника.

    Чистая функция, не трогает os.environ и не запускает mcp-clickhouse —
    это позволяет проверить раскладку юнит-тестом (в т.ч. что WMS и DWH из
    ОДНОГО общего source_env дают РАЗНЫЕ CLICKHOUSE_*, см.
    tests/test_clickhouse_proxy.py) без сети и без реального пакета.
    """
    prefix = _prefix_for(cluster)
    missing = [prefix + name for name in REQUIRED if not source_env.get(prefix + name)]
    if missing:
        raise SystemExit(
            "clickhouse_proxy.py: не заданы переменные: %s (кластер %s)"
            % (", ".join(missing), cluster)
        )

    env = dict(source_env)
    env["CLICKHOUSE_HOST"] = source_env[prefix + "HOST"]
    env["CLICKHOUSE_USER"] = source_env[prefix + "USER"]
    env["CLICKHOUSE_PASSWORD"] = source_env[prefix + "PASSWORD"]
    for name, default in OPTIONAL_DEFAULTS.items():
        env["CLICKHOUSE_" + name] = source_env.get(prefix + name, default)
    env.setdefault("MCP_TRANSPORT", "stdio")
    env.setdefault("CLICKHOUSE_VERIFY", "false")
    env.setdefault("CHDB_ENABLED", "false")
    return env


def main(argv):
    if len(argv) != 2:
        raise SystemExit("Использование: clickhouse_proxy.py <wms|dwh>")
    env = build_env(argv[1], os.environ)
    os.execvpe("mcp-clickhouse", ["mcp-clickhouse"], env)


if __name__ == "__main__":
    main(sys.argv)
