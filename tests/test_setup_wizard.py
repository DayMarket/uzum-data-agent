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
    stub_dir.mkdir(exist_ok=True)

    _script(stub_dir / "curl", """#!/usr/bin/env python3
# Подставной curl: код ответа и тело берутся из правил теста по подстроке
# URL. Аргументы разбираются так же, как их передаёт curl_check в setup.sh.
# Заголовки (в них логин и пароль) приходят конфигом на стандартный вход —
# читаем и записываем в лог вместе с URL: без этого нельзя проверить, ЧЕМ
# мастер стучался, а «той ли учёткой» — это ровно то, что тут ломалось.
import json, os, sys
argv = sys.argv[1:]
url = argv[-1]
out = argv[argv.index("-o") + 1] if "-o" in argv else None
config = sys.stdin.read() if "--config" in argv else ""
code, body = "000", ""
for pattern, rule_code, rule_body in json.load(open(os.environ["UZUM_TEST_CURL_RULES"])):
    if pattern in url:
        code, body = str(rule_code), rule_body
        break
with open(os.environ["UZUM_TEST_CURL_LOG"], "a") as log:
    log.write(json.dumps({"url": url, "config": config}, ensure_ascii=False) + "\\n")
if out:
    open(out, "w").write(body)
sys.stdout.write(code)
""")

    _script(stub_dir / "uv", """#!/usr/bin/env python3
# Подставной uv. Мастер зовёт его для двух разных дел, и стаб их различает
# так же, как настоящий uv: `uv sync --script <файл>` и `uv run <файл>` —
# прогрев окружений коннекторов; `uv run …superset_mcp.py --check` — живая
# проверка Superset. Записывает всё, что ему передали.
import json, os, sys
argv = sys.argv[1:]
with open(os.environ["UZUM_TEST_UV_LOG"], "a") as log:
    log.write(json.dumps({
        "argv": argv,
        "superset_env": {k: v for k, v in os.environ.items() if k.startswith("SUPERSET_")},
    }) + "\\n")
if argv[:1] == ["sync"]:
    sys.exit(0)
if "--check" not in argv:
    sys.exit(0)   # прогрев: скрипт «завершился на EOF»
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
               superset_check="OK:7", engines=("claude",), answers=None,
               stub_overrides=None, extra_env=None, saved_secrets=None):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if "codex" in engines:
        # Живая проверка доверия хукам первым делом смотрит на auth.json и
        # без него не запускает codex вовсе — тогда тесты про «запускался
        # или нет» ничего бы не проверяли.
        (home / ".codex").mkdir(exist_ok=True)
        (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    stub_dir = _stubs(tmp_path, engines=engines)
    for name, body in (stub_overrides or {}).items():
        _script(stub_dir / name, body)
    rules_path = tmp_path / "curl-rules.json"
    rules_path.write_text(json.dumps(list(curl_rules)), encoding="utf-8")
    if dotenv is not None:
        (repo / ".env").write_text(dotenv, encoding="utf-8")
    if saved_secrets is not None:
        # Машина, где установка уже проходила: доступы лежат в secrets.env.
        (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)
        (home / ".config" / "uzum-ai" / "secrets.env").write_text(
            saved_secrets, encoding="utf-8")

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
    env.update(extra_env or {})
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
    # Не «последний вызов uv»: тем же uv мастер ещё и прогревает окружения
    # коннекторов, и последним оказывается прогрев, а не проверка.
    checks = [c for c in calls if "--check" in c["argv"]]
    assert checks, "живая проверка Superset не запускалась вовсе"
    call = checks[-1]
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


# ── Телеметрия: доступ тот же, что у складского ClickHouse ───────────────

TELEMETRY_OK = ["sandbox.ai_usage_sessions", 200, "128"]


def _curl_calls(tmp_path):
    """Что мастер реально спрашивал у сервера: URL и заголовки (в них логин
    и пароль). Заголовки нужны, чтобы проверять не только «сходил», но и
    «той ли учёткой» — а это и есть предмет находки."""
    path = tmp_path / "curl.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_telemetry_asks_nothing_and_checks_the_warehouse_access(tmp_path):
    """Решение владельца: телеметрия пишется в складской ClickHouse теми же
    логином и паролем — значит спрашивать нечего. Живая проверка при этом
    обязана остаться: это единственное место, где человек узнаёт, что
    телеметрия работает."""
    repo = _make_repo_copy(tmp_path)

    result, home = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                              curl_rules=[TELEMETRY_OK], dotenv=DOTENV_WMS_AND_JIRA)

    out = result.stdout
    assert "sandbox.ai_usage_sessions на месте, строк: 128" in out, out
    for asked in ("Хост телеметрии", "  Логин:", "  Пароль:"):
        assert asked not in out, "мастер снова спрашивает про телеметрию:\n%s" % out
    # И ничего лишнего в secrets.env: дубль складских значений — ловушка,
    # он перебивает CH_WMS_* и после смены пароля тихо остановит запись.
    assert "TELEMETRY_CH_" not in _secrets(home), _secrets(home)


def test_telemetry_check_goes_with_the_warehouse_credentials(tmp_path):
    """Мутация «телеметрия берёт не те креды» обязана быть видна: смотрим,
    какие именно логин и пароль ушли в проверку."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--add", "telemetry"],
               curl_rules=[TELEMETRY_OK], dotenv=DOTENV_WMS_AND_JIRA)

    calls = [c for c in _curl_calls(tmp_path) if "ai_usage_sessions" in c["url"]]
    assert calls, "живой проверки телеметрии не было вовсе"
    assert "wms.internal:8123" in calls[0]["url"], calls[0]["url"]
    assert "X-ClickHouse-User: имя-фамилия" in calls[0]["config"], calls[0]["config"]
    assert "X-ClickHouse-Key: пароль-склада" in calls[0]["config"]


