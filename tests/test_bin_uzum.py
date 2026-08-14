"""Тесты на bin/uzum — находка ревью (задача Codex-6, второй раунд).

`bin/uzum` дёргает `python3 -c "... setup_helpers.select_engine(...) ..."`
для выбора движка (первый раунд ревью свёл эту логику к одной реализации —
см. lib/setup_helpers.py). Но сама эта правка ввела новую зависимость от
внешнего процесса ровно в развилке выбора движка: если помощник не
отработал (упал, вернул не то, интерпретатора вообще нет) — переменные
ENGINE/REASON пустые, ни одна ветка case не совпадает, и раньше скрипт
безусловно проваливался в `exec claude` с кодом успеха. Человек попросил
Codex — получил Claude Code, и ничто ему об этом не сказало. Тот же класс
дефекта, что уже дважды ловили на этом проекте (выглядит как рабочий
запуск, а на самом деле нет).

Копируем `bin/uzum` (и, где нужно для контроля, `lib/setup_helpers.py`) в
изолированную временную папку — подставной REPO_DIR, чтобы не трогать
настоящий репозиторий и не зависеть от PATH машины, где гоняются тесты.
Тот же приём, что уже используется для хуков (`tests/test_session_start.py`
— `subprocess.run(["bash", str(script)], ...)`), только для bin/uzum.
"""
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_UZUM = REPO_ROOT / "bin" / "uzum"


def _stub_engine(path):
    """Заглушка claude/codex — печатает, что её вызвали, и с чем, и
    завершается успешно. Если она хоть раз попадёт в вывод в тестах ниже —
    значит движок реально запустился, это и есть проверяемый факт.

    Плюс, когда задан UZUM_TEST_DUMP_DIR, сбрасывает туда СВОЁ окружение и
    СВОЙ argv. Это именно снимок процесса, а не список того, что заглушка
    согласилась подтвердить: она не знает, какие имена от неё ждут, и
    печатает всё подряд (`env`). Нужен для тестов про мостик окружения
    Codex ниже."""
    path.write_text(
        "#!/usr/bin/env bash\n"
        "name=\"$(basename \"$0\")\"\n"
        "echo \"STUB $name called args=[$*]\"\n"
        "if [ -n \"${UZUM_TEST_DUMP_DIR:-}\" ]; then\n"
        "  env > \"$UZUM_TEST_DUMP_DIR/$name.env\"\n"
        "  printf '%s\\n' \"$@\" > \"$UZUM_TEST_DUMP_DIR/$name.argv\"\n"
        "fi\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_repo(tmp_path, broken_helper=False):
    """Минимальная копия репозитория, достаточная для запуска bin/uzum:
    сам bin/uzum и lib/setup_helpers.py (+ envfile.py и hook_scope.py, от
    которых setup_helpers.py зависит на уровне импорта)."""
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "lib").mkdir(parents=True)
    (repo / "connectors").mkdir()
    (repo / "work").mkdir()
    shutil.copy(BIN_UZUM, repo / "bin" / "uzum")
    # Правило «команда есть И запускается здесь» (см. tests/test_win_path.py):
    # bin/uzum подключает его файлом, а не держит свою копию, поэтому в
    # минимальном репозитории он тоже обязан быть. Без него лаунчер не
    # определит ни одного движка — ровно так этот тест и упал, когда правило
    # переехало в lib/.
    shutil.copy(REPO_ROOT / "lib" / "win_path.sh", repo / "lib" / "win_path.sh")
    (repo / "bin" / "uzum").chmod(0o755)
    # Мостик окружения Codex и реестр, из которого он берёт соответствие
    # source→target (см. тесты про запуск Codex ниже).
    shutil.copy(REPO_ROOT / "connectors" / "registry.py", repo / "connectors" / "registry.py")
    shutil.copy(REPO_ROOT / "connectors" / "codex_env_bridge.py",
                repo / "connectors" / "codex_env_bridge.py")
    if broken_helper:
        # Синтаксически валидный python-модуль, который безусловно падает
        # при импорте — воспроизводит "вспомогательный процесс не
        # отработал" любой причиной (исключение), не только отсутствием
        # интерпретатора.
        (repo / "lib" / "setup_helpers.py").write_text(
            "raise RuntimeError('сломанный помощник — намеренно для теста')\n",
            encoding="utf-8",
        )
    else:
        shutil.copy(REPO_ROOT / "lib" / "setup_helpers.py", repo / "lib" / "setup_helpers.py")
        shutil.copy(REPO_ROOT / "lib" / "envfile.py", repo / "lib" / "envfile.py")
        shutil.copy(REPO_ROOT / "lib" / "hook_scope.py", repo / "lib" / "hook_scope.py")
    return repo


