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
import time
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


# Ответы `netbird status --json`, снятые с живой машины (netbird 0.73.2) и
# урезанные до полей, которые читает мастер. Различие между состояниями —
# ровно то, что было снято: management.connected, daemonStatus, счётчик
# пиров. Процесс демона в ОБОИХ состояниях жив, поэтому старая проверка
# `pgrep -x netbird` их не различала вовсе.
NETBIRD_STATUS_UP = (
    '{"peers":{"total":2,"connected":2},"daemonStatus":"Connected",'
    '"management":{"url":"https://vpn.daymarket.uz:33073","connected":true,"error":""},'
    '"signal":{"url":"http://vpn.daymarket.uz:10000","connected":true,"error":""}}'
)
NETBIRD_STATUS_DOWN = (
    '{"peers":{"total":0,"connected":0},"daemonStatus":"Idle",'
    '"management":{"url":"https://vpn.daymarket.uz:33073","connected":false,"error":""},'
    '"signal":{"url":"http://vpn.daymarket.uz:10000","connected":false,"error":""}}'
)


def _stubs(tmp_path, engines=("claude",), with_netbird=True):
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

    if with_netbird:
        _script(stub_dir / "netbird", """#!/usr/bin/env python3
# Подставной netbird. Отвечает ровно тем, что задал тест, — включая то, чем
# настоящий netbird 0.73.2 отличает свои состояния (снято живыми прогонами):
# при удачном вызове JSON идёт в stdout, а предупреждения grpc всё равно
# летят в stderr; когда демон недоступен — stdout ПУСТ, код возврата 1, вся
# диагностика в stderr. Стаб ничего не решает за проверяемый код: он не
# знает ни про «✓», ни про «✗», только печатает и возвращает код.
import os, sys, time
time.sleep(float(os.environ.get("UZUM_TEST_NETBIRD_SLEEP", "0")))
sys.stderr.write(os.environ.get("UZUM_TEST_NETBIRD_STDERR", ""))
sys.stdout.write(os.environ.get("UZUM_TEST_NETBIRD_STDOUT", ""))
sys.exit(int(os.environ.get("UZUM_TEST_NETBIRD_CODE", "0")))
""")

    # Единственное место, где мастер зовёт pgrep, — запасной путь Netbird
    # (команды нет в PATH, но процесс демона может быть жив). Стабим, иначе
    # тест читал бы состояние ЭТОЙ машины, где демон обычно запущен.
    _script(stub_dir / "pgrep", """#!/usr/bin/env bash
[ "${UZUM_TEST_PGREP_FOUND:-0}" = "1" ] && exit 0
exit 1
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
               stub_overrides=None, extra_env=None, saved_secrets=None,
               netbird="up"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    if "codex" in engines:
        # Живая проверка доверия хукам первым делом смотрит на auth.json и
        # без него не запускает codex вовсе — тогда тесты про «запускался
        # или нет» ничего бы не проверяли.
        (home / ".codex").mkdir(exist_ok=True)
        (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")
    stub_dir = _stubs(tmp_path, engines=engines, with_netbird=netbird != "absent")
    for name, body in (stub_overrides or {}).items():
        _script(stub_dir / name, body)
    if netbird == "absent":
        # Машина без Netbird: команды нет вовсе. Настоящий netbird лежит в
        # системном PATH, поэтому его каталог оттуда убираем — иначе тест
        # проверял бы состояние VPN ЭТОЙ машины, а не поведение мастера.
        path_dirs = [d for d in os.environ["PATH"].split(os.pathsep)
                     if d and not (Path(d) / "netbird").exists()]
        assert not any((Path(d) / "netbird").exists() for d in path_dirs)
        search_path = os.pathsep.join([str(stub_dir), *path_dirs])
    else:
        search_path = "%s%s%s" % (stub_dir, os.pathsep, os.environ["PATH"])
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
        "PATH": search_path,
        "USER": "test",
        "TERM": "dumb",
        # По умолчанию туннель поднят — иначе каждый тест про что угодно
        # другое начинался бы с красной строки про Netbird.
        "UZUM_TEST_NETBIRD_STDOUT": NETBIRD_STATUS_UP,
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


def _curl_calls(tmp_path):
    """Что мастер реально спрашивал у сервера через curl: URL и заголовки (в
    них логин и пароль). Заголовки нужны, чтобы проверять не только «сходил»,
    но и «той ли учёткой»."""
    path = tmp_path / "curl.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


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


# ── Телеметрия: проверяется ЗАПИСЬ, а не чтение ─────────────────────────
#
# Находка живой приёмки: мастер проверял телеметрию запросом SELECT count()
# FROM sandbox.ai_usage_sessions и печатал на нём «✓ на месте, строк: N».
# У учётки только на чтение этот запрос ПРОХОДИТ — человек читал зелёную
# галочку про телеметрию, которая писать не может, а сессии молча копились в
# очереди на диске. Снято живьём на двух настоящих учётках одного кластера:
#
#     a-bir         SELECT 200 | INSERT 500 «Code: 164 … (READONLY)»
#     n-lyubchenko  SELECT 200 | INSERT 200
#
# Поэтому подставной ClickHouse ниже ведёт себя ровно так же: SELECT отвечает
# ВСЕМ, INSERT — только тому, кому выданы права. Он не знает ни про «✓», ни
# про «✗» и ничего не подтверждает: все утверждения тестов — про то, что
# напечатал сам мастер и с каким запросом он пришёл на сервер. Верни проверку
# к чтению — и тесты про отказ упадут: учётка без прав получит «телеметрия
# пишется».


class _FakeClickHouse(object):
    """HTTP-сервер, отвечающий как ClickHouse складского кластера.

    Не заглушка curl: мастер ходит тем же кодом, что и хуки
    (lib/telemetry.py), а его запрос — настоящий HTTP. Проверять такое можно
    только тем, что реально доехало по сети.
    """

    # Дословный ответ боевого кластера учётке без прав (снят живым запросом).
    READONLY = ("Code: 164. DB::Exception: %s: Cannot execute query in "
                "readonly mode. (READONLY) (version 25.8.25.37-yc.1)")

    def __init__(self, writers=()):
        import http.server
        import threading
        import urllib.parse

        requests = self.requests = []
        writers = set(writers)
        readonly = self.READONLY

        class Handler(http.server.BaseHTTPRequestHandler):
            def _handle(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length).decode("utf-8") if length else ""
                query = urllib.parse.parse_qs(
                    urllib.parse.urlparse(self.path).query).get("query", [""])[0]
                user = self.headers.get("X-ClickHouse-User", "")
                requests.append({
                    "method": self.command,
                    "query": query,
                    "user": user,
                    "key": self.headers.get("X-ClickHouse-Key", ""),
                    "body": body,
                })
                insert = query.strip().upper().startswith("INSERT")
                if insert and user not in writers:
                    status, payload = 500, (readonly % user).encode("utf-8")
                elif insert:
                    status, payload = 200, b""
                else:
                    # Чтение проходит у любой заведённой учётки — как на бою.
                    status, payload = 200, b"15"
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = _handle
            do_POST = _handle

            def log_message(self, *a):
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.host, self.port = self._server.server_address
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        return False

    def dotenv(self, user="imya-familiya", password="parol-sklada", extra=""):
        return (
            "CH_WMS_HOST=%s\n"
            "CH_WMS_PORT=%s\n"
            "CH_WMS_USER=%s\n"
            "CH_WMS_PASSWORD=%s\n"
            "CH_WMS_SECURE=false\n"
            "%s" % (self.host, self.port, user, password, extra)
        )

    def secrets(self, user="imya-familiya", password="parol-sklada", extra=""):
        return (
            "CH_WMS_HOST='%s'\n"
            "CH_WMS_PORT='%s'\n"
            "CH_WMS_USER='%s'\n"
            "CH_WMS_PASSWORD='%s'\n"
            "CH_WMS_SECURE='false'\n"
            "%s" % (self.host, self.port, user, password, extra)
        )

    def inserts(self):
        return [r for r in self.requests
                if r["query"].strip().upper().startswith("INSERT")]

    def selects(self):
        return [r for r in self.requests
                if r["query"].strip().upper().startswith("SELECT")]


def test_telemetry_check_inserts_instead_of_reading_the_table(tmp_path):
    """Главное утверждение находки: мастер обязан проверять то, что обещает.

    Обещает он запись — значит и запрос должен быть вставкой, той же самой,
    которой пишут хуки. Мутация «проверка снова читает» ловится здесь дважды:
    запросом, с которым мастер пришёл, и отсутствием SELECT'а вовсе."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                               dotenv=ch.dotenv())

    inserts = ch.inserts()
    assert inserts, (
        "мастер не сделал ни одной вставки — проверять запись чтением и есть "
        "находка:\n%s" % result.stdout)
    assert inserts[0]["method"] == "POST", inserts[0]
    assert inserts[0]["query"].startswith(
        "INSERT INTO sandbox.ai_usage_sessions"), inserts[0]["query"]
    assert not ch.selects(), "проверка снова читает: %s" % ch.selects()
    assert "пробная запись" in result.stdout, result.stdout
    assert "телеметрия пишется" in result.stdout, result.stdout


def test_telemetry_check_leaves_no_row_behind(tmp_path):
    """Решение про проверочную строку: её нет вовсе. Мусор в боевой таблице
    оставлять нельзя (отладочные строки schema-check уже приходилось
    вычищать), а удалить его учётке аналитика нечем — прав на ALTER DELETE у
    неё тем более нет. Права ClickHouse проверяет до чтения тела запроса,
    поэтому пустая вставка получает тот же отказ, что и настоящая, и следа не
    оставляет (см. докстринг lib/telemetry.py::check_write)."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        _run_setup(repo, tmp_path, args=["--add", "telemetry"], dotenv=ch.dotenv())

    inserts = ch.inserts()
    assert inserts, "вставки не было вовсе"
    assert inserts[0]["body"] == "", (
        "мастер записал строку в боевую таблицу: %r" % inserts[0]["body"])


def test_telemetry_without_insert_rights_is_told_in_words(tmp_path):
    """Учётка только на чтение — та самая, что была у всей пилотной группы.
    Мастер обязан сказать правду: причина, следствие, что делать."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=()) as ch:
        result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                               dotenv=ch.dotenv())

    out = result.stdout
    assert "телеметрия пишется" not in out, out
    assert "прав INSERT в sandbox" in out, out          # причина
    assert "READONLY" in out, out                       # ответ сервера, а не пересказ
    assert "не влияет" in out, out                      # следствие: работать не мешает
    assert "очереди" in out, out                        # строки не теряются
    assert "JSM" in out, out                            # что делать
    assert "./setup.sh --add telemetry" in out, out