def test_telemetry_without_the_warehouse_says_so_instead_of_asking(tmp_path):
    """Склад ещё не настроен — писать некуда. Это не ошибка установки и не
    повод задавать вопросы: доступ появится, телеметрия включится сама."""
    repo = _make_repo_copy(tmp_path)

    result, home = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                              curl_rules=[TELEMETRY_OK])

    assert "складской ClickHouse ещё не настроен" in result.stdout, result.stdout
    assert "TELEMETRY_CH_" not in _secrets(home)
    assert not [c for c in _curl_calls(tmp_path) if "ai_usage_sessions" in c["url"]]


def test_wizard_removes_telemetry_duplicates_of_the_warehouse_values(tmp_path):
    """Машина, установленная до этой правки: в secrets.env лежат
    TELEMETRY_CH_*, дословно повторяющие CH_WMS_*. Они перебивают складские
    значения — после смены пароля склада телеметрия молча перестала бы
    писаться. Мастер обязан их убрать."""
    repo = _make_repo_copy(tmp_path)
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text(
        "CH_WMS_HOST='wms.internal'\n"
        "CH_WMS_PORT='8123'\n"
        "CH_WMS_USER='имя-фамилия'\n"
        "CH_WMS_PASSWORD='пароль-склада'\n"
        "TELEMETRY_CH_HOST='wms.internal'\n"
        "TELEMETRY_CH_PORT='8123'\n"
        "TELEMETRY_CH_USER='имя-фамилия'\n"
        "TELEMETRY_CH_PASSWORD='пароль-склада'\n",
        encoding="utf-8")

    _run_setup(repo, tmp_path, args=["--add", "telemetry"], curl_rules=[TELEMETRY_OK])

    secrets = _secrets(home)
    assert "TELEMETRY_CH_" not in secrets, secrets
    assert "CH_WMS_PASSWORD='пароль-склада'" in secrets, "снесли лишнее:\n%s" % secrets


def test_wizard_keeps_a_deliberate_writer_account_and_checks_it(tmp_path):
    """Исключение, ради которого механизм оставлен: у пилотной группы может
    не быть прав INSERT, и запись пойдёт отдельной учёткой-писателем.
    Заданное руками значение — не дубль: его нельзя ни стереть, ни
    проигнорировать при проверке."""
    repo = _make_repo_copy(tmp_path)
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text(
        "CH_WMS_HOST='wms.internal'\n"
        "CH_WMS_PORT='8123'\n"
        "CH_WMS_USER='имя-фамилия'\n"
        "CH_WMS_PASSWORD='пароль-склада'\n"
        "TELEMETRY_CH_USER='ai-usage-writer'\n"
        "TELEMETRY_CH_PASSWORD='пароль-писателя'\n",
        encoding="utf-8")

    result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                           curl_rules=[TELEMETRY_OK])

    secrets = _secrets(home)
    assert "TELEMETRY_CH_USER='ai-usage-writer'" in secrets, secrets
    calls = [c for c in _curl_calls(tmp_path) if "ai_usage_sessions" in c["url"]]
    assert calls, "живой проверки телеметрии не было"
    assert "X-ClickHouse-User: ai-usage-writer" in calls[0]["config"], (
        "проверили складскую учётку, а писать будет другая — это и есть "
        "«зелёная установка, пустая таблица»")
    # Адрес при этом остаётся складским: ради смены учётки не должно
    # приходиться переписывать все четыре значения.
    assert "wms.internal:8123" in calls[0]["url"]
    assert "заданы отдельно" in result.stdout


