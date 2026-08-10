"""Тесты на сам мастер установки — `setup.sh`, запущенный целиком.

До сих пор setup.sh проверялся только через свои python-хелперы
(lib/setup_helpers.py). Три находки живой приёмки из четырёх лежали ровно в
той части, которую не проверял никто: что мастер СПРАШИВАЕТ, что он
ГОВОРИТ в конце и что он ДОСТАВЛЯЕТ после `--add`. Поэтому здесь он
запускается как процесс, в изолированной копии репозитория, с подставными
внешними командами — и проверяется по тому, что он напечатал и что записал
на диск.

Подставные команды не решают за проверяемый код: `curl` отвечает кодами,
которые задаёт тест (как настоящий сервер — доступом или отказом), `uv`
печатает результат живой проверки Superset и заодно записывает, что ему
передали. Ни одна из них не «подтверждает ожидаемое»: все утверждения тестов
— про вывод самого setup.sh и про файлы, которые он написал.

Сети тут нет, живого Codex и живого Superset тоже — для них есть отдельные
живые проверки (tests/test_codex_permissions.py и отчёт задачи).

Секретов в файле нет — только выдуманные значения.
"""
import json
import os
import shutil
import stat
import subprocess
from collections import namedtuple
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_Result = namedtuple("_Result", "returncode stdout stderr")

# Копируем не всё подряд: .git и кэши большие и не нужны, а
# .claude/settings.local.json и .env — это состояние ЭТОЙ машины, из-за
# которого тест бы читал чужой список включённых коннекторов.
_SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "work", "tests", ".codex"}


def _make_repo_copy(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT, repo,
        ignore=lambda src, names: [n for n in names if n in _SKIP_DIRS],
    )
    for stale in (repo / ".claude" / "settings.local.json", repo / ".env"):
        if stale.exists():
            stale.unlink()
    (repo / "work").mkdir(exist_ok=True)
    return repo