def _isolated_home(tmp_path, engine_bin_dir, secrets="CH_USER='t'\n"):
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text(secrets, encoding="utf-8")
    engine_bin_dir.mkdir()
    _stub_engine(engine_bin_dir / "claude")
    _stub_engine(engine_bin_dir / "codex")
    return home


def _curated_path(extra_dirs, include_python):
    """PATH со стандартными утилитами (dirname, cat, sed, ...), собранный
    ЯВНЫМ куратором — символические ссылки на конкретные бинари по имени, а
    не подключение целых системных каталогов. Два свойства, которые важны
    для тестов ниже и которых не даёт просто "взять PATH процесса":

      1. `include_python=False` — python3 гарантированно НЕДОСТИЖИМ (не
         просто "не установлен где-то", а буквально нет ни одного пути в
         PATH, где его можно найти), а не просто "может, а может и не
         найдётся, в зависимости от машины, где гоняются тесты".
      2. Не протаскивает случайные claude/codex, если они реально стоят на
         машине, где гоняется тест (иначе сценарий "заглушку codex убрали —
         значит codex недоступен" был бы неверным: PATH нашёл бы настоящий
         codex дальше по списку каталогов)."""
    tools_dir = extra_dirs[0].parent / ("coreutils-with-python" if include_python else "coreutils-no-python")
    tools_dir.mkdir(exist_ok=True)
    needed = ["dirname", "basename", "readlink", "cat", "mkdir", "sed",
              "printf", "grep", "rm", "ls", "bash", "true", "false", "env"]
    if include_python:
        needed.append("python3")
    for name in needed:
        real = shutil.which(name)
        target = tools_dir / name
        if real and not target.exists():
            target.symlink_to(real)
    parts = [str(d) for d in extra_dirs] + [str(tools_dir)]
    return ":".join(parts)


def _run_uzum(repo, home, path, args=(), input_text="\n", dump_dir=None):
    env = {"HOME": str(home), "PATH": path, "USER": "test", "TERM": "xterm"}
    if dump_dir is not None:
        env["UZUM_TEST_DUMP_DIR"] = str(dump_dir)
    return subprocess.run(
        ["bash", str(repo / "bin" / "uzum"), *args],
        env=env, cwd=str(repo), input=input_text,
        capture_output=True, text=True, timeout=30,
    )


def test_broken_helper_fails_loudly_instead_of_defaulting_to_claude(tmp_path):
    """Critical, второй раунд ревью: вспомогательный python-процесс упал
    (здесь — импорт lib/setup_helpers.py бросает исключение) — bin/uzum
    обязан завершиться ненулевым кодом и явным сообщением, а не молча
    запустить Claude Code (та самая тихая заглушка, которую как раз
    искореняли всей этой задачей)."""
    repo = _make_repo(tmp_path, broken_helper=True)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode != 0, "сломанный помощник не должен давать код успеха"
    combined = result.stdout + result.stderr
    assert "STUB claude" not in combined, (
        "молча запустил Claude Code вместо честной ошибки:\n" + combined
    )
    assert "STUB codex" not in combined


def test_missing_python3_fails_loudly_with_a_specific_message(tmp_path):
    """Второй сценарий ревью, вполне реалистичный: python3 отсутствует в
    PATH целиком, оба движка при этом установлены. Раньше это давало одну
    строку "command not found" в stderr и тут же тихий запуск Claude Code
    с кодом успеха. Сообщение обязано называть python3 явно — открытие
    самого факта зависимости, не только про то, что что-то не найдено."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=False)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode != 0, "без python3 не должно быть кода успеха"
    combined = result.stdout + result.stderr
    assert "STUB claude" not in combined, (
        "молча запустил Claude Code вместо честной ошибки:\n" + combined
    )
    assert "STUB codex" not in combined
    assert "python3" in combined.lower(), (
        "сообщение обязано явно называть python3, а не просто \"не найдено\":\n" + combined
    )


def test_healthy_path_still_launches_the_requested_engine(tmp_path):
    """Бэкстоп: находки выше не должны были сломать штатный путь — с живым
    python3 и рабочим помощником --codex по-прежнему запускает codex."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB codex" in result.stdout