# ── Первая сессия не должна молчать: окружения коннекторов готовит мастер ──

def test_wizard_prepares_the_environment_of_every_local_connector(tmp_path):
    """Замер: `uv run connectors/trino_proxy.py` на чистой машине — 17,7 с,
    Codex столько старта MCP-сервера не ждёт и просто не показывает
    коннектор, молча. Значит окружение должно быть собрано на установке, где
    человек и так ждёт.

    Список файлов берётся из реестра, а не из ожиданий теста: если в
    registry.py появится ещё один локальный коннектор, а прогрев про него не
    узнает — тест упадёт."""
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from connectors.registry import CONNECTORS, ProjectScript

    expected = []
    for connector in CONNECTORS:
        branches = [connector.args]
        if connector.codex is not None:
            branches.append(connector.codex.args)
        for args in branches:
            for arg in args:
                if isinstance(arg, ProjectScript) and arg.path not in expected:
                    expected.append(arg.path)
    assert expected, "в реестре не осталось локальных коннекторов — тест потерял смысл"

    repo = _make_repo_copy(tmp_path)
    _run_setup(repo, tmp_path, args=["--add", "growthbook"], answers="ключ-gb\n\n")

    calls = [json.loads(line) for line in
             (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()]
    synced = {c["argv"][2] for c in calls if c["argv"][:2] == ["sync", "--script"]}
    ran = {c["argv"][1] for c in calls if c["argv"][:1] == ["run"] and len(c["argv"]) > 1}

    for path in expected:
        assert path in synced, "зависимости %s не готовятся на установке" % path
        assert path in ran, (
            "окружение %s не собирается: после одного лишь sync первый запуск "
            "всё равно занимал 12 секунд" % path)


def test_warmup_failure_is_reported_not_swallowed(tmp_path):
    """Сети нет — зависимости не доехали. Это не повод останавливать
    установку, но и молчать нельзя: именно так выглядит «первая сессия без
    коннекторов», ради которой всё это и делается."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--add", "growthbook"],
                           answers="ключ-gb\n\n",
                           stub_overrides={"uv": "#!/usr/bin/env bash\nexit 1\n"})

    assert "зависимости не скачались" in result.stdout, result.stdout[-2000:]
    assert result.returncode == 0, "прогрев не должен останавливать установку"


# ── Коннектор без кредов не включается ───────────────────────────────────
#
# Живая проверка всех девяти показала: `openmetadata` без кредов не «просто
# не работает», а падает с кодом 1 ещё до рукопожатия — на каждом старте
# сессии, при полностью исправной установке. Проверено рукопожатием:
# openmetadata — код 1, ноль инструментов; grafana — код 0, но 65
# инструментов против молча подставленного http://localhost:3000; growthbook
# — код 0, три инструмента без ключа. Правило общее: нет кредов — нет
# коннектора, и человеку сказано, каких именно значений не хватает.

NO_CREDS_CONNECTORS = ("grafana", "openmetadata", "growthbook", "sheets",
                       "clickhouse-dwh", "superset")


def test_connectors_without_credentials_do_not_get_enabled(tmp_path):
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
               dotenv=DOTENV_WMS_AND_JIRA)

    enabled = _enabled(repo)
    assert "clickhouse-wms" in enabled and "atlassian" in enabled
    # trino кредов не требует вовсе (SSO) — его отсутствие в списке означало
    # бы, что правило рубит лишнее.
    assert "trino" in enabled, enabled
    for cid in NO_CREDS_CONNECTORS:
        assert cid not in enabled, (
            "%s включён без кредов: под Codex это падение на каждом старте "
            "сессии, под Claude Code — «Connected» у нерабочего сервера" % cid)


def test_codex_config_has_no_connector_that_cannot_start(tmp_path):
    """Тот же гейт должен действовать и на конфиг Codex — он собирается из
    того же списка."""
    repo = _make_repo_copy(tmp_path)

    _, home = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
                         dotenv=DOTENV_WMS_AND_JIRA, engines=("claude", "codex"))

    config = (home / ".codex" / "uzum.config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.clickhouse-wms]" in config
    for cid in NO_CREDS_CONNECTORS:
        assert "[mcp_servers.%s]" % cid not in config, (
            "%s попал в конфиг Codex без кредов" % cid)


def test_wizard_says_which_values_are_missing_not_just_that_it_is_absent(tmp_path):
    """«Человек должен понимать, почему коннектора нет, а не искать его
    молча»: в итоге названы конкретные переменные."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
                           dotenv=DOTENV_WMS_AND_JIRA)

    out = result.stdout
    assert "openmetadata" in out and "нет OMD_URL, OMD_TOKEN" in out, out[-2500:]
    assert "нет GRAFANA_URL, GRAFANA_TOKEN" in out
    assert "нет GROWTHBOOK_TOKEN" in out
    assert "./setup.sh --add openmetadata" in out


