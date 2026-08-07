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


# ── Codex: именованный профиль разрешений (задача Codex-5, "разрешения") ───
#
# У Codex нет allow/deny-списка инструментов, как у Claude Code
# (.claude/settings.json) — только два независимых рычага, оба проверены
# живым запуском (`codex exec` с изолированным CODEX_HOME, отчёт задачи
# Codex-5):
#
# 1. Решение "спрашивать ли подтверждение на вызов конкретного
#    MCP-инструмента" Codex принимает ИСКЛЮЧИТЕЛЬНО по полю
#    `annotations.readOnlyHint`, которое отдаёт сам MCP-сервер в ответе на
#    `tools/list` — этот именованный профиль на MCP-вызовы не влияет
#    (проверено отдельно: readOnlyHint-инструмент выполняется молча и с этим
#    профилем тоже). Поэтому это НЕ настраивается здесь — оба поля
#    прописаны непосредственно в connectors/trino_proxy.py и
#    connectors/superset_mcp.py. См. tests/test_codex_permissions.py.
#
# 2. Правила 2 (запрет чтения секретов/.env) и 3 (запись только в work/)
#    решает ИМЕННО именованный профиль ниже (`[permissions.<имя>]` +
#    `default_permissions`), а НЕ `sandbox_mode`/`[sandbox_workspace_write]`.
#    ИСПРАВЛЕНО по итогам ревью безопасности: здесь раньше было утверждение,
#    что Codex не даёт сочетать sandbox_mode с именованным профилем и что
#    при обоих заданных профиль «молча не применяется» — это было передано
#    координатором как факт из документации, не проверено запуском и
#    оказалось НЕВЕРНЫМ (ревьюер проверил живым запуском: при обоих полях
#    сразу профиль применяется, `.env` по-прежнему недоступен; перепроверено
#    самостоятельно на чистом, ни разу не использованном пути проекта —
#    подтверждено). `sandbox_mode`/`[sandbox_workspace_write]` здесь просто
#    не заданы — гигиена (единственный источник политики файловой системы),
#    не техническая необходимость.
#
#    Настоящая причина, по которой ПЕРВАЯ версия этого файла (только
#    sandbox_mode=workspace-write, без профиля) не могла выразить правила
#    2/3: у sandbox_mode/approval_policy попросту нет понятия «запретить
#    чтение конкретного пути» — команда `cat .env` реально исполнялась
#    (подтверждено: секрет присутствовал в сыром выводе исполнения shell),
#    а видимость защиты в интерактивном ответе давал только добровольный
#    отказ самой модели печатать значение дословно — то же мягкое, легко
#    обходимое поведение (обходится подменой `cat` на `cp secrets.env
#    work/copied.txt`), которое коннекторы этого репозитория уже сознательно
#    не считают защитой (см. commit b6f60f8 про SQL).
#
#    Профиль `uzum` наследует встроенный `:workspace` (`extends`) и поверх
#    него задаёт три правила filesystem (проверено живым запуском,
#    отчёт задачи Codex-5, обходы 1-4 из ревью):
#      - `~/.config/uzum-ai/**` = deny — единственное место, где секреты
#        лежат открытым текстом вне репозитория (см. connectors/ACCESS.md).
#      - `<repo>/**/*.env` = deny — `.env` в корне репозитория и в любой
#        вложенной папке (в т.ч. work/), той же цели.
#      - `<repo>/**` = read, `<repo>/work/**` = write — читать можно весь
#        репозиторий, писать только в work/.
#    ВАЖНО: пути АБСОЛЮТНЫЕ, не через специальный ключ `:workspace_roots`.
#    Первая попытка использовала `:workspace_roots = { "work/**" = "write",
#    ... }` — она успешно проходила все прямые проверки, но не пережила
#    запуск с `-C work` (или любой другой сменой рабочего корня агента):
#    `:workspace_roots` пересчитывается от ТЕКУЩЕГО рабочего корня Codex, то
#    есть `-C work` делает "work/" новым текущим корнем и запись за его
#    пределами (в старый корень репозитория) снова разрешена — живая
#    проверка, `echo z1 > ../outside.txt` после `-C work` прошла (exit 0,
#    файл реально создан). Абсолютные пути этой уязвимости не имеют —
#    тот же `-C work` с абсолютными путями в профиле не создал файл
#    (`operation not permitted`, файла на диске нет). Абсолютный путь
#    вычисляется в момент генерации из `_REPO_ROOT` (см. верх файла) — то
#    есть меняется от машины к машине; `.codex/config.toml`, закоммиченный
#    в git, отражает путь той машины, где его в последний раз
#    регенерировали (`setup.sh`/`tools/render_configs.py`) — при клонировании
#    на другую машину аналитик обязан прогнать генератор заново (то же
#    самое, что уже верно для секретов и остального в setup.sh), иначе
#    правило "работать в work/" будет ссылаться на чужой путь.
#
#    `--dangerously-bypass-approvals-and-sandbox` (обход подтверждений и
#    песочницы в headless-режиме, `codex exec`) — флаг ЗАПУСКА для
#    автоматизации без терминала, а не часть профиля; в config.toml он не
#    прописывается никогда. Не путать с `--dangerously-bypass-hook-trust`
#    (тот — про доверие хукам, см. docs/codex-facts.md, раздел 7).
#
#    ВТОРАЯ находка, ещё важнее первой: `extends = ":workspace"` НЕ подошёл
#    как база профиля — живой запуск в изолированной песочнице (не git) с
#    `extends=":workspace"` корректно блокировал запись вне work/, но ТОТ ЖЕ
#    профиль в НАСТОЯЩЕМ git-репозитории (этот самый репозиторий) — нет:
#    `echo x > outside.txt` в корне репозитория молча создавал файл, хотя
#    правило `<repo>/work/**` = write было единственным write-правилом в
#    профиле. Причина: `:workspace` сам по себе даёт файлам ВНУТРИ
#    git-репозитория неявное разрешение на запись (видимо для обычных
#    правок кода агентом), и явные правила `read`/`write` в профиле его не
#    отменяют — отменяет только `deny`, а `deny` заодно убивает и чтение
#    (правило 2 требует читать весь репозиторий, значит `deny` на весь
#    репозиторий не годится). Фикс — базовый профиль `:read-only` (не даёт
#    НИКАКОЙ записи по умолчанию), поверх которого правило `work/**` = write
#    — единственный источник права записи. Проверено живым запуском заново
#    в настоящем git-репозитории (не только в изолированной песочнице):
#    запись вне work/ блокируется даже после `-C work`, запись в work/ и
#    чтение всего репозитория по-прежнему работают. См. отчёт задачи
#    Codex-5, раздел "доработка по ревью".
#
#    ТРЕТЬЯ находка (Critical, ревью безопасности): этот файл сам по себе,
#    лежащий в git-репозитории, НЕ имеет никакого эффекта, пока что-то
#    (мастер установки, задача про лаунчер) не свяжет его с тем, что реально
#    читает Codex — `$CODEX_HOME/config.toml`. Проверено живым запуском:
#    Codex НЕ подхватывает `<repo>/.codex/config.toml` автоматически по
#    текущему каталогу (в отличие от `.mcp.json` у Claude Code) — со свежим
#    `$CODEX_HOME` без единого файла конфига `cat .env` в этом самом
#    репозитории реально исполняется, значение секрета присутствует в сыром
#    выводе команды, а баннер показывает легаси `sandbox: read-only` вместо
#    `custom permissions`. Это НЕ вопрос доверия каталогу (проверено
#    отдельно: ни наличие, ни отсутствие `[projects."<path>"] trust_level =
#    "trusted"` не меняет поведение `codex exec`, когда конфиг РЕАЛЬНО
#    загружен — деталь и границы проверки в отчёте задачи Codex-5) — до тех
#    пор, пока `$CODEX_HOME` не указывает на файл с этим профилем, защиты
#    нет вообще, при каждом запуске, а не только при первом. См.
#    tests/test_codex_permissions.py, живые тесты в конце файла.
CODEX_PERMISSION_PROFILE_EXTENDS = ":read-only"
CODEX_PERMISSION_PROFILE_NAME = "uzum"
CODEX_SECRETS_HOME_GLOB = "~/.config/uzum-ai/**"