def test_telemetry_without_insert_rights_does_not_fail_the_install(tmp_path):
    """Отсутствие прав на статистику — не поломка инструмента. Ни красного ✗
    на телеметрию, ни ненулевого кода возврата у установки."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=()) as ch:
        result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                               dotenv=ch.dotenv())

    assert result.returncode == 0, result.stdout[-3000:]
    block = result.stdout.split("── Телеметрия")[1]
    assert "✗" not in block, "красный ✗ на всю установку:\n%s" % block


def test_telemetry_is_not_switched_off_when_writing_is_denied(tmp_path):
    """Решение про «включать ли»: телеметрия остаётся включённой. Хук пишет в
    локальную очередь (сети не касается), очередь ограничена по объёму и
    возрасту, отправка повторяется каждой сессией — значит после выдачи прав
    накопленное уедет само. Выключить значило бы гарантированно потерять
    именно те сессии, ради которых пилот и считают."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=()) as ch:
        _, home = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                             dotenv=ch.dotenv())

    assert "TELEMETRY_ENABLED" not in _secrets(home), _secrets(home)


def test_telemetry_asks_nothing_and_checks_the_warehouse_access(tmp_path):
    """Решение владельца: телеметрия пишется в складской ClickHouse теми же
    логином и паролем — значит спрашивать нечего. Живая проверка при этом
    обязана остаться: это единственное место, где человек узнаёт, что
    телеметрия работает."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        result, home = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                                  dotenv=ch.dotenv(extra="JIRA_TOKEN=токен-jira\n"))

    out = result.stdout
    for asked in ("Хост телеметрии", "  Логин:", "  Пароль:"):
        assert asked not in out, "мастер снова спрашивает про телеметрию:\n%s" % out
    # И ничего лишнего в secrets.env: дубль складских значений — ловушка,
    # он перебивает CH_WMS_* и после смены пароля тихо остановит запись.
    assert "TELEMETRY_CH_" not in _secrets(home), _secrets(home)


def test_telemetry_check_goes_with_the_warehouse_credentials(tmp_path):
    """Мутация «телеметрия берёт не те креды» обязана быть видна: смотрим,
    какие именно логин и пароль ушли в проверку."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        _run_setup(repo, tmp_path, args=["--add", "telemetry"], dotenv=ch.dotenv())

    inserts = ch.inserts()
    assert inserts, "живой проверки телеметрии не было вовсе"
    assert inserts[0]["user"] == "imya-familiya", inserts[0]["user"]
    assert inserts[0]["key"] == "parol-sklada"