def test_stale_enabled_connectors_are_removed_when_credentials_are_gone(tmp_path):
    """Состояние клона приёмки: в settings.local.json включены все девять,
    хотя кредов на три из них нет ни у кого. Список раньше только
    дополнялся и никогда не чистился — поэтому один раз включённый
    openmetadata падал в каждой сессии месяцами."""
    repo = _make_repo_copy(tmp_path)
    settings = repo / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledMcpjsonServers": [
        "atlassian", "clickhouse-dwh", "clickhouse-wms", "sheets", "superset",
        "trino", "grafana", "openmetadata", "growthbook"]}), encoding="utf-8")

    _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_OK],
               dotenv=DOTENV_WMS_AND_JIRA)

    enabled = _enabled(repo)
    for cid in NO_CREDS_CONNECTORS:
        assert cid not in enabled, (
            "%s остался включённым с прошлого раза — список чистится только "
            "добавлением" % cid)
    assert "clickhouse-wms" in enabled and "atlassian" in enabled


def test_a_connector_configured_earlier_survives_a_failed_live_check(tmp_path):
    """Обратная сторона правила: истёкший токен — не повод выкинуть
    коннектор из конфига. Правило про «кредов нет вовсе», а не про «сейчас
    не ответил».

    Машина, где Jira когда-то настроили успешно (токен лежит в secrets.env),
    а сегодня он протух: живая проверка отвечает 401, новый токен в
    secrets.env не пишется — и коннектор обязан остаться включённым, чтобы
    человек увидел внятную ошибку авторизации, а не молчаливое исчезновение
    инструментов Jira из сессии."""
    repo = _make_repo_copy(tmp_path)
    settings = repo / ".claude" / "settings.local.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"enabledMcpjsonServers": ["atlassian"]}),
                        encoding="utf-8")
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text(
        "JIRA_TOKEN='токен-с-прошлой-установки'\n", encoding="utf-8")

    _run_setup(repo, tmp_path, curl_rules=[WMS_OK, JIRA_REJECTED],
               dotenv=DOTENV_WMS_AND_JIRA)

    assert "atlassian" in _enabled(repo), (
        "коннектор с уже настроенными кредами выкинут из-за отказа живой "
        "проверки — человек потеряет инструменты Jira вместо понятной 401")


# ── Повторная установка не спрашивает то, что уже сохранено ──────────────
#
# Находка живого пользователя: на машине, где установка уже проходила и
# secrets.env полон, `./setup.sh --non-interactive` падал со списком «не
# хватает значений» — все пять значений лежали в файле. Мастер жил только
# окружением (ask_required_secret смотрит туда), а в окружение попадал
# только .env; secrets.env читали лишь отдельные места (телеметрия, Trino,
# connector_readiness, clickhouse_users). Отсюда же `default:` в ошибке
# DWH — коннектор уходил в свою дефолтную учётку.

DWH_OK = ["dwh.internal", 200, "9"]

