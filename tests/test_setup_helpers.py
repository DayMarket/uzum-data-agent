import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

import setup_helpers


def test_writes_env_file_with_permissions(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis-platon"})
    # Значения пишутся в одинарных кавычках: файл `source`-ит bin/uzum,
    # см. tests/test_envfile.py.
    assert path.read_text(encoding="utf-8").strip() == "CH_USER='denis-platon'"
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_merges_without_losing_existing_keys(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis"})
    setup_helpers.write_env(str(path), {"JIRA_TOKEN": "test-token-xxx"})
    content = path.read_text(encoding="utf-8")
    assert "CH_USER='denis'" in content
    assert "JIRA_TOKEN='test-token-xxx'" in content


def test_overwrites_existing_key(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "old"})
    setup_helpers.write_env(str(path), {"CH_USER": "new"})
    assert "CH_USER='new'" in path.read_text(encoding="utf-8")
    assert "CH_USER='old'" not in path.read_text(encoding="utf-8")


def test_enables_only_configured_servers(tmp_path):
    path = tmp_path / "settings.local.json"
    setup_helpers.write_enabled_servers(str(path), ["clickhouse", "atlassian"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["enabledMcpjsonServers"] == ["atlassian", "clickhouse"]


def test_keeps_other_local_settings(tmp_path):
    path = tmp_path / "settings.local.json"
    path.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    setup_helpers.write_enabled_servers(str(path), ["trino"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["Bash(ls)"]
    assert data["enabledMcpjsonServers"] == ["trino"]


def test_read_enabled_servers_round_trips_what_the_wizard_wrote(tmp_path):
    """Список включённых коннекторов — один на оба движка: Claude Code берёт
    его отсюда сам, генератор конфига Codex — через эту функцию."""
    path = tmp_path / "settings.local.json"
    setup_helpers.write_enabled_servers(str(path), ["trino", "superset"])
    assert setup_helpers.read_enabled_servers(str(path)) == ["superset", "trino"]


def test_read_enabled_servers_distinguishes_no_choice_from_empty_choice(tmp_path):
    """None («выбор не сделан») и [] («выбрано ничего») — разные состояния, и
    генератор конфига Codex ведёт себя в них по-разному: в первом случае
    пишет все коннекторы, во втором — ни одного."""
    missing = tmp_path / "нет-такого.json"
    assert setup_helpers.read_enabled_servers(str(missing)) is None

    empty_choice = tmp_path / "empty.json"
    empty_choice.write_text(json.dumps({"enabledMcpjsonServers": []}), encoding="utf-8")
    assert setup_helpers.read_enabled_servers(str(empty_choice)) == []

    no_key = tmp_path / "no_key.json"
    no_key.write_text(json.dumps({"permissions": {}}), encoding="utf-8")
    assert setup_helpers.read_enabled_servers(str(no_key)) is None

    broken = tmp_path / "broken.json"
    broken.write_text("{не json", encoding="utf-8")
    assert setup_helpers.read_enabled_servers(str(broken)) is None


def test_creates_secrets_directory_with_closed_permissions(tmp_path):
    """Находка ревью задачи 9: файл секретов был 600, но сам каталог
    (~/.config/uzum-ai) создавался с правами по умолчанию — любой процесс
    того же пользователя мог видеть список файлов внутри. Каталог должен
    закрываться на 700, а не только файл."""
    secrets_dir = tmp_path / "uzum-ai"
    path = secrets_dir / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis"})
    assert oct(secrets_dir.stat().st_mode)[-3:] == "700"


def test_tightens_permissions_of_preexisting_loose_directory(tmp_path):
    """Тот же каталог мог быть создан старой версией скрипта (до фикса) с
    правами по умолчанию — write_env должен подтянуть его до 700 и на уже
    существующем каталоге, не только на только что созданном."""
    secrets_dir = tmp_path / "uzum-ai"
    secrets_dir.mkdir(mode=0o755)
    path = secrets_dir / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis"})
    assert oct(secrets_dir.stat().st_mode)[-3:] == "700"


# ── Задача Codex-6: движки, доставка конфига Codex, хуки Codex ─────────────

def test_detect_engines_finds_only_whats_in_path():
    """which — параметр ради теста: не бьёт по реальному PATH машины, где
    гоняются тесты (там может стоять или не стоять ни один из движков)."""
    def fake_which_both(name):
        return "/usr/local/bin/%s" % name if name in ("claude", "codex") else None
    assert setup_helpers.detect_engines(fake_which_both) == ["claude", "codex"]

    def fake_which_only_codex(name):
        return "/usr/local/bin/codex" if name == "codex" else None
    assert setup_helpers.detect_engines(fake_which_only_codex) == ["codex"]

    def fake_which_none(name):
        return None
    assert setup_helpers.detect_engines(fake_which_none) == []


def test_engines_setup_plan_blocks_when_neither_engine_found():
    """Ни один движок не найден — блокирующая ошибка с командами установки
    ОБОИХ (не одного на выбор), чтобы аналитик не гадал, что ставить."""
    to_configure, blocking_error = setup_helpers.engines_setup_plan([])
    assert to_configure == []
    assert blocking_error is not None
    assert "claude.com/code" in blocking_error
    assert "@openai/codex" in blocking_error


def test_engines_setup_plan_configures_the_single_installed_engine():
    to_configure, blocking_error = setup_helpers.engines_setup_plan(["codex"])
    assert to_configure == ["codex"]
    assert blocking_error is None


def test_engines_setup_plan_configures_both_when_both_installed():
    to_configure, blocking_error = setup_helpers.engines_setup_plan(["claude", "codex"])
    assert to_configure == ["claude", "codex"]
    assert blocking_error is None


# ── Выбор движка для bin/uzum ───────────────────────────────────────────────

def test_select_engine_explicit_argument_wins_over_everything():
    engine, reason = setup_helpers.select_engine(
        available=["claude", "codex"], requested="codex", remembered="claude")
    assert engine == "codex"
    assert reason == "requested"


def test_select_engine_explicit_argument_for_engine_not_installed_is_an_error():
    """Явно попросили движок, которого на этой машине нет — понятная
    ошибка, а не тихий откат на другой движок (аналитик решит бы, что
    Codex настроен, хотя на самом деле сессия ушла в Claude Code)."""
    engine, reason = setup_helpers.select_engine(
        available=["claude"], requested="codex", remembered=None)
    assert engine is None
    assert reason == "engine_not_available:codex"


def test_select_engine_falls_back_to_remembered_choice():
    engine, reason = setup_helpers.select_engine(
        available=["claude", "codex"], requested=None, remembered="codex")
    assert engine == "codex"
    assert reason == "remembered"


def test_select_engine_ignores_stale_remembered_choice_no_longer_installed():
    """Запомненный движок мог быть удалён с машины со времени прошлого
    запуска — не падаем на нём молча, идём дальше по правилам выбора."""
    engine, reason = setup_helpers.select_engine(
        available=["claude"], requested=None, remembered="codex")
    assert engine == "claude"
    assert reason == "only_configured"


def test_select_engine_defaults_to_the_only_configured_engine():
    engine, reason = setup_helpers.select_engine(
        available=["codex"], requested=None, remembered=None)
    assert engine == "codex"
    assert reason == "only_configured"


def test_select_engine_is_ambiguous_when_both_available_and_nothing_decided():
    """Оба движка настроены, явного выбора и запомненного решения нет —
    не решаем молча за человека: bin/uzum должен спросить."""
    engine, reason = setup_helpers.select_engine(
        available=["claude", "codex"], requested=None, remembered=None)
    assert engine is None
    assert reason == "ambiguous"


def test_select_engine_none_available_is_reported_as_such():
    engine, reason = setup_helpers.select_engine(available=[], requested=None, remembered=None)
    assert engine is None
    assert reason == "none_available"


# ── Находка ревью №2/№4: когда bin/uzum вправе ПЕРЕЗАПИСАТЬ запомненный
# выбор движка. Единственная реализация этой развилки — здесь, bin/uzum
# сверяется с этой же функцией (через python3 -c), а не переигрывает
# условие заново в bash: именно расхождение кода и комментария в отдельной
# bash-копии этой логики и было находкой ревью.

def test_should_remember_engine_choice_only_right_after_asking_interactively():
    assert setup_helpers.should_remember_engine_choice("ambiguous") is True


def test_should_remember_engine_choice_is_false_for_every_other_reason():
    for reason in (
        "requested", "remembered", "only_configured",
        "none_available", "engine_not_available:codex", "engine_not_available:claude",
    ):
        assert setup_helpers.should_remember_engine_choice(reason) is False, reason


def test_remembered_engine_round_trips_through_a_file(tmp_path):
    path = str(tmp_path / "state" / "engine")
    assert setup_helpers.read_remembered_engine(path) is None  # файла ещё нет
    setup_helpers.write_remembered_engine(path, "codex")
    assert setup_helpers.read_remembered_engine(path) == "codex"
    setup_helpers.write_remembered_engine(path, "claude")
    assert setup_helpers.read_remembered_engine(path) == "claude"


# ── Доставка конфига Codex: профиль $CODEX_HOME/uzum.config.toml ───────────
#
# Решение (см. отчёт задачи, факт 1 из брифа): НЕ сливать в
# $CODEX_HOME/config.toml и НЕ переключать $CODEX_HOME на папку
# репозитория. Вместо этого — штатный, задокументированный в `codex --help`
# механизм профиля (`-p/--profile <имя>`: "Layer $CODEX_HOME/<name>.config.toml
# on top of the base user config"), проверенный живым запуском (см. отчёт):
# профиль применяется целиком, даже когда базового config.toml нет вовсе
# или он занят чужим содержимым (доверие другим проектам, настройки другого
# инструмента) — и это содержимое остаётся нетронутым.

def test_deploy_codex_profile_writes_named_profile_file_not_base_config(tmp_path):
    codex_home = tmp_path / "codex_home"
    generated = tmp_path / "generated_config.toml"
    generated.write_text('default_permissions = "uzum"\n', encoding="utf-8")

    changed, backed_up_to = setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    assert changed is True
    assert backed_up_to is None  # ничего чужого не было — нечего беречь
    profile_path = codex_home / ("%s.config.toml" % setup_helpers.CODEX_PROFILE_NAME)
    written = profile_path.read_text(encoding="utf-8")
    assert written.startswith(setup_helpers.CODEX_PROFILE_MARKER)
    assert 'default_permissions = "uzum"' in written
    # Базового config.toml мастер не создаёт и не трогает вообще.
    assert not (codex_home / "config.toml").exists()


def test_deploy_codex_profile_does_not_touch_preexisting_base_config(tmp_path):
    """Находка отчёта: на реальной машине $CODEX_HOME/config.toml уже может
    быть занят — доверием к другим проектам, настройками другого
    инструмента. Мастер обязан оставить его как есть."""
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    base_config = codex_home / "config.toml"
    base_config.write_text(
        '[projects."/Users/someone/other-project"]\ntrust_level = "trusted"\n',
        encoding="utf-8",
    )
    generated = tmp_path / "generated_config.toml"
    generated.write_text('default_permissions = "uzum"\n', encoding="utf-8")

    setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    assert base_config.read_text(encoding="utf-8") == (
        '[projects."/Users/someone/other-project"]\ntrust_level = "trusted"\n'
    )


def test_deploy_codex_profile_is_idempotent_when_content_unchanged(tmp_path):
    codex_home = tmp_path / "codex_home"
    generated = tmp_path / "generated_config.toml"
    generated.write_text('default_permissions = "uzum"\n', encoding="utf-8")

    first, _ = setup_helpers.deploy_codex_profile(str(generated), str(codex_home))
    second, _ = setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    assert first is True
    assert second is False  # содержимое не изменилось — второй раз не пишем


def test_deploy_codex_profile_updates_when_registry_changes(tmp_path):
    codex_home = tmp_path / "codex_home"
    generated = tmp_path / "generated_config.toml"
    generated.write_text('default_permissions = "uzum"\n', encoding="utf-8")
    setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    generated.write_text('default_permissions = "uzum"\n[mcp_servers.trino]\n', encoding="utf-8")
    changed, backed_up_to = setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    assert changed is True
    assert backed_up_to is None  # это наш же файл (есть маркер) — не чужой, беречь нечего
    profile_path = codex_home / ("%s.config.toml" % setup_helpers.CODEX_PROFILE_NAME)
    assert "mcp_servers.trino" in profile_path.read_text(encoding="utf-8")


# ── Находка ревью №5: чужой файл с тем же именем профиля не затирается молча ──
#
# Файл хуков (deploy_codex_hooks) сливается бережно, потому что hooks.json —
# структурированный JSON, слияние по ключам осмысленно. config.toml профиля —
# непрозрачный текстовый блоб (мы же его и генерируем целиком) — "слияние"
# для него не имеет смысла, но молча ЗАТЕРЕТЬ чужой файл с тем же именем
# профиля («uzum» — не невозможное совпадение) тоже нельзя: асимметрия с
# hooks.json была необоснованной. Наш файл маркируется комментарием в первой
# строке; если на диске уже есть файл с этим именем БЕЗ нашего маркера — это
# не наш файл, и вместо тихой перезаписи он переименовывается в сторону
# (backed_up_to), а не молча исчезает.

def test_deploy_codex_profile_backs_up_foreign_file_with_the_same_profile_name(tmp_path):
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    profile_path = codex_home / ("%s.config.toml" % setup_helpers.CODEX_PROFILE_NAME)
    foreign_content = "# чей-то ручной профиль, не наш\nmodel = \"o3\"\n"
    profile_path.write_text(foreign_content, encoding="utf-8")
    generated = tmp_path / "generated_config.toml"
    generated.write_text('default_permissions = "uzum"\n', encoding="utf-8")

    changed, backed_up_to = setup_helpers.deploy_codex_profile(str(generated), str(codex_home))

    assert changed is True
    assert backed_up_to is not None
    # Чужое содержимое не пропало — лежит рядом, под другим именем, целиком.
    from pathlib import Path
    assert Path(backed_up_to).read_text(encoding="utf-8") == foreign_content
    # А по прежнему пути теперь наш файл, с маркером.
    assert profile_path.read_text(encoding="utf-8").startswith(setup_helpers.CODEX_PROFILE_MARKER)


# ── Хуки Codex: слияние с $CODEX_HOME/hooks.json, не перезапись ────────────
#
# hooks.json — единственный, не профиль-специфичный файл на весь
# $CODEX_HOME (в отличие от config.toml, у hooks.json нет механизма
# layering по имени профиля — проверено: `codex --help` не упоминает такой
# возможности для hooks). Он может быть уже занят другим инструментом —
# так и оказалось на машине автора задачи (сторонний notify-хук). Слепая
# перезапись стёрла бы чужие хуки.

def _codex_hook_commands(defs):
    return [hook["command"] for groups in defs.values()
            for group in groups for hook in group["hooks"]]


def test_codex_hook_definitions_cover_the_engine_portable_events():
    """SessionStart/SessionEnd/UserPromptSubmit/PostToolUse — те же
    события, что уже пишет Claude Code, и НЕТ PostToolUseFailure: у Codex
    такого события не существует (docs/codex-facts.md, раздел 3) —
    PostToolUse там срабатывает и на успехе, и на сбое."""
    defs = setup_helpers.codex_hook_definitions()
    assert set(defs) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse"}
    for command in _codex_hook_commands(defs):
        assert ".claude/hooks/" in command


# ── Дефект: относительный путь в hooks.json ломал Codex в чужих проектах ──
#
# hooks.json — общий на весь $CODEX_HOME, то есть на ВСЕ проекты аналитика.
# Пока путь в команде был относительным, в постороннем каталоге файла по
# нему не оказывалось — и это не тишина, а ненулевой код возврата python3.
# Живой запуск (docs/codex-facts.md, раздел 11):
#     hook: SessionStart Failed
#     hook: UserPromptSubmit Blocked
# «Blocked» означает, что промпт не доходит до модели вообще: установка
# нашего инструмента ломала аналитику Codex во всех остальных его проектах.
# Файл теперь всегда на месте (абсолютный путь), а «не наша сессия»
# решается внутри самих скриптов (lib/hook_scope.py, tests/test_hook_scope.py).

def test_codex_hook_commands_point_at_this_clone_by_absolute_path(tmp_path):
    root = str(tmp_path / "uzum-data-agent")
    for command in _codex_hook_commands(setup_helpers.codex_hook_definitions(root)):
        assert os.path.join(root, ".claude", "hooks") in command
        # Относительного пути не осталось нигде: в чужом проекте команда
        # обязана найти файл и завершиться нулём, а не упасть.
        assert " .claude/hooks/" not in command


def test_codex_hook_commands_quote_a_path_with_spaces(tmp_path):
    """Путь к клону аналитика может содержать пробел (`~/My Projects/...`),
    а команда хука исполняется шеллом — без кавычек она распалась бы на два
    аргумента и хук падал бы уже у нас, в нашем же репозитории."""
    root = str(tmp_path / "My Projects" / "uzum-data-agent")
    for command in _codex_hook_commands(setup_helpers.codex_hook_definitions(root)):
        # shlex.split разбирает команду так же, как это сделает шелл: путь
        # обязан остаться ОДНИМ аргументом, а не распасться на два.
        script = {a for a in shlex.split(command) if a.endswith((".py", ".sh"))}
        assert len(script) == 1, command
        path = script.pop()
        assert path == os.path.join(root, ".claude", "hooks",
                                    os.path.basename(path)), command


def test_codex_hook_definitions_default_to_our_own_clone():
    """По умолчанию — корень того клона, откуда setup.sh подключил lib."""
    expected = str(Path(setup_helpers.__file__).resolve().parent.parent)
    for command in _codex_hook_commands(setup_helpers.codex_hook_definitions()):
        assert expected in command


# ── Находка повторного ревью: абсолютный путь на исчезнувший клон ─────────
#
# Абсолютный путь гарантирует, что файл будет найден, только пока папка
# репозитория на месте. Аналитик её переносит, переименовывает или удаляет —
# $CODEX_HOME/hooks.json не меняется (его пишет только setup.sh, bin/uzum
# хуки не передеплоивает), и мы возвращаемся к тому же `UserPromptSubmit
# Blocked`, только через другой вход и уже во ВСЕХ проектах разом, включая
# сам переехавший репозиторий — то есть починить изнутри инструмента нельзя.
# Поэтому команда обёрнута в `test -f … && exec … || exit 0`.

def test_codex_hook_command_exits_zero_when_the_clone_is_gone(tmp_path):
    """Запускаем команду ровно так, как её запускает Codex — через шелл."""
    gone = str(tmp_path / "переехавший клон")
    for command in _codex_hook_commands(setup_helpers.codex_hook_definitions(gone)):
        result = subprocess.run(["sh", "-c", command], input="{}",
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, command
        assert result.stdout == "", command
        assert result.stderr == "", command


def test_codex_hook_command_still_runs_the_script_that_is_in_place(tmp_path):
    """Обратная сторона: пока файл на месте, обёртка обязана его запустить, а
    не съесть. Скрипт-двойник печатает метку — её и ищем."""
    root = tmp_path / "clone"
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    for script in ("log_session.py", "log_event.py", "on_session_start.sh"):
        (hooks / script).write_text("print('запущен')\n" if script.endswith(".py")
                                    else "echo запущен\n", encoding="utf-8")

    for command in _codex_hook_commands(setup_helpers.codex_hook_definitions(str(root))):
        result = subprocess.run(["sh", "-c", command], input="{}",
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, command
        assert result.stdout.strip() == "запущен", command


def test_codex_session_start_updates_the_repository_too_not_only_telemetry():
    """Находка ревью (Important): раньше сессия Codex не делала `git pull`
    вообще — on_session_start.sh регистрировался только для Claude Code, а
    замены не было. Аналитик в Codex работал бы на скиллах из дня
    установки и никак бы об этом не узнал. Проверяем возможность, а не имя
    файла: среди команд SessionStart должна быть та, что запускает
    обновление репозитория, и именно в режиме обычного текста (--plain) —
    Claude-формат hookSpecificOutput для Codex не проверен."""
    groups = setup_helpers.codex_hook_definitions()["SessionStart"]
    commands = [hook["command"] for group in groups for hook in group["hooks"]]
    updater = [c for c in commands if "on_session_start.sh" in c]
    assert updater, commands
    assert all("--plain" in c for c in updater), updater


def test_codex_hook_definitions_keep_one_script_per_entry():
    """По одному скрипту на запись: так каждая видна и в hooks.json, и в
    диалоге доверия по отдельности, а не слипается в одну строку."""
    for groups in setup_helpers.codex_hook_definitions().values():
        for group in groups:
            assert len(group["hooks"]) == 1, group


# ── Апгрейд уже установленной машины: старая форма записи вытесняется ─────
#
# Самая дорогая находка третьего круга ревью. merge_codex_hooks только
# ДОБАВЛЯЛ записи, сравнивая их по значению целиком, — а значение меняется
# вместе с текстом команды. У аналитика, поставившего инструмент раньше,
# после ./setup.sh в hooks.json оставались обе записи разом, и живой Codex
# в постороннем каталоге давал:
#
#     hook: UserPromptSubmit
#     hook: UserPromptSubmit
#     hook: UserPromptSubmit Blocked
#     hook: UserPromptSubmit Completed
#
# То есть починка не доезжала ровно до тех, ради кого дефект и заводили, а в
# нашем собственном репозитории каждое событие обрабатывалось дважды.
#
# Все прежние проверки идемпотентности сливали codex_hook_definitions() сам с
# собой — то есть проверяли случай, в котором дефекта быть не может. Ниже
# проверяется именно апгрейд: СТАРАЯ форма записи против новой.

# Корень клона, который «ставится» в тестах ниже, и дословно те команды,
# которые писали прошлые версии codex_hook_definitions() для этого корня.
#
# Все три формы видны в `git log` по codex_hook_definitions: относительный
# путь (4b81b8c … 6764f04 — это форма установленной базы), абсолютный путь
# (f2761fc), абсолютный путь в обёртке `test -f` (7a59c6f, 90c2905).
#
# Строки выписаны здесь руками, а НЕ взяты из setup_helpers.
# legacy_hook_commands(): иначе тест сверял бы перечень сам с собой и молчал
# бы о любой ошибке в нём. Что это ровно тот текст, который печатал код тех
# коммитов, проверено отдельно (git archive каждого коммита + печать команд;
# дословный вывод — в отчёте по задаче).
LEGACY_ROOT = "/opt/uzum-data-agent"
LEGACY_RELATIVE = "python3 .claude/hooks/log_event.py"
LEGACY_ABSOLUTE = "python3 /opt/uzum-data-agent/.claude/hooks/log_event.py"
LEGACY_COMMANDS = (
    # 4b81b8c … 6764f04
    "python3 .claude/hooks/log_event.py",
    "python3 .claude/hooks/log_session.py",
    "bash .claude/hooks/on_session_start.sh --plain",
    # f2761fc
    "python3 /opt/uzum-data-agent/.claude/hooks/log_event.py",
    "python3 /opt/uzum-data-agent/.claude/hooks/log_session.py",
    "bash /opt/uzum-data-agent/.claude/hooks/on_session_start.sh --plain",
    # 7a59c6f, 90c2905
    "test -f /opt/uzum-data-agent/.claude/hooks/log_event.py && exec python3"
    " /opt/uzum-data-agent/.claude/hooks/log_event.py || exit 0",
    "test -f /opt/uzum-data-agent/.claude/hooks/log_session.py && exec python3"
    " /opt/uzum-data-agent/.claude/hooks/log_session.py || exit 0",
    "test -f /opt/uzum-data-agent/.claude/hooks/on_session_start.sh && exec bash"
    " /opt/uzum-data-agent/.claude/hooks/on_session_start.sh --plain || exit 0",
)


def _commands(merged, event):
    return [hook["command"] for group in merged["hooks"][event]
            for hook in group["hooks"]]


def _merge_installing(installed, root=LEGACY_ROOT):
    """Слияние ровно так, как его делает deploy_codex_hooks при ./setup.sh."""
    return setup_helpers.merge_codex_hooks(
        installed, setup_helpers.codex_hook_definitions(root), root)


@pytest.mark.parametrize("legacy", LEGACY_COMMANDS)
def test_merge_evicts_our_own_entry_of_an_older_form(legacy):
    installed = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": legacy}]}]}}

    merged = _merge_installing(installed)

    commands = _commands(merged, "UserPromptSubmit")
    assert legacy not in commands, commands
    assert len(commands) == 1, commands


@pytest.mark.parametrize("legacy", [LEGACY_RELATIVE, LEGACY_ABSOLUTE])
def test_merge_evicts_ours_but_keeps_the_foreign_entry_next_to_it(legacy):
    """Вытеснение обязано быть прицельным: чужой хук на том же событии —
    не наш и остаётся нетронутым."""
    foreign = {"type": "command", "command": "some-other-tool-notify.sh"}
    installed = {"hooks": {"UserPromptSubmit": [
        {"hooks": [foreign]},
        {"hooks": [{"type": "command", "command": legacy}]},
    ]}}

    merged = _merge_installing(installed)

    commands = _commands(merged, "UserPromptSubmit")
    assert commands[0] == "some-other-tool-notify.sh", commands
    assert legacy not in commands, commands
    assert len(commands) == 2, commands


# ── Находка: предикат съедал чужой инструмент, и это не проверялось ────────
#
# Опознание шло по имени скрипта плюс подстроке ".claude/hooks/" в команде.
# Оба признака родовые: `.claude/hooks/` — штатный каталог Claude Code, а
# `log_event.py` — предельно общее имя, мы сами его и выбрали. Чужой
# инструмент, следующий той же конвенции, молча удалялся из общего
# hooks.json. Мутация «убрать условие .claude/hooks/» не роняла ни одного
# теста из 68: на вход никогда не подавали чужую запись с именем нашего
# скрипта. Теперь подают.

FOREIGN_LOOKALIKES = (
    # Ровно тот случай, который вскрыл ревьюер: чужой инструмент в своём
    # клоне, та же конвенция каталога, то же родовое имя скрипта.
    "python3 /Users/analyst/other-tool/.claude/hooks/log_event.py",
    "test -f ~/other-tool/.claude/hooks/log_session.py && exec python3"
    " ~/other-tool/.claude/hooks/log_session.py || exit 0",
    "bash /Users/analyst/other-tool/.claude/hooks/on_session_start.sh --plain",
    # Соседний инструмент с этой машины — он тоже помечает свои записи.
    'SUPERSET_AGENT_ID=codex "/Users/analyst/.superset/hooks/notify.sh"',
    "python3 /home/a/mytool/hooks/log_event.py",
    "grep -r log_event.py ~/notes",
    # Прежняя наша форма ЦЕЛИКОМ лежит внутри чужой команды — но команда
    # всё равно чужая: совпадать обязана вся строка, а не её кусок.
    "sh -c 'cd ~/other-tool && python3 .claude/hooks/log_event.py'",
)


@pytest.mark.parametrize("foreign", FOREIGN_LOOKALIKES)
def test_merge_keeps_a_foreign_hook_that_only_looks_like_ours(foreign):
    """Чужая команда с именем нашего скрипта и штатным каталогом Claude Code
    — всё ещё чужая. Из общего файла её удалять нельзя."""
    installed = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": foreign}]}]}}

    merged = _merge_installing(installed)

    commands = _commands(merged, "UserPromptSubmit")
    assert commands[0] == foreign, commands
    assert len(commands) == 2, commands  # чужая на месте, наша добавлена рядом


# ── Находка: совпадение искалось в хуке, а удалялась запись целиком ────────
#
# is_our_codex_hook отвечал True, если совпал ЛЮБОЙ хук внутри записи, а
# фильтр выбрасывал запись целиком — вместе с чужими командами, лежащими в
# ней рядом. Записей больше чем с одним хуком в тестах не было вообще.

def test_merge_takes_out_our_hook_and_leaves_the_foreign_one_in_the_same_entry():
    foreign_first = {"type": "command", "command": "чужой-инструмент-notify.sh"}
    foreign_last = {"type": "command", "command": "чужой-инструмент-audit.sh"}
    installed = {"hooks": {"UserPromptSubmit": [{
        "matcher": "*",  # чужие поля записи тоже не наши, чтобы их терять
        "hooks": [foreign_first,
                  {"type": "command", "command": LEGACY_RELATIVE},
                  foreign_last],
    }]}}

    merged = _merge_installing(installed)

    groups = merged["hooks"]["UserPromptSubmit"]
    # Чужая запись цела: оба чужих хука, в прежнем порядке, с прежними полями.
    assert groups[0]["hooks"] == [foreign_first, foreign_last], groups[0]
    assert groups[0]["matcher"] == "*", groups[0]
    # Наш прежний хук ушёл, новый добавлен ОТДЕЛЬНОЙ записью.
    commands = _commands(merged, "UserPromptSubmit")
    assert LEGACY_RELATIVE not in commands, commands
    assert len([c for c in commands if "log_event.py" in c]) == 1, commands


def test_merge_drops_an_entry_that_had_nothing_but_our_hook_in_it():
    """Обратная сторона: запись, где кроме нашего хука ничего не было, должна
    исчезнуть целиком, а не остаться пустой оболочкой."""
    installed = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": LEGACY_RELATIVE}]},
        {"hooks": [{"type": "command", "command": "чужой-инструмент-notify.sh"}]},
    ]}}

    merged = _merge_installing(installed)

    groups = merged["hooks"]["UserPromptSubmit"]
    assert all(group["hooks"] for group in groups), groups
    assert len(groups) == 2, groups  # чужая + наша новая


def test_merge_keeps_an_entry_whose_shape_we_do_not_understand():
    """hooks.json — общий файл, и его формат не наш: чужой инструмент (или
    будущая версия Codex) может положить туда запись, которую мы не умеем
    разбирать. Не опознали как своё — не трогаем. Иначе «не разобрали»
    означало бы «удалили»."""
    odd = [
        {"hooks": "чужой-инструмент-notify.sh"},        # не список
        {"matcher": "Bash"},                             # хуков нет вовсе
        "чужой-инструмент-notify.sh",                    # вообще не запись
        {"hooks": ["чужой-инструмент-notify.sh", 42]},   # хуки не словари
    ]
    installed = {"hooks": {"UserPromptSubmit": list(odd)}}

    merged = _merge_installing(installed)

    assert merged["hooks"]["UserPromptSubmit"][:len(odd)] == odd, merged["hooks"]


def test_every_command_we_write_carries_our_marker():
    """Метка — единственное, по чему наши будущие записи опознаются в общем
    файле. Потерять её при следующей смене команды нельзя: без неё вытеснение
    снова начнёт угадывать по родовым признакам."""
    for command in _codex_hook_commands(
            setup_helpers.codex_hook_definitions(LEGACY_ROOT)):
        assert setup_helpers.OUR_HOOK_MARKER in command, command
    for foreign in FOREIGN_LOOKALIKES:
        assert setup_helpers.OUR_HOOK_MARKER not in foreign, foreign


def test_marker_identifies_our_entry_whatever_the_rest_of_the_command_is():
    """Метка не зависит ни от пути, ни от формы команды: запись, оставшуюся от
    клона по ДРУГОМУ пути (аналитик перенёс папку и переставил инструмент),
    вытесняем по одной метке — перечень прежних форм тут уже не помогает."""
    moved = ("test -f '/старый путь/uzum-data-agent/.claude/hooks/log_event.py'"
             " && exec env %s=1 python3"
             " '/старый путь/uzum-data-agent/.claude/hooks/log_event.py'"
             " || exit 0" % setup_helpers.OUR_HOOK_MARKER)
    installed = {"hooks": {"UserPromptSubmit": [
        {"hooks": [{"type": "command", "command": moved}]}]}}

    merged = _merge_installing(installed)

    commands = _commands(merged, "UserPromptSubmit")
    assert moved not in commands, commands
    assert len(commands) == 1, commands


def test_merge_is_idempotent_across_a_change_of_the_command():
    """Повторный ./setup.sh после смены формы команды не должен ни удваивать
    записи, ни менять файл во второй раз."""
    old = setup_helpers.merge_codex_hooks(
        {}, setup_helpers.codex_hook_definitions("/старый путь/uzum-data-agent"))
    new_defs = setup_helpers.codex_hook_definitions("/новый путь/uzum-data-agent")

    once = setup_helpers.merge_codex_hooks(old, new_defs)
    twice = setup_helpers.merge_codex_hooks(once, new_defs)

    assert once == twice
    assert once["hooks"] == {event: list(groups)
                             for event, groups in new_defs.items()}


def test_merge_drops_an_event_we_no_longer_register():
    """Если мы перестанем регистрировать событие, наша запись о нём должна
    уйти из файла, а не остаться висеть навсегда. `Stop` мы не регистрируем —
    значит запись на нём, помеченная как наша, лишняя."""
    ours = setup_helpers.codex_hook_definitions(LEGACY_ROOT)["PostToolUse"][0]
    installed = {"hooks": {"Stop": [ours]}}

    merged = _merge_installing(installed)

    assert "Stop" not in merged["hooks"], merged["hooks"]


def test_deploy_codex_hooks_upgrades_an_older_installation_on_disk(tmp_path):
    """То же самое, но через настоящий deploy_codex_hooks и файл на диске —
    путь, которым это и происходит у аналитика при ./setup.sh."""
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(json.dumps({"hooks": {
        "UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "чужой-notify.sh"}]},
            {"hooks": [{"type": "command", "command": LEGACY_RELATIVE}]},
        ],
    }}), encoding="utf-8")

    assert setup_helpers.deploy_codex_hooks(str(codex_home), str(tmp_path / "clone")) is True

    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    commands = [hook["command"] for group in data["hooks"]["UserPromptSubmit"]
                for hook in group["hooks"]]
    assert LEGACY_RELATIVE not in commands, commands
    assert "чужой-notify.sh" in commands, commands
    assert len([c for c in commands if "log_event.py" in c]) == 1, commands


def test_merge_codex_hooks_preserves_foreign_hooks_untouched():
    foreign = {
        "hooks": {
            "SessionStart": [
                {"hooks": [{"type": "command", "command": "some-other-tool-notify.sh"}]}
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": "some-other-tool-notify.sh"}]}
            ],
        }
    }
    merged = setup_helpers.merge_codex_hooks(foreign, setup_helpers.codex_hook_definitions())

    # Чужой SessionStart-хук на месте, наш добавлен рядом, не вместо.
    session_start_commands = [h["hooks"][0]["command"] for h in merged["hooks"]["SessionStart"]]
    assert "some-other-tool-notify.sh" in session_start_commands
    assert any("log_session.py" in c for c in session_start_commands)
    # Событие, которое мы вообще не трогаем (Stop), осталось как было.
    assert merged["hooks"]["Stop"] == foreign["hooks"]["Stop"]


def test_merge_codex_hooks_is_idempotent_no_duplicate_entries():
    defs = setup_helpers.codex_hook_definitions()
    once = setup_helpers.merge_codex_hooks({}, defs)
    twice = setup_helpers.merge_codex_hooks(once, defs)
    assert twice == once
    for event, event_hooks in twice["hooks"].items():
        assert len(event_hooks) == len(defs[event]), event


def test_merge_codex_hooks_adds_only_the_new_entry_to_an_older_installation():
    """Повторный ./setup.sh на машине, где hooks.json остался от прошлой
    версии (без хука обновления репозитория), обязан ДОБАВИТЬ недостающую
    запись, а не удвоить уже лежащую там телеметрию."""
    defs = setup_helpers.codex_hook_definitions()
    older = {"hooks": {"SessionStart": [defs["SessionStart"][0]]}}
    merged = setup_helpers.merge_codex_hooks(older, defs)
    assert merged["hooks"]["SessionStart"] == defs["SessionStart"]


def test_deploy_codex_hooks_merges_with_file_already_on_disk(tmp_path):
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(json.dumps({
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "foreign.sh"}]}]}
    }), encoding="utf-8")

    changed = setup_helpers.deploy_codex_hooks(str(codex_home))

    assert changed is True
    data = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert data["hooks"]["Stop"] == [{"hooks": [{"type": "command", "command": "foreign.sh"}]}]
    assert "PostToolUse" in data["hooks"]