def test_telemetry_without_the_warehouse_says_so_instead_of_asking(tmp_path):
    """Склад ещё не настроен — писать некуда. Это не ошибка установки и не
    повод задавать вопросы: доступ появится, телеметрия включится сама."""
    repo = _make_repo_copy(tmp_path)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        result, home = _run_setup(repo, tmp_path, args=["--add", "telemetry"])

    assert "складской ClickHouse ещё не настроен" in result.stdout, result.stdout
    assert "TELEMETRY_CH_" not in _secrets(home)
    assert not ch.requests, "стучались в базу, не зная адреса: %s" % ch.requests


def test_telemetry_unreachable_cluster_is_an_error_not_a_missing_right(tmp_path):
    """Обратная сторона мягкой формулировки: «нет прав» не должно печататься
    на всё подряд. Недоступный кластер — это поломка, и говорить о ней надо
    иначе (✗ и текст ответа), иначе человек пойдёт заводить заявку вместо
    того, чтобы поднять Netbird."""
    repo = _make_repo_copy(tmp_path)

    # Порт, на котором заведомо никто не слушает.
    result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"],
                           dotenv=("CH_WMS_HOST=127.0.0.1\nCH_WMS_PORT=1\n"
                                   "CH_WMS_USER=imya-familiya\n"
                                   "CH_WMS_PASSWORD=parol-sklada\n"))

    out = result.stdout
    assert "не прошла" in out, out
    assert "прав INSERT" not in out, out
    assert result.returncode == 0, out[-2000:]