# ── мостик окружения для Codex ───────────────────────────────────────────
#
# Находка живой приёмки: под Codex шесть коннекторов из девяти стартовали
# без переменных. У Codex нет ${VAR}-подстановки в config.toml — только
# `env_vars = ["ИМЯ"]`, «переслать под тем же именем» (docs/codex-facts.md,
# раздел 4), а аналитик держит в secrets.env наши имена (JIRA_TOKEN), тогда
# как процессы коннекторов ждут свои (JIRA_PERSONAL_TOKEN). Значит целевые
# имена обязан выставить тот, кто запускает codex, — то есть bin/uzum.
#
# Проверяется это снимком окружения РЕАЛЬНО ЗАПУЩЕННОЙ заглушки движка
# (`env` в файл), а не возвратом функции: заглушка не знает, каких имён от
# неё ждут, и сбрасывает всё подряд. Значения намеренно со спецсимволами —
# пробел и `$`: они проходят через `source secrets.env`, python и execvpe, и
# любое лишнее раскрытие в шелле было бы видно.

SECRETS_WITH_JIRA = (
    "CH_USER='t'\n"
    "CH_WMS_HOST='wms.internal'\n"
    "CH_WMS_USER='u'\n"
    "CH_WMS_PASSWORD='пароль с пробелом'\n"
    "JIRA_TOKEN='токен-9f3a с пробелом и $знаком'\n"
    "CONFLUENCE_TOKEN='конф-токен-7c1d'\n"
    "GRAFANA_TOKEN='граф-токен'\n"
)
JIRA_TOKEN_VALUE = "токен-9f3a с пробелом и $знаком"
CONFLUENCE_TOKEN_VALUE = "конф-токен-7c1d"


def _engine_env(dump_dir, engine):
    """Окружение процесса-движка из снимка `env`. Разбираем по первому «=»:
    это сырые строки чужого процесса, а не подготовленный тестом словарь."""
    text = (dump_dir / ("%s.env" % engine)).read_text(encoding="utf-8")
    env = {}
    for line in text.splitlines():
        if "=" in line:
            name, value = line.split("=", 1)
            env[name] = value
    return env


# Обе формы запуска: без аргументов (интерактивная сессия) и с аргументами
# (любой неинтерактивный запуск — им же сдавалась приёмка). Находка ревью:
# проверки окружения жили только на первой форме, и мутация «убрать мостик»
# во второй ветке bin/uzum проходила зелёной.
BOTH_LAUNCH_FORMS = pytest.mark.parametrize(
    "extra_args", [[], ["exec", "перечисли инструменты"]],
    ids=["no-args", "with-args"])


@BOTH_LAUNCH_FORMS
def test_codex_gets_the_variables_under_the_names_connectors_actually_expect(tmp_path, extra_args):
    """Главный тест находки: JIRA_TOKEN аналитика обязан доехать до Codex
    ещё и под именами JIRA_PERSONAL_TOKEN/CONFLUENCE_PERSONAL_TOKEN, иначе
    uvx mcp-atlassian стартует без токена и не отдаёт ни одного
    инструмента."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    result = _run_uzum(repo, home, path, args=["--codex", *extra_args], dump_dir=dump_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB codex" in result.stdout
    env = _engine_env(dump_dir, "codex")
    assert env.get("JIRA_PERSONAL_TOKEN") == JIRA_TOKEN_VALUE, (
        "JIRA_PERSONAL_TOKEN = %r" % env.get("JIRA_PERSONAL_TOKEN"))
    # Токен Confluence — свой, не JIRA_TOKEN: PAT в Server/DC действует
    # только в том продукте, где создан (токен Jira на Confluence — 401,
    # проверено живым запросом 14.08.2026).
    assert env.get("CONFLUENCE_PERSONAL_TOKEN") == CONFLUENCE_TOKEN_VALUE
    assert env.get("GRAFANA_SERVICE_ACCOUNT_TOKEN") == "граф-токен"
    # Исходные имена никуда не делись — их читает, например, trino_proxy.py
    # из secrets.env, и ломать это не входило в задачу.
    assert env.get("JIRA_TOKEN") == JIRA_TOKEN_VALUE


@BOTH_LAUNCH_FORMS
def test_codex_gets_the_defaults_for_addresses_nobody_filled_in(tmp_path, extra_args):
    """Адреса Jira/Confluence/Trino в secrets.env не лежат: раньше их
    подставлял `${JIRA_URL:-https://jira.uzum.com}` в .mcp.json, и под Codex
    подставить их было некому."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    _run_uzum(repo, home, path, args=["--codex", *extra_args], dump_dir=dump_dir)

    env = _engine_env(dump_dir, "codex")
    assert env.get("JIRA_URL") == "https://jira.uzum.com"
    assert env.get("CONFLUENCE_URL") == "https://confluence.uzum.com"
    assert env.get("TRINO_HOST") == "trino.prod-data.internal.daymarket.uz"
    assert env.get("TRINO_CATALOG") == "dwh-iceberg"


