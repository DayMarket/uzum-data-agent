import json
import os

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
        # Путь до скрипта относительный, не абсолютный: hooks.json общий на
        # весь $CODEX_HOME, и в чужих проектах наш хук просто не найдётся.
        assert ".claude/hooks/" in command
        assert " /" not in command


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
    """Идемпотентность merge_codex_hooks держится на сравнении записей по
    значению. Если сложить два скрипта в одну запись, то добавление третьего
    в будущем изменит значение целиком — старая запись перестанет совпадать
    и телеметрия начнёт писаться дважды из одной сессии."""
    for groups in setup_helpers.codex_hook_definitions().values():
        for group in groups:
            assert len(group["hooks"]) == 1, group


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
    assert any(c.startswith("python3 .claude/hooks/") for c in session_start_commands)
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