def test_wizard_removes_telemetry_duplicates_of_the_warehouse_values(tmp_path):
    """Машина, установленная до этой правки: в secrets.env лежат
    TELEMETRY_CH_*, дословно повторяющие CH_WMS_*. Они перебивают складские
    значения — после смены пароля склада телеметрия молча перестала бы
    писаться. Мастер обязан их убрать."""
    repo = _make_repo_copy(tmp_path)
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)

    with _FakeClickHouse(writers=("imya-familiya",)) as ch:
        (home / ".config" / "uzum-ai" / "secrets.env").write_text(
            ch.secrets(extra=(
                "TELEMETRY_CH_HOST='%s'\n"
                "TELEMETRY_CH_PORT='%s'\n"
                "TELEMETRY_CH_USER='imya-familiya'\n"
                "TELEMETRY_CH_PASSWORD='parol-sklada'\n" % (ch.host, ch.port))),
            encoding="utf-8")

        _run_setup(repo, tmp_path, args=["--add", "telemetry"])

    secrets = _secrets(home)
    assert "TELEMETRY_CH_" not in secrets, secrets
    assert "CH_WMS_PASSWORD='parol-sklada'" in secrets, "снесли лишнее:\n%s" % secrets


def test_wizard_keeps_a_deliberate_writer_account_and_checks_it(tmp_path):
    """Исключение, ради которого механизм оставлен: у пилотной группы может
    не быть прав INSERT, и запись пойдёт отдельной учёткой-писателем.
    Заданное руками значение — не дубль: его нельзя ни стереть, ни
    проигнорировать при проверке."""
    repo = _make_repo_copy(tmp_path)
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True, exist_ok=True)

    with _FakeClickHouse(writers=("ai-usage-writer",)) as ch:
        (home / ".config" / "uzum-ai" / "secrets.env").write_text(
            ch.secrets(extra="TELEMETRY_CH_USER='ai-usage-writer'\n"
                             "TELEMETRY_CH_PASSWORD='parol-pisatelya'\n"),
            encoding="utf-8")

        result, _ = _run_setup(repo, tmp_path, args=["--add", "telemetry"])

    secrets = _secrets(home)
    assert "TELEMETRY_CH_USER='ai-usage-writer'" in secrets, secrets
    inserts = ch.inserts()
    assert inserts, "живой проверки телеметрии не было"
    assert inserts[0]["user"] == "ai-usage-writer", (
        "проверили складскую учётку, а писать будет другая — это и есть "
        "«зелёная установка, пустая таблица»")
    # Адрес при этом остаётся складским — иначе запрос не пришёл бы на этот
    # сервер вовсе: ради смены учётки не должно приходиться переписывать все
    # четыре значения.
    assert "заданы отдельно" in result.stdout
    assert "телеметрия пишется" in result.stdout, result.stdout


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
    assert "нет GRAFANA_TOKEN" in out
    assert "GRAFANA_URL" not in out, (
        "адрес Grafana объявлен дефолтом в реестре — просить его у человека нечего")
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