def test_codex_is_still_launched_with_our_profile_and_the_users_arguments(tmp_path):
    """Мостик не должен ничего поменять в самом запуске: тот же `-p uzum`,
    те же аргументы человека — и значения по-прежнему не в командной строке
    (в `ps` их быть не должно, как и раньше)."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    _run_uzum(repo, home, path, args=["--codex", "экспорт отчёта"], dump_dir=dump_dir)

    argv = (dump_dir / "codex.argv").read_text(encoding="utf-8").splitlines()
    assert argv == ["-p", "uzum", "экспорт отчёта"], argv
    assert JIRA_TOKEN_VALUE not in " ".join(argv)


@BOTH_LAUNCH_FORMS
def test_claude_code_environment_is_left_exactly_as_it_was(tmp_path, extra_args):
    """Граница задачи: у Claude Code подстановка своя и работает
    (`${JIRA_TOKEN}` внутри .mcp.json), переименовывать ему ничего не надо.
    Целевые имена в его окружении означали бы, что мостик применился не там,
    где надо."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    result = _run_uzum(repo, home, path, args=["--claude", *extra_args], dump_dir=dump_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    env = _engine_env(dump_dir, "claude")
    assert env.get("JIRA_TOKEN") == JIRA_TOKEN_VALUE, "секреты перестали доезжать"
    for name in ("JIRA_PERSONAL_TOKEN", "CONFLUENCE_PERSONAL_TOKEN",
                 "GRAFANA_SERVICE_ACCOUNT_TOKEN", "JIRA_URL", "TRINO_HOST"):
        assert name not in env, (
            "%s появился в окружении Claude Code — поведение изменилось там, "
            "где меняться не должно" % name)


def test_missing_bridge_file_stops_the_launch_instead_of_starting_codex_blind(tmp_path):
    """Защитное условие в bin/uzum, проверенное само по себе: без файла
    мостика запуск обязан оборваться с объяснением. Тихий `exec codex` тут
    дал бы ровно ту сессию с шестью мёртвыми коннекторами, ради которой всё
    это и делалось."""
    repo = _make_repo(tmp_path, broken_helper=False)
    (repo / "connectors" / "codex_env_bridge.py").unlink()
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode != 0, "молча запустился без мостика"
    combined = result.stdout + result.stderr
    assert "STUB codex" not in combined, combined
    assert "codex_env_bridge.py" in combined


def test_healthy_path_without_flag_launches_the_only_configured_engine(tmp_path):
    """Тот же бэкстоп для пути "единственный настроенный движок" — только
    claude в PATH, без флага."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    (engine_bin / "codex").unlink()
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=[])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB claude" in result.stdout


def test_machine_user_is_passed_through_and_an_explicit_one_survives(tmp_path):
    """Колонка `user` в телеметрии берётся из UZUM_USER. Здесь стояла
    подстановка `${CH_USER:-$USER}`, а переменной CH_USER не существует с
    тех пор, как ClickHouse развели на два кластера — она молча уходила в
    запасной вариант, и никто не замечал, потому что $USER выглядит
    правдоподобно.

    Два факта разом: без явного значения приезжает учётка машины, а
    заданное снаружи значение переживает запуск (прежняя строка его
    затирала — это второй дефект в той же строке)."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin, secrets=SECRETS_WITH_JIRA)
    path = _curated_path([engine_bin], include_python=True)
    dump_dir = tmp_path / "dump"
    dump_dir.mkdir()

    _run_uzum(repo, home, path, args=["--claude"], dump_dir=dump_dir)
    env = _engine_env(dump_dir, "claude")
    assert env.get("UZUM_USER") == "test", (
        "в UZUM_USER не учётка машины: %r" % env.get("UZUM_USER"))

    env_with_override = {"HOME": str(home), "PATH": path, "USER": "test",
                         "TERM": "xterm", "UZUM_TEST_DUMP_DIR": str(dump_dir),
                         "UZUM_USER": "явно-заданный"}
    subprocess.run(["bash", str(repo / "bin" / "uzum"), "--claude"],
                   env=env_with_override, cwd=str(repo), input="\n",
                   capture_output=True, text=True, timeout=30)
    env2 = _engine_env(dump_dir, "claude")
    assert env2.get("UZUM_USER") == "явно-заданный", (
        "заданный снаружи UZUM_USER затёрт: %r" % env2.get("UZUM_USER"))