def _script(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _stubs(tmp_path, engines=("claude",)):
    """Подставные внешние команды. `curl` и `uv` — содержательные (см.
    докстринг модуля), остальные нужны только чтобы `command -v` их нашёл."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()

    _script(stub_dir / "curl", """#!/usr/bin/env python3
# Подставной curl: код ответа и тело берутся из правил теста по подстроке
# URL. Аргументы разбираются так же, как их передаёт curl_check в setup.sh.
import json, os, sys
argv = sys.argv[1:]
url = argv[-1]
out = argv[argv.index("-o") + 1] if "-o" in argv else None
code, body = "000", ""
for pattern, rule_code, rule_body in json.load(open(os.environ["UZUM_TEST_CURL_RULES"])):
    if pattern in url:
        code, body = str(rule_code), rule_body
        break
with open(os.environ["UZUM_TEST_CURL_LOG"], "a") as log:
    log.write(url + "\\n")
if out:
    open(out, "w").write(body)
sys.stdout.write(code)
""")

    _script(stub_dir / "uv", """#!/usr/bin/env python3
# Подставной uv: единственное, ради чего мастер его зовёт — живая проверка
# Superset (`uv run connectors/superset_mcp.py --check`). Записывает, ЧТО
# ему передали (аргументы и SUPERSET_*-окружение), и печатает заданный
# тестом результат.
import json, os, sys
with open(os.environ["UZUM_TEST_UV_LOG"], "a") as log:
    log.write(json.dumps({
        "argv": sys.argv[1:],
        "superset_env": {k: v for k, v in os.environ.items() if k.startswith("SUPERSET_")},
    }) + "\\n")
answer = os.environ.get("UZUM_TEST_SUPERSET_CHECK", "OK:7")
print(answer)
sys.exit(0 if answer.startswith("OK:") else 1)
""")

    for name in ("uvx", "npx"):
        _script(stub_dir / name, "#!/usr/bin/env bash\nexit 0\n")
    for name in engines:
        # Движок записывает факт своего запуска в лог: живая проверка
        # доверия хукам уводит его вывод в файл, поэтому «печатал или нет»
        # тут ничего не доказывает, а «запускался или нет» — доказывает.
        _script(stub_dir / name,
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"$UZUM_TEST_ENGINE_LOG\"\n"
                "echo \"STUB " + name + " $*\"\n")
    return stub_dir


def _run_setup(repo, tmp_path, args=(), curl_rules=(), dotenv=None,
               superset_check="OK:7", engines=("claude",), answers=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if "codex" in engines:
        # Живая проверка доверия хукам первым делом смотрит на auth.json и
        # без него не запускает codex вовсе — тогда тесты про «запускался
        # или нет» ничего бы не проверяли.
        (home / ".codex").mkdir(exist_ok=True)
        (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    stub_dir = _stubs(tmp_path, engines=engines)
    rules_path = tmp_path / "curl-rules.json"
    rules_path.write_text(json.dumps(list(curl_rules)), encoding="utf-8")
    if dotenv is not None:
        (repo / ".env").write_text(dotenv, encoding="utf-8")

    env = {
        "HOME": str(home),
        "PATH": "%s:%s" % (stub_dir, os.environ["PATH"]),
        "USER": "test",
        "TERM": "dumb",
        "UZUM_TEST_CURL_RULES": str(rules_path),
        "UZUM_TEST_CURL_LOG": str(tmp_path / "curl.log"),
        "UZUM_TEST_UV_LOG": str(tmp_path / "uv.log"),
        "UZUM_TEST_ENGINE_LOG": str(tmp_path / "engine.log"),
        "UZUM_TEST_SUPERSET_CHECK": superset_check,
    }
    # Вывод — в файлы, а не в трубу. Сторожевой таймер живой проверки
    # доверия хукам (`( sleep 90; kill … ) &` в check_codex_hook_trust)
    # переживает свой субшелл и держит унаследованный дескриптор открытым:
    # при stdout-трубе subprocess ждал бы EOF все 90 секунд после того, как
    # сам мастер давно завершился. Человека в терминале это не касается
    # (там stdout — не труба), но тест из-за этого шёл три минуты.
    out_path, err_path = tmp_path / "setup.out", tmp_path / "setup.err"
    with open(out_path, "w", encoding="utf-8") as out, \
            open(err_path, "w", encoding="utf-8") as err:
        completed = subprocess.run(
            ["bash", str(repo / "setup.sh"), *args],
            env=env, cwd=str(repo), text=True, stdout=out, stderr=err,
            input="\n" * 40 if answers is None else answers,
            timeout=180,
        )
    result = _Result(
        completed.returncode,
        out_path.read_text(encoding="utf-8"),
        err_path.read_text(encoding="utf-8"),
    )
    return result, home


def _secrets(home):
    path = home / ".config" / "uzum-ai" / "secrets.env"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _enabled(repo):
    path = repo / ".claude" / "settings.local.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("enabledMcpjsonServers", [])


WMS_OK = ["wms.internal", 200, "12"]
JIRA_REJECTED = ["jira.uzum.com", 401, "nope"]
JIRA_OK = ["jira.uzum.com", 200, '{"displayName": "Аналитик"}']

DOTENV_WMS_AND_JIRA = (
    "CH_WMS_HOST=wms.internal\n"
    "CH_WMS_PORT=8123\n"
    "CH_WMS_USER=имя-фамилия\n"
    "CH_WMS_PASSWORD=пароль-склада\n"
    "JIRA_TOKEN=токен-jira\n"
)


# ── Находка 2: итог мастера ──────────────────────────────────────────────

def test_full_run_lists_what_did_not_connect_with_a_command_for_each(tmp_path):
    """Раньше в конце было только число включённых. Установка, где половина
    доступов не доехала, выглядела так же, как полная."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_REJECTED],
                           dotenv=DOTENV_WMS_AND_JIRA)

    out = result.stdout
    assert "Не подключено" in out, out[-3000:]
    for server in ("atlassian", "grafana", "openmetadata", "growthbook", "sheets",
                   "clickhouse-dwh", "superset"):
        assert "./setup.sh --add %s" % server in out, (
            "в итоге нет команды для %s:\n%s" % (server, out[-3000:]))
    # То, что реально включилось, в список «не подключено» попасть не должно.
    tail = out.split("Не подключено")[1]
    assert "--add clickhouse-wms" not in tail
    assert "--add trino" not in tail