def test_every_saved_access_is_still_checked_live_not_taken_on_faith(tmp_path):
    """Смысл мастера — показать работающий доступ, а не то, что он нашёл
    строчку в файле. Проверяем это на КАЖДОМ сохранённом доступе, а не на
    одной Jira: имя теста отвечает за принцип, значит и держать он должен
    принцип. Смотрим, что ушёл запрос и что он ушёл с сохранённым
    значением, — «сходил куда-то» тут ничего не доказывает."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES, saved_secrets=SAVED_EVERYTHING)

    calls = _curl_calls(tmp_path)

    def checked(where, credential):
        return [c for c in calls if where in c["url"] and credential in c["config"]]

    # Складской ClickHouse, общий ClickHouse и Jira — их мастер проверяет
    # curl'ом, поэтому доказательство лежит в его логе.
    for where, credential, what in (
            ("wms.internal:8123/?query=SELECT+count()+FROM+system.databases",
             "X-ClickHouse-User: имя-фамилия", "ClickHouse WMS"),
            ("dwh.internal:8123/?query=SELECT+count()+FROM+system.databases",
             "X-ClickHouse-User: учётка-dwh", "ClickHouse DWH"),
            ("jira.uzum.com", "Bearer токен-с-прошлой-установки", "Jira")):
        assert checked(where, credential), (
            "%s: сохранённое значение приняли на веру — живого запроса с ним "
            "не было. Запросы, которые ушли: %s"
            % (what, [c["url"] for c in calls]))

    # Телеметрия проверяется не curl'ом, а тем же кодом, которым пишут хуки
    # (lib/telemetry.py::check_write) — в curl.log её запроса нет и быть не
    # должно. Здесь доказательство живой попытки — то, что мастер напечатал её
    # исход по сохранённому адресу; чем именно он стучался, проверяют тесты
    # телеметрии выше, на настоящем HTTP-сервере.
    assert "sandbox.ai_usage_sessions" in result.stdout, result.stdout[-3000:]
    assert "wms.internal:8123" in result.stdout, result.stdout[-3000:]
    assert not [c for c in calls if "ai_usage_sessions" in c["url"]], (
        "телеметрию снова проверяют вторым запросом мимо lib/telemetry.py")

    # Superset проверяется не curl'ом, а тем же кодом, которым потом ходит
    # коннектор (superset_mcp.py --check), — смотрим в лог uv.
    uv_calls = [json.loads(line) for line in
                (tmp_path / "uv.log").read_text(encoding="utf-8").splitlines()]
    superset = [c for c in uv_calls if "--check" in c["argv"]]
    assert superset, "Superset: сохранённые логин и пароль не пошли в живую проверку"
    assert superset[-1]["superset_env"]["SUPERSET_USERNAME"] == "логин-аналитика"
    assert superset[-1]["superset_env"]["SUPERSET_PASSWORD"] == "пароль-superset"


def test_a_stale_saved_token_is_reported_and_the_connector_is_not_enabled(tmp_path):
    """Обратная сторона живой проверки: сохранённый токен протух — отказ
    обязан прозвучать, а коннектор не включиться."""
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


def test_add_without_questions_reuses_the_saved_value_instead_of_failing(tmp_path):
    """Граница проходит по «будет ли задан вопрос», а не по режиму. С
    --non-interactive вопроса нет вовсе, поэтому отказ от подстановки ничего
    не сохраняет — только гарантирует падение на значении, которое лежит в
    secrets.env. А `./setup.sh --add atlassian` — та самая команда, которую
    мастер сам советует строкой «пропущено — подключить позже»."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path,
                           args=["--add", "atlassian", "--non-interactive"],
                           curl_rules=[JIRA_OK], saved_secrets=SAVED_EVERYTHING)

    assert "не хватает значений" not in result.stdout, result.stdout[-2500:]
    assert result.returncode == 0, result.stdout[-2500:]
    calls = [c for c in _curl_calls(tmp_path) if "jira.uzum.com" in c["url"]]
    assert calls, "сохранённый доступ не перепроверен живым запросом"
    assert "Bearer токен-с-прошлой-установки" in calls[0]["config"], calls[0]["config"]
    assert "atlassian" in _enabled(repo)


# ── Необязательный коннектор не роняет прогон без вопросов ───────────────
#
# Форма одна на все необязательные: первым спрашивается значение БЕЗ
# дефолта, и пустой ответ выводит из коннектора целиком. У openmetadata это
# OMD_URL, у sheets GOOGLE_SA_FILE, у superset SUPERSET_USERNAME, у grafana
# GRAFANA_TOKEN. У адресов Superset и Grafana дефолт есть, поэтому «нет URL»
# не наступает никогда — и пока развилка стояла на адресе, `./setup.sh
# --non-interactive` на машине, где коннектор осознанно не заводили, выходил
# с кодом 1.