def test_deploy_codex_hooks_creates_file_when_none_exists(tmp_path):
    codex_home = tmp_path / "codex_home"
    changed = setup_helpers.deploy_codex_hooks(str(codex_home))
    assert changed is True
    data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    assert set(data["hooks"]) == {"SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse"}


def test_deploy_codex_hooks_is_idempotent_on_repeated_calls(tmp_path):
    codex_home = tmp_path / "codex_home"
    setup_helpers.deploy_codex_hooks(str(codex_home))
    changed_again = setup_helpers.deploy_codex_hooks(str(codex_home))
    assert changed_again is False


def test_deploy_codex_hooks_tolerates_corrupt_existing_file(tmp_path):
    """Битый hooks.json (не наш формат, не наша забота чинить) не должен
    ронять установку — честнее переписать его нашими хуками, чем упасть."""
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    (codex_home / "hooks.json").write_text("{not valid json", encoding="utf-8")
    changed = setup_helpers.deploy_codex_hooks(str(codex_home))
    assert changed is True
    data = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    assert "PostToolUse" in data["hooks"]


def test_codex_home_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "custom-codex-home"))
    assert setup_helpers.codex_home() == str(tmp_path / "custom-codex-home")


def test_codex_home_defaults_to_dot_codex_under_home(monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert setup_helpers.codex_home() == os.path.expanduser("~/.codex")


# ── Готовность коннектора: «переменная есть» ≠ «в переменной что-то есть» ──
#
# Находка ревью: условие `.strip()` в connector_readiness не проверялось
# ничем — замена его на `name not in values` оставляла весь набор зелёным.
# А путь реальный: write_env пишет пустое значение как KEY='' (проверено
# ниже), и такой файл сделал бы «готовыми» все девять коннекторов разом,
# молча отменив весь гейт.

def _readiness(secrets_path):
    return dict(setup_helpers.connector_readiness(str(secrets_path)))


def test_empty_value_is_not_a_value(tmp_path):
    """Переменная в файле есть, значения в ней нет. Именно так write_env
    сохраняет пустой ответ, и именно так выглядит `.env`, заполненный
    наполовину."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"OMD_URL": "", "OMD_TOKEN": ""})

    # Предпосылка, а не украшение: если бы файл вообще не содержал этих
    # имён, тест проверял бы совсем другой случай — «ключа нет».
    assert "OMD_URL=''" in path.read_text(encoding="utf-8")

    assert _readiness(path)["openmetadata"] == ("OMD_URL", "OMD_TOKEN")
    assert "openmetadata" not in setup_helpers.configured_connector_ids(str(path))


def test_whitespace_only_value_is_not_a_value(tmp_path):
    """Пробел или табуляция — это опечатка при заполнении файла руками, а не
    адрес и не токен. Коннектор с таким «значением» не поднимется."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"GRAFANA_URL": "   ", "GRAFANA_TOKEN": "\t"})

    # Адрес получил дефолт в реестре, поэтому пробелы в нём — не нехватка:
    # подставится дефолт. А вот пробел вместо токена остаётся нехваткой,
    # иначе коннектор поднялся бы без доступа.
    assert _readiness(path)["grafana"] == ("GRAFANA_TOKEN",)
    assert "grafana" not in setup_helpers.configured_connector_ids(str(path))


def test_a_filled_value_really_makes_the_connector_ready(tmp_path):
    """Положительный контроль. Без него все проверки выше прошли бы и на
    сломанной функции, которая всегда отвечает «ничего не готово»."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"OMD_URL": "https://omd.internal",
                                        "OMD_TOKEN": "токен"})

    assert _readiness(path)["openmetadata"] == ()
    assert "openmetadata" in setup_helpers.configured_connector_ids(str(path))


def test_secrets_file_made_of_empty_values_enables_nothing_but_trino(tmp_path):
    """Тот самый путь целиком: `.env.example` поставляется с двумя десятками
    пустых переменных, и если они окажутся в secrets.env (write_env кладёт
    их как KEY=''), гейт обязан остаться закрытым для всех восьми
    коннекторов, которым нужны креды. Девятый — trino, он ходит по SSO и
    кредов не требует вовсе.

    Имена берутся из настоящего `.env.example`, а не переписаны сюда: файл
    меняется вместе с реестром, и тест обязан меняться вместе с ним."""
    example = (Path(setup_helpers.__file__).resolve().parent.parent
               / ".env.example").read_text(encoding="utf-8")
    names = [line.split("=", 1)[0].strip()
             for line in example.splitlines()
             if "=" in line and not line.strip().startswith("#")]
    assert len(names) >= 10, "в .env.example не осталось переменных — тест ослеп"

    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {name: "" for name in names})

    assert setup_helpers.configured_connector_ids(str(path)) == ["trino"], (
        "пустышки из .env.example приняты за настроенные доступы")