def test_missing_required_access_is_the_very_last_thing_the_wizard_says(tmp_path):
    """Требование брифа дословно: про обязательное — прямо и последней
    строкой, а не в середине вывода между восемью другими коннекторами."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_REJECTED],
                           dotenv=DOTENV_WMS_AND_JIRA)

    out = result.stdout.rstrip()
    assert "Установка неполна" in out, out[-3000:]
    last_block = out[out.index("Установка неполна"):]
    assert "Jira" in last_block
    assert "./setup.sh --add atlassian" in last_block
    # Ничего после этого блока быть не должно — иначе он снова теряется.
    assert "Готово." not in last_block
    assert "ВАЖНО" not in last_block
    assert out.endswith(
        "Остальным можно пользоваться уже сейчас — это добавляется отдельно, "
        "когда доступ будет."), out[-500:]


def test_nothing_about_incomplete_install_when_both_required_are_in(tmp_path):
    """Обратная сторона: страж не должен пугать того, у кого всё на месте.
    Без этого теста «Установка неполна» могла бы печататься всегда."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
                           dotenv=DOTENV_WMS_AND_JIRA)

    assert "Установка неполна" not in result.stdout, result.stdout[-3000:]
    assert "clickhouse-wms" in _enabled(repo)
    assert "atlassian" in _enabled(repo)
    # Остальные всё так же перечислены — это другая, не блокирующая часть.
    assert "./setup.sh --add grafana" in result.stdout


def test_empty_token_is_not_reported_as_a_rejected_one(tmp_path):
    """Enter на вопросе о токене — это «не ввёл», а не «токен не подошёл».
    Раньше пустой Bearer уходил в Jira, та отвечала 401, и человек шёл
    перевыпускать нормальный токен."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--add", "atlassian"],
                           curl_rules=[JIRA_REJECTED], answers="\n\n")

    out = result.stdout
    assert "токен не введён" in out, out
    assert "токен не принят" not in out, out
    # И запроса быть не должно: проверять нечего.
    curl_log = tmp_path / "curl.log"
    assert not curl_log.exists() or "jira" not in curl_log.read_text(encoding="utf-8")


# ── Находка 3: --add доставляет конфиг Codex ─────────────────────────────

def test_add_delivers_the_codex_profile_where_codex_actually_reads_it(tmp_path):
    """`./setup.sh --add <коннектор>` — это команда, которую инструмент сам
    советует при отвалившемся доступе. Раньше она обновляла
    .codex/config.toml в папке репозитория, но не копию в
    $CODEX_HOME/uzum.config.toml — единственную, которую Codex читает. Под
    Codex `--add` не давал ничего до следующего полного прогона мастера."""
    repo = _make_repo_copy(tmp_path)

    result, home = _run_setup(repo, tmp_path, args=["--add", "growthbook"],
                              engines=("claude", "codex"), answers="ключ-gb\n\n")

    profile = home / ".codex" / "uzum.config.toml"
    assert profile.exists(), (
        "профиль не доставлен в $CODEX_HOME после --add:\n" + result.stdout[-3000:])
    text = profile.read_text(encoding="utf-8")
    assert "[mcp_servers.growthbook]" in text, (
        "коннектор, который только что включили, не попал в конфиг, который "
        "читает Codex:\n" + text)
    assert "growthbook" in _enabled(repo)


def test_add_does_not_spend_a_model_call_on_the_hook_trust_probe(tmp_path):
    """Довод, ради которого условие и стояло: `--add` не должен каждый раз
    дёргать живой `codex exec` — это настоящий запрос к модели. Доставка от
    него отделена, а проверка доверия осталась там, где была."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--add", "growthbook"],
               engines=("claude", "codex"), answers="ключ-gb\n\n")

    engine_log = tmp_path / "engine.log"
    assert not engine_log.exists(), (
        "движок запускался при --add: %s" % engine_log.read_text(encoding="utf-8"))


def test_full_run_still_probes_hook_trust_with_a_real_codex_run(tmp_path):
    """Бэкстоп к предыдущему: разделение не должно было отменить живую
    проверку там, где она и была. Факт — что codex реально запускался, а не
    что в выводе встретились нужные слова (они есть и в справке в конце)."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
                           dotenv=DOTENV_WMS_AND_JIRA, engines=("claude", "codex"))

    engine_log = tmp_path / "engine.log"
    assert engine_log.exists(), result.stdout[-3000:]
    assert "exec" in engine_log.read_text(encoding="utf-8")
    assert "доверие хукам Codex" in result.stdout, result.stdout[-3000:]


def test_add_codex_hooks_still_probes_hook_trust(tmp_path):
    """Отдельная цель `--add codex-hooks` существует ровно ради этой живой
    проверки — она не должна была уехать вместе с общим `--add`."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--add", "codex-hooks"],
               engines=("claude", "codex"), answers="\n\n")

    engine_log = tmp_path / "engine.log"
    assert engine_log.exists(), "живая проверка доверия не запускалась"
    assert "exec" in engine_log.read_text(encoding="utf-8")