SAVED_WITHOUT_SUPERSET = "".join(
    line + "\n" for line in SAVED_EVERYTHING.splitlines()
    if not line.startswith("SUPERSET_"))


def test_a_connector_nobody_configured_does_not_fail_the_run_without_questions(tmp_path):
    """Superset не в REQUIRED_SERVERS — значит человек, который его не
    заводил, не должен получать красный выход. Ровно как у трёх соседей."""
    assert "SUPERSET_" not in SAVED_WITHOUT_SUPERSET, SAVED_WITHOUT_SUPERSET
    assert "JIRA_TOKEN" in SAVED_WITHOUT_SUPERSET, "из образца вырезали лишнее"
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES,
                           saved_secrets=SAVED_WITHOUT_SUPERSET)

    assert "не хватает значений" not in result.stdout, result.stdout[-3000:]
    assert result.returncode == 0, result.stdout[-3000:]
    # Пропущен так же, как остальные необязательные: строкой и командой.
    assert "./setup.sh --add superset" in result.stdout
    assert "superset" not in _enabled(repo)


def test_grafana_without_a_token_does_not_fail_the_run_without_questions(tmp_path):
    """То же самое для Grafana, и по той же причине: адрес получил дефолт в
    реестре, значит «нет URL» больше не наступает, и без развилки по токену
    мастер дошёл бы до ask_required и уронил прогон у каждого, кто Grafana
    не заводил. Тест падает, если развилку вернуть на URL."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES,
                           saved_secrets=SAVED_EVERYTHING)

    assert "не хватает значений" not in result.stdout, result.stdout[-3000:]
    assert "✗ GRAFANA_TOKEN" not in result.stdout, result.stdout[-3000:]
    assert result.returncode == 0, result.stdout[-3000:]
    assert "./setup.sh --add grafana" in result.stdout
    assert "grafana" not in _enabled(repo)


def test_a_grafana_address_given_by_hand_without_a_token_is_not_swallowed(tmp_path):
    """Обратная сторона развилки: адрес назвали руками, а токен нет — значит
    Grafana человеку всё-таки нужна, и молчание выглядело бы как «мастер не
    заметил». Ровно как зеркальное предупреждение у Superset."""
    repo = _make_repo_copy(tmp_path)
    with_url = SAVED_EVERYTHING + "GRAFANA_URL='https://своя-графана.internal'\n"

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES, saved_secrets=with_url)

    assert "адрес не использован" in result.stdout, result.stdout[-3000:]
    assert "grafana" not in _enabled(repo)


def test_half_configured_superset_is_still_reported_as_missing(tmp_path):
    """Обратная сторона: логин есть, пароля нет — это не «Superset не
    нужен», а незаконченная настройка, и молчать о ней нельзя. Иначе
    развилка «необязателен» проглотила бы настоящую нехватку значения."""
    repo = _make_repo_copy(tmp_path)
    half = SAVED_WITHOUT_SUPERSET + "SUPERSET_USERNAME='логин-аналитика'\n"

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES, saved_secrets=half)

    assert "не хватает значений" in result.stdout, result.stdout[-3000:]
    assert "SUPERSET_PASSWORD" in result.stdout, result.stdout[-3000:]
    assert result.returncode == 1


# ── Netbird: проверяем состояние туннеля, а не наличие процесса ──────────
#
# Поймано на живой машине: `netbird status` показывал Management:
# Disconnected и Peers count: 0/0, процесс демона при этом был жив — и мастер
# печатал «✓ Netbird запущен», а следом четыре ✗ подряд от внутренних
# коннекторов с кодом 000. Единственная строка, которая должна была объяснить
# причину, говорила «всё хорошо».
#
# Стаб netbird ничего не решает за проверяемый код: он печатает ответ, который
# задал тест, и уходит с заданным кодом — ровно как настоящий. Все утверждения
# ниже — про то, что сказал мастер.


def _netbird_run(repo, tmp_path, stdout=None, code="0", stderr="",
                 netbird="up", pgrep_found=False, sleep="0"):
    env = {"UZUM_TEST_NETBIRD_CODE": code,
           "UZUM_TEST_NETBIRD_STDERR": stderr,
           "UZUM_TEST_NETBIRD_SLEEP": sleep,
           "UZUM_TEST_PGREP_FOUND": "1" if pgrep_found else "0"}
    if stdout is not None:
        env["UZUM_TEST_NETBIRD_STDOUT"] = stdout
    result, _ = _run_setup(repo, tmp_path, args=["--add", "growthbook"],
                           answers="ключ-gb\n\n", extra_env=env, netbird=netbird)
    return result.stdout


def test_a_connected_tunnel_is_reported_as_connected(tmp_path):
    """Положительный контроль. Без него всё, что ниже, прошло бы и на
    проверке, которая ругается всегда."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_UP)

    assert "Netbird подключён (пиров: 2/2)" in out, out[:1500]


