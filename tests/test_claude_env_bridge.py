"""Тесты на мостик окружения для Claude Code (connectors/claude_env_bridge.py).

Находка с живой машины аналитика (14.08.2026): подстановка `${VAR}` в
`.mcp.json` работает только когда окружение Claude Code подготовил
`bin/uzum`. Сессию, поднятую мимо него (Claude Desktop после рестарта
приложения, голый `claude` в терминале), Claude Code запускает всё равно —
и передаёт плейсхолдер дочернему процессу БУКВАЛЬНО: у ClickHouse хостом
становится строка `${CH_DWH_HOST}` (у аналитика — дословно эта ошибка).
Мостик обязан пересобрать переменные из окружения → secrets.env → default
и не отдать ребёнку ни литерал, ни пустую строку.

Секретов в файле нет — только имена переменных и выдуманные значения.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from connectors import claude_env_bridge  # noqa: E402
from connectors.registry import CONNECTORS  # noqa: E402

BRIDGE_PATH = REPO_ROOT / "connectors" / "claude_env_bridge.py"
BY_ID = {c.id: c for c in CONNECTORS}


# ── connector_env: чистая функция разрешения ─────────────────────────────

def test_the_rida_case_a_literal_placeholder_is_replaced_from_secrets():
    """Ровно то, что случилось на живой машине: Claude Desktop передал
    мостику литерал `${CH_DWH_HOST}` под именем CLICKHOUSE_HOST, а значение
    лежит в secrets.env. Ребёнок обязан увидеть значение, не литерал."""
    environ = {"CLICKHOUSE_HOST": "${CH_DWH_HOST}", "PATH": "/usr/bin"}
    secrets = {"CH_DWH_HOST": "dwh.internal", "CH_DWH_USER": "u",
               "CH_DWH_PASSWORD": "p"}

    env = claude_env_bridge.connector_env(BY_ID["clickhouse-dwh"], environ, secrets)

    assert env["CLICKHOUSE_HOST"] == "dwh.internal"
    assert env["CLICKHOUSE_USER"] == "u"
    assert env["CLICKHOUSE_PASSWORD"] == "p"
    assert env["CLICKHOUSE_PORT"] == "8123", "дефолт порта не подставился"


def test_a_placeholder_without_a_value_anywhere_is_removed_not_passed_on():
    """Нет значения ни в окружении, ни в secrets.env, ни в дефолтах — имя
    убирается вовсе: литерал `${VAR}` пакет счёл бы настоящим значением
    (mcp-clickhouse честно ходил бы на хост `${ch_dwh_host}`), а пустая
    строка означала бы «доступ есть, но не работает»."""
    environ = {"CLICKHOUSE_HOST": "${CH_DWH_HOST}", "CLICKHOUSE_USER": "${CH_DWH_USER}"}

    env = claude_env_bridge.connector_env(BY_ID["clickhouse-dwh"], environ, {})

    assert "CLICKHOUSE_HOST" not in env
    assert "CLICKHOUSE_USER" not in env


def test_the_environment_wins_over_secrets_and_secrets_win_over_defaults():
    """Порядок тот же, что и всегда был при запуске через uzum: явно
    заданное окружение сильнее файла (им пользуются тесты и переопределения
    вида `CH_DWH_HOST=… uzum`), файл сильнее дефолта из реестра."""
    connector = BY_ID["clickhouse-dwh"]

    env = claude_env_bridge.connector_env(
        connector,
        {"CH_DWH_HOST": "из-окружения"},
        {"CH_DWH_HOST": "из-файла", "CH_DWH_PORT": "9000"},
    )
    assert env["CLICKHOUSE_HOST"] == "из-окружения"
    assert env["CLICKHOUSE_PORT"] == "9000", "secrets.env должен перебить дефолт"

    env = claude_env_bridge.connector_env(connector, {}, {"CH_DWH_HOST": "из-файла"})
    assert env["CLICKHOUSE_HOST"] == "из-файла"
    assert env["CLICKHOUSE_PORT"] == "8123"


def test_an_empty_value_is_treated_as_absent():
    """Пустая строка в окружении (`${CONFLUENCE_TOKEN:-}` разворачивается
    в пустоту, когда переменной нет) — то же, что отсутствие: дальше по
    цепочке, а не «пустой токен» ребёнку. Семантика та же, что у
    codex_env_overlay — расхождение мостиков означало бы, что один и тот
    же коннектор под двумя движками видит разные значения."""
    connector = BY_ID["atlassian"]

    env = claude_env_bridge.connector_env(
        connector, {"CONFLUENCE_PERSONAL_TOKEN": "", "JIRA_TOKEN": "т"}, {})
    assert "CONFLUENCE_PERSONAL_TOKEN" not in env

    env = claude_env_bridge.connector_env(
        connector, {"CONFLUENCE_PERSONAL_TOKEN": ""},
        {"CONFLUENCE_TOKEN": "конф-токен"})
    assert env["CONFLUENCE_PERSONAL_TOKEN"] == "конф-токен"


def test_missing_optional_confluence_token_leaves_a_working_jira_only_setup():
    """Без CONFLUENCE_TOKEN у mcp-atlassian должен настроиться один Jira:
    проверено живым MCP-рукопожатием (14.08.2026) — с пустым/отсутствующим
    CONFLUENCE_PERSONAL_TOKEN пакет отдаёт 63 jira_* инструмента и ни
    одного confluence_*. Мостик обязан дать ровно эту конфигурацию."""
    env = claude_env_bridge.connector_env(
        BY_ID["atlassian"], {}, {"JIRA_TOKEN": "т"})

    assert env["JIRA_PERSONAL_TOKEN"] == "т"
    assert env["JIRA_URL"] == "https://jira.uzum.com"
    assert env["CONFLUENCE_URL"] == "https://confluence.uzum.com"
    assert "CONFLUENCE_PERSONAL_TOKEN" not in env


def test_static_env_is_set_and_the_rest_of_the_environment_survives():
    env = claude_env_bridge.connector_env(
        BY_ID["clickhouse-wms"], {"PATH": "/usr/bin", "LANG": "C"},
        {"CH_WMS_HOST": "wms.internal"})

    assert env["MCP_TRANSPORT"] == "stdio"
    assert env["CLICKHOUSE_VERIFY"] == "false"
    assert env["CHDB_ENABLED"] == "false"
    assert env["PATH"] == "/usr/bin"
    assert env["LANG"] == "C"


def test_the_function_does_not_mutate_the_environment_it_was_given():
    environ = {"CLICKHOUSE_HOST": "${CH_DWH_HOST}"}
    claude_env_bridge.connector_env(BY_ID["clickhouse-dwh"], environ,
                                    {"CH_DWH_HOST": "x"})
    assert environ == {"CLICKHOUSE_HOST": "${CH_DWH_HOST}"}


# ── сквозные: настоящий процесс ──────────────────────────────────────────

def _env_dump_stub(path, dump_path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump({'argv': sys.argv, 'env': dict(os.environ)},"
        " open(%r, 'w'))\n" % str(dump_path),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _home_with_secrets(tmp_path, text):
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text(text, encoding="utf-8")
    return home


def test_bridge_resolves_from_secrets_env_when_the_session_was_not_launched_via_uzum(tmp_path):
    """Сквозной сценарий Риды: окружение процесса — как у Claude Desktop
    (секретов нет, target-имена пришли литералами из .mcp.json), значения
    лежат только в ~/.config/uzum-ai/secrets.env."""
    dump = tmp_path / "dump.json"
    stub = tmp_path / "fake-server"
    _env_dump_stub(stub, dump)
    home = _home_with_secrets(
        tmp_path,
        "CH_DWH_HOST='dwh.internal'\n"
        "CH_DWH_USER='u'\n"
        "CH_DWH_PASSWORD='пароль с пробелом и $знаком'\n",
    )

    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "CLICKHOUSE_HOST": "${CH_DWH_HOST}",
           "CLICKHOUSE_USER": "${CH_DWH_USER}",
           "CLICKHOUSE_PASSWORD": "${CH_DWH_PASSWORD}"}
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "clickhouse-dwh", "--",
         str(stub), "--with", "pyarrow", "mcp-clickhouse"],
        env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    child = json.loads(dump.read_text(encoding="utf-8"))
    assert child["env"]["CLICKHOUSE_HOST"] == "dwh.internal"
    assert child["env"]["CLICKHOUSE_PASSWORD"] == "пароль с пробелом и $знаком"
    assert child["env"]["CLICKHOUSE_PORT"] == "8123"
    assert child["argv"][1:] == ["--with", "pyarrow", "mcp-clickhouse"], (
        "аргументы запуска изменились: %r" % (child["argv"],))
    assert not any("пароль" in a for a in child["argv"]), (
        "значение попало в командную строку — его видно в ps")


def test_bridge_is_a_no_op_when_uzum_already_prepared_the_environment(tmp_path):
    """Запуск через bin/uzum: секреты уже в окружении, Claude Code уже
    развернул подстановки. Мостик обязан дать побайтово те же значения —
    он пересобирает их из тех же источников, а не из env-словаря."""
    dump = tmp_path / "dump.json"
    stub = tmp_path / "fake-server"
    _env_dump_stub(stub, dump)
    home = _home_with_secrets(tmp_path, "CH_DWH_HOST='из-файла-не-должно-победить'\n")

    env = {"PATH": os.environ["PATH"], "HOME": str(home),
           "CH_DWH_HOST": "dwh.internal", "CH_DWH_USER": "u", "CH_DWH_PASSWORD": "p",
           "CLICKHOUSE_HOST": "dwh.internal", "CLICKHOUSE_USER": "u",
           "CLICKHOUSE_PASSWORD": "p"}
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "clickhouse-dwh", "--", str(stub)],
        env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    child = json.loads(dump.read_text(encoding="utf-8"))
    assert child["env"]["CLICKHOUSE_HOST"] == "dwh.internal"


def test_bridge_refuses_an_unknown_connector_id(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "no-such-connector", "--", "true"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "no-such-connector" in result.stderr
    assert "clickhouse-dwh" in result.stderr, "список известных id не напечатан"


def test_bridge_without_a_command_explains_itself(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "atlassian"],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "Использование" in result.stderr


def test_bridge_reports_a_command_it_cannot_start(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "atlassian", "--",
         str(tmp_path / "no-such-binary-4f2a")],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "no-such-binary-4f2a" in result.stderr