def _codex_permission_profile_filesystem_rules(repo_root: Path) -> dict:
    # ПРИНЯТОЕ ОГРАНИЧЕНИЕ (найдено ревью безопасности, Critical #2,
    # проверено запуском): правило `**/*.env` = deny матчит ПУТЬ файла, а не
    # его содержимое — если `.env` хоть раз попадал в git-индекс (даже если
    # потом убран из отслеживания), его блоб остаётся читаем по хэшу через
    # `git cat-file -p <hash>`, потому что объекты git лежат в `.git/objects/`,
    # а не по имени `.env`, и подпадают под общее правило `read` на весь
    # репозиторий. Живой запуск подтвердил обход буквально: `git rm --cached
    # .env` + коммит не мешают `git cat-file -p <старый-хэш>` вывести секрет
    # целиком.
    #
    # НЕ закрыто сознательно, не по недосмотру: `deny` на `.git/**` целиком
    # ломает git полностью (`git status` → "fatal: not a git repository" —
    # проверено запуском), а частичный deny на `.git/objects/**` сломал бы
    # ровно то же самое (git читает объекты для diff/log/status/checkout —
    # для ЛЮБОЙ операции, не только "опасных"). Разница между "объект в
    # истории" и "объект на диске сейчас" в git принципиально не выражается
    # через ACL на пути — это свойство content-addressable хранилища, а не
    # дефект этого профиля; тот же обход существует для ЛЮБОГО инструмента,
    # который просто разрешает запускать `git`, независимо от движка или
    # ACL-системы. Единственное реальное средство от секрета, попавшего в
    # git — считать его скомпрометированным и ротировать, а не полагаться на
    # то, что какой-то ACL спрячет его обратно.
    #
    # Почему риск принят, а не является активной дырой сегодня: `.env`
    # исключён в `.gitignore` (`*.env`), и `git log --all --full-history --
    # '*.env' '.env'` в этом репозитории пуст — секрет никогда не попадал в
    # индекс. Если это когда-нибудь изменится (случайный `git add -A`,
    # например) — единственно верный ответ тот же самый: ротация секрета,
    # а не правка этого профиля.
    repo_abs = str(repo_root.resolve())
    return {
        CODEX_SECRETS_HOME_GLOB: "deny",
        "%s/**/*.env" % repo_abs: "deny",
        "%s/**" % repo_abs: "read",
        "%s/work/**" % repo_abs: "write",
    }