def test_a_live_netbird_process_with_the_tunnel_down_is_not_called_fine(tmp_path):
    """Сама находка: демон жив, туннель опущен. Раньше здесь печаталось
    «✓ Netbird запущен» — и человек шёл перевыпускать токены."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_DOWN, pgrep_found=True)

    assert "Netbird подключён" not in out, out[:1500]
    assert "Netbird не подключён" in out, out[:1500]
    # Строка обязана помогать, а не только сообщать диагноз.
    assert "netbird up" in out, out[:1500]


def test_an_unrecognised_netbird_answer_is_not_called_fine(tmp_path):
    """Netbird — не наш продукт, формат вывода может поменяться. Тогда мастер
    обязан сказать «определить не удалось» и идти дальше, а не молчаливо
    согласиться, что всё хорошо."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout="статус: всё чудесно\n",
                       pgrep_found=True)

    assert "Netbird подключён" not in out, out[:1500]
    assert "состояние Netbird определить не удалось" in out, out[:1500]
    # И это не остановка установки: коннектор после этого настроен.
    assert "growthbook" in _enabled(repo), out[-2000:]


def test_json_without_the_expected_field_is_not_called_fine(tmp_path):
    """Второй способ, которым чужой формат ломается: JSON разобрался, а поля
    нет. Молчаливое «ок» тут — тот же дефект."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path,
                       stdout='{"peers":{"total":2,"connected":2}}',
                       pgrep_found=True)

    assert "Netbird подключён" not in out, out[:1500]
    assert "состояние Netbird определить не удалось" in out, out[:1500]
    assert "management.connected" in out, out[:1500]


def test_an_unreachable_daemon_says_how_to_start_it(tmp_path):
    """Демон не запущен: настоящий netbird уходит с кодом 1, стандартный
    вывод оставляет пустым, диагностику пишет в stderr (проверено живым
    прогоном). Это не «не установлен» и не «опущен» — и делать надо другое."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout="", code="1",
                       stderr="Error: failed to connect to daemon error: "
                              "context deadline exceeded\n")

    assert "Netbird подключён" not in out, out[:1500]
    assert "Служба Netbird не отвечает" in out, out[:1500]
    assert "netbird service start" in out, out[:1500]


def test_netbird_not_installed_at_all_is_still_reported(tmp_path):
    """Случай, который работал и раньше: Netbird на машине нет вовсе.
    Ломать его было нельзя."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, netbird="absent", pgrep_found=False)

    assert "Netbird не установлен" in out, out[:1500]
    assert "connectors/ACCESS.md" in out, out[:1500]


def test_a_missing_cli_with_a_live_daemon_is_not_called_missing(tmp_path):
    """Приложение стоит, а команды netbird в PATH нет. Сказать «не
    установлен» значило бы соврать в другую сторону: туннель может быть
    поднят. Мы просто не знаем — так и говорим."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, netbird="absent", pgrep_found=True)

    assert "Netbird не установлен" not in out, out[:1500]
    assert "Netbird подключён" not in out, out[:1500]
    assert "состояние Netbird определить не удалось" in out, out[:1500]
    assert "нет в PATH" in out, out[:1500]


def test_a_silent_netbird_does_not_hold_the_wizard(tmp_path):
    """Своего таймаута у проверки не было, а настоящий netbird при
    недоступном демоне молчит около 10 секунд (замерено). На фоне мгновенного
    остального мастера человек десять секунд смотрит в пустой экран и решает,
    что всё повисло. Нормальный ответ приходит за 0,26 с."""
    repo = _make_repo_copy(tmp_path)
    started = time.monotonic()

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_UP,
                       sleep="30", pgrep_found=True)

    spent = time.monotonic() - started
    assert "Netbird подключён" not in out, out[:1500]
    assert "состояние Netbird определить не удалось" in out, out[:1500]
    assert "не ответил за 5 с" in out, out[:1500]
    # Мастер дошёл до конца, а не завис на молчащей команде.
    assert "growthbook" in _enabled(repo), out[-2000:]
    assert spent < 30, (
        "мастер дождался молчащего netbird целиком — таймаут не сработал, "
        "прошло %.1f с" % spent)