# Ровно то, что write_env пишет после удачной первой установки: значения в
# одинарных кавычках, тем же форматом, что читает envfile.
SAVED_EVERYTHING = (
    "CH_WMS_HOST='wms.internal'\n"
    "CH_WMS_PORT='8123'\n"
    "CH_WMS_USER='имя-фамилия'\n"
    "CH_WMS_PASSWORD='пароль-склада'\n"
    "CH_WMS_SECURE='false'\n"
    "CH_DWH_HOST='dwh.internal'\n"
    "CH_DWH_PORT='8123'\n"
    "CH_DWH_USER='учётка-dwh'\n"
    "CH_DWH_PASSWORD='пароль-dwh'\n"
    "CH_DWH_SECURE='false'\n"
    "JIRA_TOKEN='токен-с-прошлой-установки'\n"
    "SUPERSET_URL='https://bi.uzum.uz'\n"
    "SUPERSET_USERNAME='логин-аналитика'\n"
    "SUPERSET_PASSWORD='пароль-superset'\n"
)

SAVED_ALL_RULES = [WMS_OK, DWH_OK, JIRA_OK]


def test_second_run_does_not_ask_again_for_what_is_already_saved(tmp_path):
    """Дословная находка: `./setup.sh --non-interactive` на настроенной
    машине. Ни одного «не хватает значений» и код возврата 0 — раньше здесь
    был список из пяти значений, каждое из которых лежало в secrets.env."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES,
                           saved_secrets=SAVED_EVERYTHING)

    out = result.stdout
    assert "не хватает значений" not in out, out[-3000:]
    for label in ("CH_WMS_USER", "CH_WMS_PASSWORD", "CH_DWH_USER",
                  "CH_DWH_PASSWORD", "JIRA_TOKEN"):
        assert "✗ %s" % label not in out, (
            "%s спрошен заново, хотя лежит в secrets.env:\n%s" % (label, out[-3000:]))
    assert result.returncode == 0, out[-3000:]


def test_saved_dwh_credentials_go_into_the_live_check_not_the_default_account(tmp_path):
    """Вторая половина находки: «Code: 194 … default: Authentication
    failed». Без подхвата сохранённого CH_DWH_USER пуст, заголовок уходит
    пустым, и ClickHouse подставляет свою учётку `default`. Смотрим, ЧЕМ
    мастер постучался."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--non-interactive"],
               curl_rules=SAVED_ALL_RULES, saved_secrets=SAVED_EVERYTHING)

    calls = [c for c in _curl_calls(tmp_path) if "dwh.internal" in c["url"]]
    assert calls, "живой проверки DWH под сохранённой учёткой не было вовсе"
    assert "X-ClickHouse-User: учётка-dwh" in calls[0]["config"], calls[0]["config"]
    assert "X-ClickHouse-Key: пароль-dwh" in calls[0]["config"]


def test_saved_values_are_still_checked_live_not_taken_on_faith(tmp_path):
    """Смысл мастера — показать работающий доступ, а не то, что он нашёл
    строчку в файле. Сохранённый токен протух: запрос обязан уйти, отказ —
    прозвучать, коннектор — не включиться."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=[WMS_OK, DWH_OK, JIRA_REJECTED],
                           saved_secrets=SAVED_EVERYTHING)

    calls = [c for c in _curl_calls(tmp_path) if "jira.uzum.com" in c["url"]]
    assert calls, "сохранённый токен приняли на веру — живой проверки не было"
    assert "Bearer токен-с-прошлой-установки" in calls[0]["config"], calls[0]["config"]
    assert "токен не принят" in result.stdout, result.stdout[-3000:]
    assert "atlassian" not in _enabled(repo)


def test_dotenv_wins_over_the_saved_value(tmp_path):
    """Приоритет: файл доступов правят именно затем, чтобы поменять
    значение. Если сохранённое перебивает .env, поменять пароль повторным
    прогоном стало бы нечем."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--non-interactive"],
               curl_rules=SAVED_ALL_RULES, saved_secrets=SAVED_EVERYTHING,
               dotenv="JIRA_TOKEN=токен-из-файла\n")

    calls = [c for c in _curl_calls(tmp_path) if "jira.uzum.com" in c["url"]]
    assert calls, "живой проверки Jira не было"
    assert "Bearer токен-из-файла" in calls[0]["config"], calls[0]["config"]
    assert "токен-с-прошлой-установки" not in calls[0]["config"]