# ── Находка 4: Superset спрашивает креды и проверяет их ──────────────────

def test_superset_asks_for_credentials_and_stores_them(tmp_path):
    """Мастер писал «кредов не нужно, вход через SSO в браузере» — и брать
    их было неоткуда: ни вопроса, ни строки в шаблоне, ни переменной в
    реестре. Коннектор при этом числился включённым."""
    repo = _make_repo_copy(tmp_path)

    result, home = _run_setup(repo, tmp_path, args=["--add", "superset"],
                              answers="\nлогин-аналитика\nпароль-superset\n\n")

    out = result.stdout
    assert "вижу 7 дашбордов" in out, out
    secrets = _secrets(home)
    assert "SUPERSET_USERNAME" in secrets, secrets
    assert "SUPERSET_PASSWORD" in secrets, secrets
    assert "логин-аналитика" in secrets
    assert "superset" in _enabled(repo)


def test_wizard_fills_exactly_the_superset_variables_the_registry_declares(tmp_path):
    """Связка «спросили» ↔ «объявлено»: мастер обязан записать под теми
    именами, которые реестр объявляет источниками для superset, — иначе
    коннектор получит `${SUPERSET_PASSWORD}` из пустоты, как и было до
    находки. Ожидаемый набор записан литералами: пропажа переменной из
    реестра — тоже поломка, а не «ну значит и спрашивать нечего»."""
    from connectors.registry import CONNECTORS_BY_ID

    sources = {item.source for item in CONNECTORS_BY_ID["superset"].env_vars()}
    assert sources == {"SUPERSET_URL", "SUPERSET_USERNAME", "SUPERSET_PASSWORD"}

    repo = _make_repo_copy(tmp_path)
    _, home = _run_setup(repo, tmp_path, args=["--add", "superset"],
                         answers="\nлогин-аналитика\nпароль-superset\n\n")

    secrets = _secrets(home)
    for name in sources:
        assert ("\n%s=" % name) in "\n" + secrets, (
            "%s объявлен в реестре, но мастер его не записал:\n%s" % (name, secrets))


def test_superset_credentials_reach_the_check_through_the_environment(tmp_path):
    """Проверка должна быть настоящей (введённые логин и пароль реально
    уходят в тот код, который потом ходит в Superset) — и при этом значения
    не должны попадать в командную строку: в `ps` их видно любому процессу
    того же пользователя."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--add", "superset"],
               answers="\nлогин-аналитика\nпароль-superset\n\n")

    calls = [json.loads(line)
             for line in (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()]
    assert calls, "живая проверка Superset не запускалась вовсе"
    call = calls[-1]
    assert call["argv"][0] == "run" and call["argv"][-1] == "--check", call["argv"]
    assert call["superset_env"]["SUPERSET_USERNAME"] == "логин-аналитика"
    assert call["superset_env"]["SUPERSET_PASSWORD"] == "пароль-superset"
    assert call["superset_env"]["SUPERSET_COOKIE_FILE"], (
        "проверка пошла с обычным cookie-файлом — уцелевшая сессия сказала бы "
        "«доступ есть» на любом пароле")
    assert not any("пароль-superset" in arg for arg in call["argv"]), call["argv"]


def test_superset_is_not_enabled_when_the_login_does_not_go_through(tmp_path):
    """Отказ входа — это не «включено»: иначе всё возвращается к тому, с
    чего начали (коннектор в списке, работать не может)."""
    repo = _make_repo_copy(tmp_path)

    result, home = _run_setup(repo, tmp_path, args=["--add", "superset"],
                              superset_check="ERROR:Keycloak: Invalid username or password",
                              answers="\nлогин\nне-тот-пароль\n\n")

    assert "вход не прошёл" in result.stdout, result.stdout
    assert "Invalid username or password" in result.stdout
    assert "superset" not in _enabled(repo)
    assert "SUPERSET_PASSWORD" not in _secrets(home)


def test_superset_without_credentials_is_skipped_not_silently_enabled(tmp_path):
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--add", "superset"], answers="\n\n\n")

    assert "не введён" in result.stdout, result.stdout
    assert "./setup.sh --add superset" in result.stdout
    assert "superset" not in _enabled(repo)