def test_a_silent_netbird_names_the_usual_cause(tmp_path):
    """Строка должна помогать. Незапущенная служба — самый частый разбор, но
    названа как версия, а не как диагноз: наблюдение одно, причин молчать
    может быть больше."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_UP, sleep="30")

    assert "netbird service start" in out, out[:1500]


# ── Адрес Superset: один, и он в реестре ────────────────────────────────

def test_the_person_is_not_asked_for_a_variable_the_wizard_fills_itself(tmp_path):
    """connector_readiness числил SUPERSET_URL недостающим, хотя мастер
    подставляет его сам. На поведение не влияло, но в итоге установки человек
    читал имя переменной, которую у него никто не спрашивал, и шёл её
    искать."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_setup(repo, tmp_path, args=["--non-interactive"],
                           curl_rules=SAVED_ALL_RULES,
                           saved_secrets=SAVED_WITHOUT_SUPERSET)

    out = result.stdout
    assert "нет SUPERSET_USERNAME, SUPERSET_PASSWORD" in out, out[-3000:]
    assert "SUPERSET_URL" not in out, (
        "мастер называет переменную, которую подставляет сам:\n%s" % out[-3000:])


def test_the_superset_address_is_taken_from_the_registry_not_copied(tmp_path):
    """Два литерала одного адреса разошлись бы при первой правке: мастер
    предлагал бы один, коннектор ходил бы на другой. Источник один — реестр."""
    from connectors.registry import CONNECTORS_BY_ID

    declared = [item.default for item in CONNECTORS_BY_ID["superset"].env_vars()
                if item.source == "SUPERSET_URL"]
    assert declared and declared[0], "в реестре у SUPERSET_URL нет дефолта"
    address = declared[0]

    repo = _make_repo_copy(tmp_path)
    result, home = _run_setup(repo, tmp_path, args=["--add", "superset"],
                              answers="\nлогин-аналитика\nпароль-superset\n\n")

    # На приглашение не смотрим: `read -p` печатает его, только когда ввод
    # идёт с терминала, а здесь это труба — проверять было бы нечего.
    # Смотрим на то, что мастер реально записал: подстановка дефолта видна
    # именно там, и мутация «свой литерал в setup.sh» её ломает.
    assert result.returncode == 0, result.stdout[-2000:]
    assert "SUPERSET_URL='%s'" % address in _secrets(home), (
        "мастер записал не тот адрес, что объявлен в реестре:\n%s" % _secrets(home))


NETBIRD_STATUS_NO_PEERS = (
    '{"peers":{"total":2,"connected":0},"daemonStatus":"Connected",'
    '"management":{"url":"https://vpn.daymarket.uz:33073","connected":true,"error":""},'
    '"signal":{"url":"http://vpn.daymarket.uz:10000","connected":true,"error":""}}'
)


def test_a_tunnel_without_peers_is_not_a_green_line(tmp_path):
    """Последнее место, где зелёная строка могла сопровождать неработающий
    туннель: management подключён, а пиров ноль. Маршруты до прод-сетей
    раздают пиры-бастионы — значит данных не будет, а мастер говорил
    «✓ Netbird подключён (пиров: 0/2)» и оставлял человека догадываться, что
    значит это число."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_NO_PEERS)

    assert "✓ Netbird подключён" not in out, out[:1500]
    assert "ни один пир не отвечает" in out, out[:1500]
    assert "пиров: 0/2" in out, out[:1500]


def test_a_tunnel_without_peers_says_what_to_check_without_overclaiming(tmp_path):
    """Состояние вживую снять не удалось — это разбор устройства сети, а не
    наблюдение. Строка обязана и помогать, и не выдавать себя за факт."""
    repo = _make_repo_copy(tmp_path)

    out = _netbird_run(repo, tmp_path, stdout=NETBIRD_STATUS_NO_PEERS)

    assert "скорее всего" in out, out[:1500]
    assert "netbird status -d" in out, out[:1500]


def test_a_network_with_no_peers_at_all_is_not_accused(tmp_path):
    """Обратная сторона: сеть, где пиров нет вовсе (0 из 0), — это не
    «пиры отвалились». Без этой границы правило ругалось бы на исправную
    установку, у которой просто нет соседей."""
    repo = _make_repo_copy(tmp_path)
    empty_network = NETBIRD_STATUS_NO_PEERS.replace(
        '"total":2,"connected":0', '"total":0,"connected":0')
    assert empty_network != NETBIRD_STATUS_NO_PEERS, "подмена в образце не сработала"

    out = _netbird_run(repo, tmp_path, stdout=empty_network)

    assert "Netbird подключён (пиров: 0/0)" in out, out[:1500]
    assert "ни один пир не отвечает" not in out, out[:1500]
