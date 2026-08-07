#!/usr/bin/env python3
"""Генератор двух форматов конфига коннекторов из одного реестра.

Источник — connectors/registry.py (registry.CONNECTORS). Здесь только рендер:
никакой логики про то, какие коннекторы существуют или что у них за env —
это решает реестр.

Claude Code (.mcp.json) — JSON, значения секретов и структурных переменных —
подстановка `${VAR}`/`${VAR:-default}`, которую разворачивает сам Claude Code
из окружения при запуске. Это уже работает и не меняется.

Codex (.codex/config.toml) — TOML, `[mcp_servers.<id>]`. Подстановка `${VAR}`
внутри config.toml у Codex НЕ РАБОТАЕТ — значение уходит дочернему процессу
буквально, как строка (проверено запуском, docs/codex-facts.md, раздел 4).
Единственный проверенный способ передать значение из окружения аналитика —
`env_vars = ["ИМЯ", ...]`: список имён, которые Codex сам пробрасывает из
своего окружения в дочерний процесс под тем же именем. Значения со
спецсимволами (пробел, `$`, апостроф, обратная кавычка) через него проходят
без искажений — тоже проверено запуском. Поэтому здесь для ЛЮБОЙ переменной
с источником в .env (EnvVar — и секретной, и структурной) пишем имя в
`env_vars`, а не значение никуда. Только литеральные константы без источника
(StaticEnv) идут в `env`-таблицу текстом — им неоткуда утечь, потому что в
реестре у них и так нет ничего, кроме этого текста.

Путь к локальным скриптам (ProjectScript) пишется ОДИНАКОВО в обоих форматах
— относительным. У Claude Code это всегда было так через макрос
`${CLAUDE_PROJECT_DIR:-.}/<path>`. Для Codex раньше эта функция считала путь
абсолютным по аналогии с фактом про ${VAR} — предположение, не проверенное
запуском, и оно оказалось неверным: относительный путь в `args` разрешается
относительно рабочего каталога, из которого запущен сам `codex`, и это
проверено живым MCP-рукопожатием (см. отчёт задачи Codex-3). Раз оба формата
теперь используют относительный путь без каких-либо машинно-зависимых
данных, `.codex/config.toml` больше не привязан к конкретному диску — можно
класть в git и сверять с тем, что лежит на диске, тем же способом, что и
.mcp.json.

TOML пишем сами: в проекте разрешена только стандартная библиотека для слоя,
который выполняется до установки зависимостей (см. бриф задачи) — внешний
пакет вроде tomli_w сюда не тянем. Формат данных, которые тут встречаются
(command — строка, args — список строк, env — таблица строка→строка,
env_vars — список строк), простой: полный TOML-парсер не нужен, нужен только
корректный писатель для этого подмножества — включая экранирование
управляющих символов (перевод строки и т.п.) в TOML basic string, без этого
случайное значение с `\n` внутри дало бы файл, который Codex не распарсит.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    # Нужно, когда файл запущен напрямую (`python tools/render_configs.py`):
    # Python кладёт в sys.path директорию скрипта (tools/), а не корень
    # репозитория, и `import connectors.registry` иначе не находится.
    sys.path.insert(0, str(_REPO_ROOT))

from connectors.registry import Connector, EnvVar, ProjectScript, StaticEnv  # noqa: E402


# ── Claude Code: .mcp.json ─────────────────────────────────────────────────

def _mcp_json_env_value(item: EnvVar) -> str:
    if item.default is not None:
        return "${%s:-%s}" % (item.source, item.default)
    return "${%s}" % item.source


def _mcp_json_arg(item) -> str:
    if isinstance(item, ProjectScript):
        return "${CLAUDE_PROJECT_DIR:-.}/%s" % item.path
    return item


def render_mcp_json(connectors: Iterable[Connector]) -> str:
    """Собрать .mcp.json (формат Claude Code) из реестра коннекторов."""
    servers = {}
    for connector in connectors:
        entry = {
            "command": connector.command,
            "args": [_mcp_json_arg(a) for a in connector.args],
        }
        env = {}
        for item in connector.env:
            if isinstance(item, StaticEnv):
                env[item.name] = item.value
            else:
                env[item.target] = _mcp_json_env_value(item)
        if env:
            entry["env"] = env
        servers[connector.id] = entry
    return json.dumps({"mcpServers": servers}, indent=2, ensure_ascii=False) + "\n"


# ── Codex: .codex/config.toml ───────────────────────────────────────────────

# TOML basic-string escapes для управляющих символов (TOML spec, "Basic
# Strings"). Символы, для которых спецификация не даёт короткого escape
# (\b \t \n \f \r \" \\), кодируются \u00XX. У нас таких значений в реестре
# сегодня нет, но писатель обязан не производить битый файл, если появятся.
_TOML_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _toml_string(value: str) -> str:
    out = []
    for ch in value:
        if ch in _TOML_SHORT_ESCAPES:
            out.append(_TOML_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return '"%s"' % "".join(out)


def _toml_array(values: Iterable[str]) -> str:
    return "[%s]" % ", ".join(_toml_string(v) for v in values)


def _codex_arg(item) -> str:
    if isinstance(item, ProjectScript):
        return item.path
    return item


def _codex_spec(connector: Connector):
    """(command, args, env) для рендера Codex — своя ветка (`connector.codex`),
    если она задана (сейчас только у clickhouse-wms/clickhouse-dwh, см.
    докстринг registry.py, "РЕШЁННАЯ НАХОДКА"), иначе общая с Claude Code."""
    if connector.codex is not None:
        return connector.codex.command, connector.codex.args, connector.codex.env
    return connector.command, connector.args, connector.env


# ── Codex: базовые настройки профиля (задача Codex-5, "разрешения") ────────
#
# У Codex нет allow/deny-списка инструментов, как у Claude Code
# (.claude/settings.json) — только три рычага, и все три проверены живым
# запуском (`codex exec` с изолированным CODEX_HOME, отчёт задачи Codex-5):
#
# 1. Решение "спрашивать ли подтверждение на вызов конкретного
#    MCP-инструмента" Codex принимает ИСКЛЮЧИТЕЛЬНО по полю
#    `annotations.readOnlyHint`, которое отдаёт сам MCP-сервер в ответе на
#    `tools/list` — ни sandbox_mode, ни approval_policy тут не участвуют
#    (проверено во всех девяти комбинациях режим×политика, плюс отдельно
#    доверие проекту). Поэтому это НЕ настраивается здесь — эти два поля
#    прописаны непосредственно в connectors/trino_proxy.py и
#    connectors/superset_mcp.py, у тех инструментов, что уже одобрены для
#    Claude Code как read-only в .claude/settings.json. См.
#    tests/test_codex_permissions.py.
#
# 2. `sandbox_mode`/`approval_policy` ниже управляют ДРУГИМ: shell-командами,
#    которые Codex выполняет сам (не через MCP) — обычный exec-инструмент
#    агента. Здесь выставлен наименее опасный из документированных вариантов
#    (workspace-write вместо danger-full-access, on-request вместо never),
#    но это НЕ решает правила 2 (запрет чтения секретов/.env) и 3 (запись
#    только в work/) — оба проверены живым запуском и НЕ выполняются: чтение
#    файла любой shell-командой (cat/sed/...) не ограничено ни в одном
#    режиме sandbox_mode ("read-only" означает "без права записи", не
#    "чтение под контролем"), а approval_policy=untrusted по официальному
#    описанию флага сама держит cat/sed/ls в списке команд, не требующих
#    подтверждения, независимо от того, какой путь им передан. Подробности,
#    что проверялось и что не удалось выразить — в отчёте задачи Codex-5.
#
# 3. `--dangerously-bypass-approvals-and-sandbox` (обход подтверждений в
#    headless-режиме, `codex exec`) — это флаг ЗАПУСКА для автоматизации без
#    терминала, а не часть профиля; в config.toml он не прописывается
#    никогда — иначе профиль тихо восстановил бы дыру, которую чинит правило
#    1. Не путать с `--dangerously-bypass-hook-trust` (это отдельный флаг про
#    доверие хукам, см. docs/codex-facts.md, раздел 7).
CODEX_BASE_SETTINGS = {
    "sandbox_mode": "workspace-write",
    "approval_policy": "on-request",
}


def _render_codex_base_settings() -> str:
    lines = ["%s = %s" % (key, _toml_string(value)) for key, value in CODEX_BASE_SETTINGS.items()]
    return "\n".join(lines)


def render_codex_toml(connectors: Iterable[Connector]) -> str:
    """Собрать .codex/config.toml (формат Codex) из реестра коннекторов."""
    blocks = [_render_codex_base_settings()]
    for connector in connectors:
        command, raw_args, env_items = _codex_spec(connector)
        args = [_codex_arg(a) for a in raw_args]
        lines = [
            "[mcp_servers.%s]" % connector.id,
            "command = %s" % _toml_string(command),
            "args = %s" % _toml_array(args),
        ]

        env_vars = [item.target for item in env_items if isinstance(item, EnvVar)]
        if env_vars:
            lines.append("env_vars = %s" % _toml_array(env_vars))

        static_env = [item for item in env_items if isinstance(item, StaticEnv)]
        if static_env:
            lines.append("")
            lines.append("[mcp_servers.%s.env]" % connector.id)
            for item in static_env:
                lines.append("%s = %s" % (item.name, _toml_string(item.value)))

        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


# ── CLI: пишет оба файла на диск ────────────────────────────────────────────

def _write(path: Path, content: str) -> bool:
    """Записать файл, если содержимое изменилось. Возвращает True, если
    что-то реально записалось (для --check и человекочитаемого вывода)."""
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def main(argv=None) -> int:
    import argparse
    from connectors.registry import CONNECTORS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true",
        help="ничего не писать, только сверить с файлами на диске и вернуть "
             "код 1, если они разошлись с реестром",
    )
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parent.parent
    mcp_json_path = repo_root / ".mcp.json"
    codex_toml_path = repo_root / ".codex" / "config.toml"

    mcp_json = render_mcp_json(CONNECTORS)
    codex_toml = render_codex_toml(CONNECTORS)

    if args.check:
        stale = []
        for path, content in ((mcp_json_path, mcp_json), (codex_toml_path, codex_toml)):
            current = path.read_text(encoding="utf-8") if path.exists() else None
            if current != content:
                stale.append(str(path.relative_to(repo_root)))
        if stale:
            print("Разошлись с реестром: %s" % ", ".join(stale))
            return 1
        print("Оба конфига соответствуют connectors/registry.py")
        return 0

    changed = []
    if _write(mcp_json_path, mcp_json):
        changed.append(str(mcp_json_path.relative_to(repo_root)))
    if _write(codex_toml_path, codex_toml):
        changed.append(str(codex_toml_path.relative_to(repo_root)))

    if changed:
        print("Обновлено: %s" % ", ".join(changed))
    else:
        print("Оба конфига уже соответствуют connectors/registry.py")
    return 0


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
