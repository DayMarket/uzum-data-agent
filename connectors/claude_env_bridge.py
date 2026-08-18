#!/usr/bin/env python3
"""Мостик окружения для Claude Code: достраивает переменные коннектора из
~/.config/uzum-ai/secrets.env и запускает настоящий процесс сервера.

ЗАЧЕМ. Подстановка `${VAR}` в `.mcp.json` берёт значения из окружения
процесса Claude Code — и вся схема держалась на том, что это окружение
подготовил `bin/uzum` (он source-ит secrets.env перед запуском движка). Но
сессию Claude Code поднимают не только через `uzum`: приложение Claude
Desktop перезапускает MCP-серверы со СВОИМ окружением (после рестарта
приложения — воспроизведено на живой машине аналитика 14.08.2026), и голый
`claude` в терминале — тоже легальный путь. В таком окружении секретов нет,
и Claude Code передаёт плейсхолдер дочернему процессу БУКВАЛЬНО: проверено
живым headless-запуском — сервер стартует, а в его env лежит строка
`${NO_SUCH_VAR_XYZ}` как есть (докстринг lib/envfile.py, писавшийся по
более ранней версии Claude Code, утверждает «валит запись MCP-сервера
целиком» — на текущей версии это уже не так). Итог у аналитика: ClickHouse
честно жалуется, что хоста `${ch_dwh_host}` не существует, а коннекторы
потише просто молчат.

ЧТО ДЕЛАЕТ. Для каждой EnvVar своего коннектора значение берётся заново:
окружение процесса (его подготовил uzum или задал сам аналитик) → сам
secrets.env → default из реестра. Что нашлось — кладётся под target-именем;
что не нашлось — target-имя из окружения УБИРАЕТСЯ, чтобы дочернему
процессу не уехал ни литеральный `${VAR}`, ни пустая строка «доступ есть,
но не работает» (та же семантика, что у codex_env_overlay в
connectors/codex_env_bridge.py). StaticEnv кладётся как есть. Дальше
os.execvpe настоящей команды — значения не пересекают границу шелла и не
светятся в ps, ровно как у Codex-мостика.

ПОЧЕМУ target-имена всё равно остаются в `.mcp.json`. Мостик получает
команду сервера после `--` (passthrough, как codex_env_bridge), а env-словарь
`.mcp.json` продолжает рендериться: по нему `claude mcp list` печатает
«Missing environment variables» (паритет с registry.required_sources,
сверяется тестом), и по нему видно, какие переменные у коннектора вообще
есть. Значения из этого словаря мостик не читает — он их пересобирает; при
запуске через `uzum` результат побайтово тот же.

ГРАНИЦЫ. Codex этот мостик не касается: там окружение готовит
codex_env_bridge на весь процесс `codex` разом (у Codex нет per-server
env-словаря). Телеметрия хуков — тоже не здесь: хуки запускает движок, не
MCP-слой, поэтому тот же запасной путь «окружение → secrets.env» встроен в
lib/telemetry.py::Config.from_env отдельно.

Использование (пишет tools/render_configs.py, руками не набирается):

    python3 connectors/claude_env_bridge.py <id-коннектора> -- <команда...>
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    # Файл запускается напрямую: Python кладёт в sys.path каталог скрипта
    # (connectors/), а не корень репозитория.
    sys.path.insert(0, str(_REPO_ROOT))
if str(_REPO_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "lib"))

import envfile  # noqa: E402
from connectors.registry import CONNECTORS, EnvVar, StaticEnv  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")

# Неразвёрнутая подстановка из `.mcp.json` — ровно те две формы, которые
# пишет tools/render_configs.py: `${VAR}` и `${VAR:-дефолт}`. Целиком, а не
# «содержит»: пароль, внутри которого есть такие символы, — обычное
# значение, и терять его нельзя.
_UNEXPANDED = re.compile(r"\A\$\{[A-Za-z_][A-Za-z0-9_]*(:-.*)?\}\Z", re.DOTALL)


def _from_environ(environ, name):
    """Значение из окружения процесса — или None, если Claude Code передал
    под этим именем сам плейсхолдер.

    Второй случай выглядит как значение (строка непустая), но значением не
    является: подставить его было нечем, и в дочерний процесс уехала бы
    строка `${SUPERSET_PASSWORD}`.

    До этой развилки дефект прятался за именами. У большинства переменных
    source и target разные (CH_DWH_HOST → CLICKHOUSE_HOST): литерал
    приезжает под target, а спрашиваем мы source — не встречаются. Но у
    superset source и target — одно имя, и `environ.get(item.source)`
    возвращал ровно тот литерал, который сам же и должен был вычистить.
    Живой симптом (18.08.2026): `./setup.sh --add superset` печатает «✓ вижу
    300 дашбордов» (мастер проверяет вход своими значениями, мимо мостика),
    а сессия падает с «Keycloak: Invalid username or password».

    Codex-мостику такая развилка не нужна: у Codex нет per-server env-словаря
    (окружение готовит codex_env_bridge на весь процесс разом), и взяться
    плейсхолдеру в его source_env неоткуда.
    """
    value = environ.get(name)
    if value and _UNEXPANDED.match(value):
        return None
    return value


def connector_env(connector, environ, secrets) -> dict:
    """Окружение дочернего процесса: копия `environ` с достроенными target-
    переменными коннектора. Чистая функция — ничего не пишет в os.environ.

    Пусто (нет в environ, пустая строка или неразвёрнутый плейсхолдер из
    `.mcp.json` — см. _from_environ) — смотрим secrets, потом default. Нет нигде — target-имя убирается вовсе: в environ под ним мог
    приехать литеральный `${VAR}` из `.mcp.json`, а пустой токен означал бы
    «доступ есть, но не работает» вместо «доступа нет» (см.
    codex_env_overlay — семантика та же, расхождение мостиков означало бы,
    что один и тот же коннектор под двумя движками видит разные значения).
    """
    env = dict(environ)
    for item in connector.env:
        if isinstance(item, StaticEnv):
            env[item.name] = item.value
            continue
        if not isinstance(item, EnvVar):
            continue
        value = _from_environ(environ, item.source) or secrets.get(item.source) or item.default
        if value:
            env[item.target] = value
        else:
            env.pop(item.target, None)
    return env


def main(argv) -> int:
    if len(argv) < 4 or argv[2] != "--":
        sys.stderr.write(
            "Использование: %s <id-коннектора> -- <команда> [аргументы]\n"
            % os.path.basename(argv[0])
        )
        return 2
    by_id = {c.id: c for c in CONNECTORS}
    connector = by_id.get(argv[1])
    if connector is None:
        sys.stderr.write(
            "Неизвестный коннектор %r — в connectors/registry.py есть: %s\n"
            % (argv[1], ", ".join(sorted(by_id)))
        )
        return 2
    command = argv[3:]
    env = connector_env(connector, os.environ, envfile.read(SECRETS_PATH))
    try:
        os.execvpe(command[0], command, env)
    except OSError as exc:
        # Сюда попадаем, только если запустить не удалось: при успехе
        # execvpe не возвращается вовсе.
        sys.stderr.write("Не удалось запустить %s: %s\n" % (command[0], exc))
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