def test_explicitly_given_environment_wins_over_the_saved_value(tmp_path):
    """Та же граница со стороны окружения: `JIRA_TOKEN=… ./setup.sh` —
    разовая подстановка, и сохранённое не должно её затирать."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--non-interactive"],
               curl_rules=SAVED_ALL_RULES, saved_secrets=SAVED_EVERYTHING,
               extra_env={"JIRA_TOKEN": "токен-из-окружения"})

    calls = [c for c in _curl_calls(tmp_path) if "jira.uzum.com" in c["url"]]
    assert calls, "живой проверки Jira не было"
    assert "Bearer токен-из-окружения" in calls[0]["config"], calls[0]["config"]
    assert "токен-с-прошлой-установки" not in calls[0]["config"]


def test_a_blank_saved_value_is_still_not_a_value(tmp_path):
    """Сторож прошлого круга: `write_env` пишет пустой ответ как `KEY=''`, и
    такой «доступ» не настроен. Правило одно на оба источника — мастер и
    гейт «коннектор без кредов не включается» читают secrets.env одним
    разборщиком и одинаково считают пробелы отсутствием значения."""
    repo = _make_repo_copy(tmp_path)
    blank_user = SAVED_EVERYTHING.replace(
        "CH_WMS_USER='имя-фамилия'", "CH_WMS_USER='   '")
    assert blank_user != SAVED_EVERYTHING, "подмена значения в образце не сработала"

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES, saved_secrets=blank_user)

    out = result.stdout
    assert "не хватает значений" in out, out[-3000:]
    assert "CH_WMS_USER (логин ClickHouse WMS)" in out, out[-3000:]
    # Остальное сохранённое подхвачено — недостающим объявлено ровно пустое.
    assert "CH_DWH_USER" not in out.split("не хватает значений")[1]
    assert result.returncode == 1
    assert "clickhouse-wms" not in _enabled(repo), (
        "мастер и гейт разошлись в том, что считать заданным значением")


def test_first_install_on_a_clean_machine_is_unchanged(tmp_path):
    """Обратная сторона: без secrets.env всё как было — ни строки про
    сохранённое, и все пять значений честно объявлены недостающими."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"])

    out = result.stdout
    assert "Нашёл сохранённые доступы" not in out, out[-3000:]
    assert "не хватает значений" in out, out[-3000:]
    for label in ("CH_WMS_USER (логин ClickHouse WMS)",
                  "CH_WMS_PASSWORD (пароль ClickHouse WMS)",
                  "CH_DWH_USER (логин ClickHouse DWH)",
                  "CH_DWH_PASSWORD (пароль ClickHouse DWH)",
                  "JIRA_TOKEN (токен Jira)"):
        assert label in out, out[-3000:]
    assert result.returncode == 1


def test_saved_secrets_do_not_open_the_gate_for_connectors_without_credentials(tmp_path):
    """Гейт прошлого круга не должен поехать: подхват сохранённого добавляет
    значения, а не готовность. Чего в secrets.env нет — того нет и в
    списке включённых."""
    repo = _make_repo_copy(tmp_path)

    _run_setup(repo, tmp_path, args=["--non-interactive"],
               curl_rules=SAVED_ALL_RULES, saved_secrets=SAVED_EVERYTHING)

    enabled = _enabled(repo)
    for cid in ("clickhouse-wms", "clickhouse-dwh", "atlassian", "superset"):
        assert cid in enabled, "%s настроен, но не включён: %s" % (cid, enabled)
    for cid in ("grafana", "openmetadata", "growthbook", "sheets"):
        assert cid not in enabled, (
            "%s включён, хотя его кредов в secrets.env нет" % cid)


def test_add_still_asks_for_a_value_that_is_already_saved(tmp_path):
    """`./setup.sh --add <коннектор>` существует ровно затем, чтобы ввести
    значение заново (протух токен, сменился пароль). Подставить сохранённое
    здесь значило бы отнять единственный способ его поменять: вопроса нет, а
    живая проверка снова падает на старом значении."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--add", "atlassian"],
                           curl_rules=[JIRA_OK], saved_secrets=SAVED_EVERYTHING,
                           answers="токен-введённый-сейчас\n\n")

    assert "Нашёл сохранённые доступы" not in result.stdout, result.stdout[-2000:]
    calls = [c for c in _curl_calls(tmp_path) if "jira.uzum.com" in c["url"]]
    assert calls, "вопрос не задали и проверять стало нечего"
    assert "Bearer токен-введённый-сейчас" in calls[0]["config"], calls[0]["config"]