def _render_codex_permission_profile(repo_root: Path) -> str:
    """default_permissions + [permissions.<имя>] — см. докстринг выше про то,
    почему это заменило sandbox_mode/approval_policy, а не дополнило их
    (Codex не даёт сочетать именованный профиль с sandbox_mode — при обоих
    сразу профиль молча не применяется)."""
    lines = [
        "default_permissions = %s" % _toml_string(CODEX_PERMISSION_PROFILE_NAME),
        "",
        "[permissions.%s]" % CODEX_PERMISSION_PROFILE_NAME,
        "description = %s" % _toml_string(
            "uzum-data-agent: чтение репозитория, запись только в work/, "
            "секреты (.env и ~/.config/uzum-ai) недоступны для чтения"
        ),
        "extends = %s" % _toml_string(CODEX_PERMISSION_PROFILE_EXTENDS),
        "",
        "[permissions.%s.filesystem]" % CODEX_PERMISSION_PROFILE_NAME,
    ]
    for pattern, access in _codex_permission_profile_filesystem_rules(repo_root).items():
        lines.append("%s = %s" % (_toml_string(pattern), _toml_string(access)))
    return "\n".join(lines)


def render_codex_toml(connectors: Iterable[Connector], repo_root: Path = None) -> str:
    """Собрать .codex/config.toml (формат Codex) из реестра коннекторов.

    `repo_root` по умолчанию — реальный корень этого репозитория
    (`_REPO_ROOT`, вычисленный из расположения этого файла на диске);
    параметр существует, чтобы тесты могли подставить временный путь и
    не зависеть от того, где именно лежит рабочая копия при прогоне CI."""
    if repo_root is None:
        repo_root = _REPO_ROOT
    blocks = [_render_codex_permission_profile(repo_root)]
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
