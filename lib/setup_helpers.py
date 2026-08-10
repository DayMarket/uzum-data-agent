"""Помощники установщика: запись секретов, список включённых серверов и
чтение файла доступов `.env` (задача 14 — заполнение мастера файлом).

Только стандартная библиотека — этот модуль подключается из setup.sh (мастер
установки), который должен работать до того, как в системе появится хоть один
внешний пакет.
"""
import json
import os
import shlex
import stat
import time

import envfile
import hook_scope


def write_env(path, values):
    """Дописать переменные в env-файл, заменяя существующие. Права 600.

    Значения пишутся в одинарных кавычках (envfile.quote): файл `source`-ит
    bin/uzum перед запуском Claude Code, и без кавычек пароль с пробелом,
    долларом или бэктиком либо не доезжал до переменной вообще, либо
    подставлял в себя чужое значение, либо выполнял команду.

    Каталог, в котором лежит файл (обычно ~/.config/uzum-ai), тоже переводится
    на 700: секреты внутри и так закрыты правами файла, но сам каталог до
    этой правки создавался с правами по умолчанию (обычно 755) — то есть
    любой процесс того же пользователя мог хотя бы увидеть список файлов
    внутри (имена, размеры, mtime), даже не имея доступа к их содержимому.
    """
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    os.chmod(dirpath, 0o700)
    existing = envfile.read(path)  # порядок ключей сохраняется
    existing.update(values)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(envfile.format_line(key, value))
    os.chmod(path, 0o600)


def drop_env(path, names):
    """Убрать переменные из env-файла. Возвращает список реально удалённых.

    Нужна ровно для одного случая — телеметрии. Раньше мастер спрашивал
    хост/порт/логин/пароль телеметрии отдельно и записывал их в secrets.env
    рядом с CH_WMS_*, хотя пишет она в тот же складской ClickHouse теми же
    кредами. Теперь значения берутся прямо из CH_WMS_* (см.
    lib/telemetry.py::Config.from_env), а оставшийся дубль в secrets.env
    стал ловушкой: он перебивает CH_WMS_*, и после смены пароля склада
    телеметрия молча перестала бы писаться — с исправным на вид доступом.

    Удаляем только тогда, когда значение дословно совпадает с тем, что и
    так придёт из CH_WMS_*; осознанно заданное другое значение (учётка-
    писатель) — не дубль, его не трогаем. Решение о совпадении принимает
    вызывающий код, здесь только запись файла.
    """
    if not os.path.exists(path):
        return []
    existing = envfile.read(path)
    removed = [name for name in names if name in existing]
    if not removed:
        return []
    for name in removed:
        del existing[name]
    dirpath = os.path.dirname(path) or "."
    os.makedirs(dirpath, exist_ok=True)
    os.chmod(dirpath, 0o700)
    with open(path, "w", encoding="utf-8") as f:
        for key, value in existing.items():
            f.write(envfile.format_line(key, value))
    os.chmod(path, 0o600)
    return removed


def read_dotenv(path):
    """Прочитать файл доступов `.env`, заполненный человеком вручную.

    Тот же разборщик, что и для secrets.env (envfile.parse) — второго парсера
    нарочно нет, чтобы значение с пробелом/`$`/бэктиком/кавычкой не сломалось
    здесь так же, как это уже однажды случилось с secrets.env (см.
    tests/test_envfile.py). `.env` лежит внутри рабочей папки, а не в
    ~/.config/uzum-ai — тот же класс данных (пароли в открытом виде), поэтому
    права проверяются и ужимаются до 600 точно так же, как каталог секретов
    ужимается до 700 в write_env.

    Кроме разбора — envfile.lint по тому же тексту: непарный апостроф/кавычка
    в значении, вписанном руками без кавычек вокруг, молча "проглатывает"
    остаток строки (см. envfile.lint). Значение при этом не подменяется —
    только предупреждение с номером строки и именем ключа, решать человеку.

    Возвращает (значения, было_ли_право_сужено, предупреждения_lint).
    Отсутствие файла — не ошибка, как и для envfile.read.
    """
    try:
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return {}, False, []
    tightened = False
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        os.chmod(path, 0o600)
        tightened = True
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return envfile.parse(text), tightened, envfile.lint(text)


def missing_keys(values, required):
    """Обязательные ключи, которых нет в `values` или которые пусты.

    Порядок из `required` сохраняется. Пустая строка считается "не
    заполнено" — ровно та же граница, на которой мастер в интерактивном
    режиме переспрашивает значение, а не молча принимает пустой ввод.
    """
    return [key for key in required if not values.get(key)]


def read_enabled_servers(path):
    """Список одобренных MCP-серверов — обратная сторона write_enabled_servers.

    Тот же файл (`.claude/settings.local.json`), по которому Claude Code
    решает, какие серверы поднимать. Нужен генератору конфига Codex: у Codex
    отдельного гейта нет, поэтому гейтом служит сам config.toml, и список
    включённых обязан быть ОДИН на оба движка — иначе аналитик, настроивший
    только WMS, в каждой сессии Codex получал бы запуск всех девяти серверов.

    Различает два разных состояния, и вызывающий код обязан их различать
    тоже:
      None       — выбор не сделан: файла нет, он битый, или ключа в нём нет
                   (например, `setup.sh` ещё ни разу не отработал);
      []         — выбор сделан, и не включено ничего.
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("enabledMcpjsonServers")
    if not isinstance(servers, list):
        return None
    return [s for s in servers if isinstance(s, str)]


def write_enabled_servers(path, servers):
    """Записать список одобренных MCP-серверов, не трогая прочие настройки."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            data = {}
    data["enabledMcpjsonServers"] = sorted(set(servers))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


# ── Задача Codex-6: движки, доставка конфига Codex, хуки Codex ─────────────
#
# Мастер теперь настраивает Claude Code и/или Codex — какие реально стоят
# на машине. Всё в этом разделе — стандартная библиотека, как и остальной
# файл (setup.sh дёргает его до появления в системе хоть одного внешнего
# пакета).
#
# Доставка конфига Codex — САМОЕ важное решение задачи (докопался
# `docs/codex-facts.md`, факт 1): Codex НЕ читает `.codex/config.toml` из
# папки репозитория сам по себе, только из `$CODEX_HOME`. Рассмотрены и
# проверены живым запуском два варианта (см. отчёт задачи):
#
#   (а) слить наши настройки в $CODEX_HOME/config.toml, аккуратно, не
#       затирая чужое — требует TOML-парсера, чтобы не потерять то, что там
#       уже может быть (доверие другим проектам, настройки других
#       инструментов), а стандартная библиотека Python 3.9 (минимальная
#       версия, см. бриф задачи) TOML не читает вообще (`tomllib` — только
#       3.11+, внешние пакеты сюда запрещены);
#   (б) переключить $CODEX_HOME на папку репозитория — теряет авторизацию,
#       которая физически лежит в $CODEX_HOME/auth.json;
#
# ни один не подошёл. Найден третий, штатный: `codex`/`codex exec`
# документируют флаг `-p/--profile <имя>` — "Layer $CODEX_HOME/<name>.
# config.toml on top of the base user config". Проверено живым запуском
# (изолированный $CODEX_HOME, см. отчёт задачи): файл
# `$CODEX_HOME/uzum.config.toml` со всем содержимым, которое генерирует
# `tools/render_configs.py` (профиль разрешений + mcp_servers), применяется
# ЦЕЛИКОМ через `-p uzum` — даже когда базового `config.toml` нет вовсе
# (`sandbox: custom permissions` в баннере, `.env` недоступен, коннектор из
# профиля виден в списке инструментов) и когда базовый `config.toml`
# существует с посторонним содержимым (доверие другим проектам, другая
# модель по умолчанию) — это содержимое остаётся на диске нетронутым.
# Значит мастеру НЕ НУЖНО ни читать, ни парсить, ни писать базовый
# config.toml вообще — ни TOML-парсер, ни риск затереть чужое.
#
# Профиль — не единственный кусок конфига. Хуки Codex живут в ОТДЕЛЬНОМ
# файле `$CODEX_HOME/hooks.json`, и он НЕ профиль-специфичный (`codex
# --help` не документирует layering по имени профиля для hooks.json,
# только для config.toml) — то есть один hooks.json на ВСЕ проекты,
# которые аналитик когда-либо откроет в Codex. Здесь слепая перезапись
# реально опасна: на машине автора задачи в $CODEX_HOME уже жил чужой
# hooks.json (сторонний инструмент, notify-хук на SessionStart/
# UserPromptSubmit/Stop) — его нужно СЛИТЬ, не заменить.
CODEX_PROFILE_NAME = "uzum"

# Нет PostToolUseFailure — у Codex такого события не существует (проверено
# запуском и статическим анализом бинаря, docs/codex-facts.md, раздел 3):
# PostToolUse у Codex срабатывает и на успехе, и на сбое инструмента, в
# отличие от Claude Code, где PostToolUse при сбое не вызывается вовсе.
CODEX_HOOK_EVENTS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse")


def detect_engines(which):
    """Какие из движков (`claude`, `codex`) есть в PATH этой машины.

    `which` — обязательный параметр, не default `shutil.which`: вызывающий
    код (setup.sh через python3 -c) всегда передаёт настоящий shutil.which
    явно, а тесты — подставной, не завязанный на PATH машины, где гоняются
    тесты (там оба движка, один или ни одного могут стоять одновременно).
    Порядок результата фиксирован (claude, codex), не алфавитной сортировкой
    множества — вывод не должен прыгать между запусками."""
    engines = []
    if which("claude"):
        engines.append("claude")
    if which("codex"):
        engines.append("codex")
    return engines


def engines_setup_plan(available):
    """Что настраивать, по списку обнаруженных движков (см. detect_engines).

    Ни одного — блокирующая ошибка с командами установки ОБОИХ движков
    (аналитик без единого инструмента ничем не воспользуется, и мы не
    гадаем, какой из двух ему нужнее). Один — настраиваем его. Оба —
    настраиваем оба. Возвращает (список_для_настройки, текст_ошибки|None)."""
    if not available:
        return [], (
            "Не найден ни Claude Code, ни Codex — нужен хотя бы один.\n"
            "  Claude Code: https://claude.com/code\n"
            "  Codex:       npm install -g @openai/codex"
        )
    return list(available), None


def select_engine(available, requested=None, remembered=None):
    """Какой движок запускать (для bin/uzum). Приоритет:

      1. Явный аргумент (`--codex`/`--claude`) — всегда побеждает. Если
         запрошенного движка нет на машине — явная ошибка, а не тихий
         откат на другой: иначе аналитик решит, что сессия пошла в Codex,
         хотя на самом деле она ушла в Claude Code.
      2. Запомненный с прошлого раза выбор — если движок всё ещё доступен
         (мог быть удалён с машины со времени последнего запуска).
      3. Единственный настроенный движок — если доступен только один.
      4. Оба доступны, явного выбора и запоминания нет — не решаем молча
         за человека, отдаём (None, "ambiguous"): bin/uzum должен спросить.

    Возвращает (движок|None, причина — код для сообщения/логики вызывающего)."""
    if requested:
        if requested not in available:
            return None, "engine_not_available:%s" % requested
        return requested, "requested"
    if remembered and remembered in available:
        return remembered, "remembered"
    if len(available) == 1:
        return available[0], "only_configured"
    if not available:
        return None, "none_available"
    return None, "ambiguous"


def should_remember_engine_choice(reason):
    """Стоит ли ПЕРЕЗАПИСАТЬ запомненный выбор движка (bin/uzum) после
    select_engine(). Находка ревью задачи Codex-6: bin/uzum сохранял выбор
    в файл при ЛЮБОМ `reason`, включая "requested" — из-за этого разовый
    `uzum --codex` при обычно используемом Claude Code молча переписывал
    дефолт для всех следующих запусков без флага, хотя комментарий рядом
    прямо говорил, что так делать нельзя (код и комментарий разошлись).

    Правильно — сохранять ТОЛЬКО когда выбор был только что сделан человеком
    в развилке "оба движка, ничего не решено" (`reason == "ambiguous"`,
    после интерактивного вопроса). Явный флаг — одноразовый выбор для этого
    запуска, не должен трогать дефолт. "remembered" уже лежит в файле —
    перезаписывать нечего. "only_configured" и коды ошибок — там нет
    неоднозначности, которую стоило бы запоминать."""
    return reason == "ambiguous"


def read_remembered_engine(path):
    """Запомненный выбор движка человеком (bin/uzum, оба движка настроены).
    Отсутствие файла — не ошибка, просто "ничего не запомнено"."""
    try:
        with open(path, encoding="utf-8") as f:
            value = f.read().strip()
    except OSError:
        return None
    return value or None


def write_remembered_engine(path, engine):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(engine + "\n")


def codex_home():
    """$CODEX_HOME, если аналитик его переопределил явно, иначе дефолт
    самого Codex — ~/.codex (docs/codex-facts.md)."""
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


# Первая строка каждого файла профиля, который пишет мастер — маркер
# происхождения (находка ревью №5). hooks.json сливается бережно (структура
# понятна — JSON по ключам), но профиль config.toml — непрозрачный текстовый
# блок, который мы и так генерируем целиком; "слияние" для него не имеет
# смысла, а асимметрия "хуки бережём, профиль просто затираем" ничем не
# обоснована — оба файла в равной мере могут оказаться чужими (имя профиля
# "uzum" — не невозможное совпадение с чужим инструментом или ручной
# настройкой аналитика). Файл БЕЗ этого маркера считается чужим: не наш,
# трогать/сравнивать по содержимому нельзя — только переименовать в сторону
# и написать своё рядом, не молча стерев.
#
# Доработка по ревью (мелкая, не обязательная, но дешёвая): признак — не
# просто человекочитаемое предложение на английском/русском, а ещё и
# фиксированный UUID внутри него. Человекочитаемый текст один в один могло
# бы совпасть с чужим файлом чисто случайно (маловероятно, но не исключено —
# кто-то мог сам начать файл с похожего комментария); строка со случайным
# на вид UUID внутри — нет, коллизия по чистой случайности практически
# невозможна. UUID зашит константой (не генерируется заново при каждом
# запуске) — иначе идемпотентность (deploy_codex_profile сравнивает файл с
# прошлого запуска байт в байт) сломалась бы сама по себе.
CODEX_PROFILE_MARKER = (
    "# uzum-data-agent:profile-marker:f47ac10b-58cc-4372-a567-0e02b2c3d479 "
    "— сгенерировано автоматически из connectors/registry.py. "
    "Правки руками потрёт следующий ./setup.sh.\n"
)


def deploy_codex_profile(generated_config_toml_path, codex_home_dir,
                          profile_name=CODEX_PROFILE_NAME):
    """Доставить .codex/config.toml (уже сгенерированный tools/render_
    configs.py из connectors/registry.py — "существующий генератор" из
    брифа задачи) туда, где Codex его реально видит: именованным профилем,
    `$CODEX_HOME/<profile>.config.toml` (см. докстринг раздела выше).

    НЕ трогает $CODEX_HOME/config.toml (базовый) вообще — ни на чтение, ни
    на запись: там может быть чужое (доверие другим проектам, настройки
    другого инструмента), и оно там и останется. Пишет, только если
    содержимое реально изменилось (та же экономия mtime, что и в
    tools/render_configs.py::_write).

    Файл профиля с ТЕМ ЖЕ ИМЕНЕМ уже на диске, но БЕЗ нашего маркера в
    первой строке, — не наш файл: не перезаписывается молча, а переносится
    рядом (`<profile>.config.toml.foreign-backup-<unix-время>`) перед тем,
    как мы напишем свой. Ничего чужого не пропадает.

    Возвращает (изменился_ли_файл: bool, путь_бэкапа_чужого|None)."""
    os.makedirs(codex_home_dir, exist_ok=True)
    target = os.path.join(codex_home_dir, "%s.config.toml" % profile_name)
    with open(generated_config_toml_path, encoding="utf-8") as f:
        content = CODEX_PROFILE_MARKER + f.read()

    current = None
    if os.path.exists(target):
        with open(target, encoding="utf-8") as f:
            current = f.read()

    if current == content:
        return False, None

    backed_up_to = None
    if current is not None and not current.startswith(CODEX_PROFILE_MARKER):
        backed_up_to = "%s.foreign-backup-%d" % (target, int(time.time()))
        os.rename(target, backed_up_to)

    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return True, backed_up_to


# Метка, по которой наши записи опознаются в общем $CODEX_HOME/hooks.json.
#
# hooks.json — файл на все проекты и все инструменты аналитика: рядом с нашими
# записями там живут чужие (на машине автора задачи —
# `SUPERSET_AGENT_ID=codex ".../.superset/hooks/notify.sh"`). Предикат,
# решающий «эта запись наша», управляет УДАЛЕНИЕМ из этого файла, поэтому он
# обязан быть однозначным: ошибётся в одну сторону — не дотянемся до уже
# установленных машин, ошибётся в другую — молча снесём чужой инструмент.
#
# Предыдущая версия опознавала запись по имени скрипта (log_event.py) плюс
# подстроке ".claude/hooks/" в команде. Оба признака родовые: `.claude/hooks/`
# — штатный каталог Claude Code, а `log_event.py` — предельно общее имя.
# Команда чужого инструмента, следующего той же конвенции (а конвенция ровно
# одна, и мы сами ей следуем), опознавалась как наша:
#
#     python3 ~/other-tool/.claude/hooks/log_event.py    -> съедалась
#
# Поэтому опознание больше не угадывает. Свои записи мы ПОМЕЧАЕМ: команда
# запускает скрипт через `env UZUM_DATA_AGENT_HOOK=1`. Метка называет
# инструмент по имени, случайно её не напишет никто, и она переживает любую
# будущую смену интерпретатора, обёртки, флагов и пути. Заодно её видно в
# диалоге доверия Codex — аналитику ясно, чей это хук.
OUR_HOOK_MARKER = "UZUM_DATA_AGENT_HOOK"


def codex_hook_definitions(repo_root=None):
    """Наши записи для $CODEX_HOME/hooks.json — те же скрипты, что уже
    пишет Claude Code (.claude/hooks/log_event.py, .claude/hooks/
    log_session.py): lib/hook_payload.py и lib/transcript_codex.py уже
    умеют оба движка (задача Codex-4), отдельного кода для Codex не нужно.

    on_session_start.sh (обновление репозитория на старте сессии) раньше сюда
    не включался: он возвращал Claude-специфичный `hookSpecificOutput`, а его
    совместимость с Codex никто не проверял запуском. Из-за этого сессия
    Codex не делала `git pull` вообще — аналитик месяцами работал бы на
    скиллах из дня установки и не узнал бы об этом. Проверено живым запуском
    (docs/codex-facts.md, раздел 9): у Codex есть событие `SessionStart`, наш
    хук на нём реально выполняется, `git pull --ff-only` реально
    подтягивает коммит, а обычный текст из stdout хука долетает до модели
    дословно. Поэтому скрипт зарегистрирован и здесь — с флагом `--plain`,
    который переключает вывод с Claude-формата на обычный текст.

    Команда — АБСОЛЮТНЫЙ путь к скриптам того клона, который сейчас
    ставится (`repo_root`, по умолчанию — корень клона, которому принадлежит
    этот файл; setup.sh подключает lib именно из своего клона).

    Раньше здесь стоял относительный путь (`python3 .claude/hooks/
    log_event.py`) — и этот докстринг утверждал, что в чужом проекте файла по
    такому пути не окажется, хук "тихо не сработает", и это и есть желаемое
    поведение. Живой запуск это ОПРОВЕРГ (docs/codex-facts.md, раздел 11).
    hooks.json у Codex — файл на весь $CODEX_HOME, общий для ВСЕХ проектов,
    которые аналитик когда-либо откроет в Codex (в отличие от профиля
    config.toml выше, который подключается явно флагом -p). В постороннем
    каталоге, где `.claude/hooks/` нет, отсутствие файла даёт не тишину, а
    `python3` с ненулевым кодом возврата, и Codex читает это как отказ хука:

        hook: SessionStart Failed
        hook: UserPromptSubmit Blocked

    `Blocked` на UserPromptSubmit означает, что промпт не доходит до модели
    вообще: аналитик ставит наш инструмент и получает нерабочий Codex во всех
    остальных своих проектах.

    Намерение осталось прежним — хуки работают в нашем репозитории и не
    вмешиваются больше нигде, — но выражено оно теперь там, где может быть
    выражено честно: абсолютный путь гарантирует, что файл на месте и
    ненулевого кода не будет, а «не наша сессия» скрипты определяют сами и
    выходят нулём мгновенно и молча (lib/hook_scope.py; тот же признак —
    рабочий каталог — работает и для Claude Code, где он безвредный no-op).
    Абсолютные пути в $CODEX_HOME/hooks.json — уже сложившаяся практика:
    посторонний инструмент, прописавший туда себя, использует именно их.

    shlex.quote — потому что команда исполняется шеллом, а путь к клону
    аналитика может содержать пробел (`~/My Projects/uzum-data-agent`).
    Машинно-зависимый путь остаётся только в $CODEX_HOME/hooks.json, которого
    нет в git.

    `test -f … && exec … || exit 0` — потому что абсолютный путь сам по себе
    не гарантирует, что файл на месте (находка повторного ревью). Аналитик
    может перенести, переименовать или удалить папку репозитория:
    $CODEX_HOME/hooks.json при этом не меняется, `bin/uzum` его не
    передеплоивает (это делает только setup.sh) — и мы возвращаемся ровно к
    тому же `UserPromptSubmit Blocked`, только через другой вход, причём во
    ВСЕХ проектах разом, включая сам переехавший репозиторий, то есть починить
    изнутри инструмента уже нельзя. С этой обёрткой единственная точка отказа
    исчезает: нет файла — шелл выходит нулём, ничего не напечатав. Проверено
    живым запуском (docs/codex-facts.md, раздел 11): команда действительно
    исполняется шеллом, `&&`/`||`/`exec` работают, `Blocked` не появляется.
    `exec` — чтобы не плодить лишний процесс на каждое событие хука.

    Значение на событие — СПИСОК записей, а не одна: у SessionStart их две
    (телеметрия и обновление репозитория), и по одному скрипту на запись, а
    не обе команды внутри одной — так каждую запись видно и в hooks.json, и в
    диалоге доверия по отдельности. Прежние наши записи merge_codex_hooks
    вытесняет (см. OUR_HOOK_MARKER и LEGACY_HOOK_FORMS), поэтому смена команды
    здесь не плодит дубликаты на уже установленных машинах.

    `env UZUM_DATA_AGENT_HOOK=1` — метка, по которой запись опознаётся как
    наша (см. OUR_HOOK_MARKER). Она не влияет на работу скрипта: переменная
    просто есть в его окружении. Меняя команду здесь, метку не теряй — на ней
    держится вытеснение прежних записей (tests/test_setup_helpers.py::
    test_every_command_we_write_carries_our_marker).

    Смена команды меняет `trusted_hash` (docs/codex-facts.md, раздел 7):
    у того, у кого Codex уже настроен, диалог «Hooks need review» появится
    ещё раз. Это штатно и происходит ровно тогда, когда команда реально
    поменялась — то есть при обновлении этой функции и при `./setup.sh`,
    запущенном из нового места. Сам по себе переезд папки hooks.json не
    трогает: диалога не будет, хуки просто перестанут находить файл и
    (благодаря `test -f`) молча выйдут нулём."""
    root = repo_root or hook_scope.repo_root()

    def entry(runner, script, args=""):
        path = shlex.quote(os.path.join(root, ".claude", "hooks", script))
        command = "test -f %s && exec env %s=1 %s %s%s || exit 0" % (
            path, OUR_HOOK_MARKER, runner, path, args)
        return {"hooks": [{"type": "command", "command": command}]}

    def telemetry(script):
        return entry("python3", script)

    return {
        "SessionStart": [
            telemetry("log_session.py"),
            entry("bash", "on_session_start.sh", " --plain"),
        ],
        "SessionEnd": [telemetry("log_session.py")],
        "UserPromptSubmit": [telemetry("log_event.py")],
        "PostToolUse": [telemetry("log_event.py")],
    }


# Все формы команды, которыми мы писали хуки ДО появления метки. Перечень
# исторический и закрытый: он описывает прошлое, а не настоящее. Новые скрипты
# и новые формы сюда НЕ добавляются — их опознаёт метка (OUR_HOOK_MARKER).
#
# Зачем перечень вообще: на уже установленных машинах метки нет — там лежит
# ровно то, что писали прежние версии codex_hook_definitions(). Пока
# merge_codex_hooks только ДОБАВЛЯЛ записи, после `./setup.sh` в hooks.json
# оставались обе разом — старая и новая, и живой Codex давал
#
#     hook: UserPromptSubmit
#     hook: UserPromptSubmit
#     hook: UserPromptSubmit Blocked
#     hook: UserPromptSubmit Completed
#
# то есть починка не доезжала ровно до тех, ради кого дефект и заводили, а в
# нашем репозитории каждое событие обрабатывалось дважды.
#
# (скрипт, интерпретатор, аргументы) — то, что перечислено ниже, менялось
# вместе с формой команды; формы собирает legacy_hook_commands().
LEGACY_HOOK_FORMS = (
    ("log_session.py", "python3", ""),
    ("log_event.py", "python3", ""),
    ("on_session_start.sh", "bash", " --plain"),
)


def legacy_hook_commands(repo_root=None):
    """Дословный перечень команд, которые писали прежние версии
    codex_hook_definitions() для клона `repo_root`. Совпадение с записью в
    hooks.json проверяется по ВСЕЙ строке, а не по подстроке.

    Три формы, все три видны в `git log` по codex_hook_definitions:

    1. относительный путь (4b81b8c … 6764f04) — `python3
       .claude/hooks/log_event.py`. Это та форма, что стоит у всей
       установленной базы;
    2. абсолютный путь (f2761fc) — `python3 <корень>/.claude/hooks/log_event.py`;
    3. абсолютный путь в обёртке (7a59c6f, 90c2905) — `test -f … && exec … ||
       exit 0`.

    Формы 2 и 3 опознаются только для того клона, который сейчас ставится:
    `python3 <любой корень>/.claude/hooks/log_event.py` — команда, которую с
    тем же успехом мог написать чужой инструмент (`.claude/hooks/` — штатный
    каталог Claude Code, `log_event.py` — родовое имя), и отличить её от нашей
    по тексту нельзя. Своё узнаём по корню, чужое не трогаем. Практическая
    цена: если аналитик поставил инструмент из ОДНОГО клона, а `./setup.sh`
    запустил из ДРУГОГО (переименовал или перенёс папку), запись прежней формы
    останется висеть. Форму 3 это не ломает (`test -f` не находит файл, шелл
    выходит нулём), формы 1 и 2 до внешних машин не доехали — они появились
    уже после того, как установленная база была собрана. Дальше этой проблемы
    нет вовсе: метка от пути не зависит.

    Путь проходит через тот же shlex.quote, что и в codex_hook_definitions —
    иначе у аналитика с пробелом в пути перечень не совпал бы с файлом.
    realpath — потому что в hooks.json мог попасть путь через симлинк
    (на macOS /tmp против /private/tmp), а setup.sh запускается по
    разрешённому; сравниваем и так, и так."""
    roots = [repo_root or hook_scope.repo_root()]
    resolved = os.path.realpath(roots[0])
    if resolved != roots[0]:
        roots.append(resolved)

    commands = []
    for script, runner, args in LEGACY_HOOK_FORMS:
        commands.append("%s .claude/hooks/%s%s" % (runner, script, args))
        for root in roots:
            path = shlex.quote(os.path.join(root, ".claude", "hooks", script))
            commands.append("%s %s%s" % (runner, path, args))
            commands.append("test -f %s && exec %s %s%s || exit 0" % (
                path, runner, path, args))
    return tuple(commands)


def is_our_codex_hook(hook, repo_root=None):
    """Наш ли это ХУК (не запись целиком) из hooks.json: наш, если несёт метку
    или дословно совпадает с одной из прежних наших команд.

    Проверка идёт на уровне хука, потому что на этом же уровне идёт и
    удаление: в одной записи hooks.json может лежать несколько команд, и
    чужая, оказавшаяся в одной записи с нашей, не должна исчезнуть вместе с
    ней."""
    if not isinstance(hook, dict):
        return False
    command = hook.get("command")
    if not isinstance(command, str):
        return False
    if OUR_HOOK_MARKER in command:
        return True
    return command in legacy_hook_commands(repo_root)


def _without_our_hooks(group, repo_root):
    """Запись hooks.json без наших хуков. None — если после этого в ней не
    осталось ни одного хука (такую запись держать в файле незачем).
    Всё чужое — команды, их порядок и остальные поля записи — как было."""
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return group  # не наш формат — не наша забота, не трогаем
    kept = [hook for hook in group["hooks"]
            if not is_our_codex_hook(hook, repo_root)]
    if len(kept) == len(group["hooks"]):
        return group
    if not kept:
        return None
    pruned = dict(group)
    pruned["hooks"] = kept
    return pruned


def merge_codex_hooks(existing, new_entries, repo_root=None):
    """Слить наши записи в уже существующую структуру hooks.json, не теряя
    чужие. `existing` — распарсенный JSON (dict) текущего hooks.json, или
    {}/None, если файла ещё не было. `new_entries` — событие → список наших
    записей (см. codex_hook_definitions). `repo_root` — корень клона, который
    ставится; тот же, с которым построены new_entries (см.
    legacy_hook_commands про то, зачем он нужен здесь).

    Наши прежние хуки ВЫТЕСНЯЮТСЯ, а не дополняются (см. OUR_HOOK_MARKER и
    LEGACY_HOOK_FORMS — там же про то, чем это кончалось). Убирается ровно наш
    хук; запись выбрасывается, только если после этого в ней ничего не
    осталось. Порядок: сначала чужие записи в том порядке, в каком они лежали,
    следом наши. Отсюда идемпотентность — уже не «повторная запись случайно
    совпала по значению», а свойство самого построения: сколько раз ни вызови,
    наших записей ровно столько, сколько их в new_entries.

    Событие, где после вытеснения не осталось ничего, из файла убирается: так
    исчезнет и запись о событии, которое мы перестали регистрировать."""
    merged = dict(existing) if existing else {}
    hooks = dict(merged.get("hooks") or {})

    # Порядок событий фиксирован (сначала уже лежащие в файле, потом наши
    # новые) — иначе ключи JSON прыгали бы между запусками, файл переписывался
    # бы без причины, а вместе с ним менялся бы trusted_hash и Codex каждый
    # раз переспрашивал бы «Hooks need review».
    events = list(hooks)
    events.extend(event for event in new_entries if event not in hooks)

    result = {}
    for event in events:
        groups = []
        for group in (hooks.get(event) or []):
            kept = _without_our_hooks(group, repo_root)
            if kept is not None:
                groups.append(kept)
        groups.extend(new_entries.get(event, []))
        if groups:
            result[event] = groups

    merged["hooks"] = result
    return merged


def deploy_codex_hooks(codex_home_dir, repo_root=None):
    """Записать/дополнить $CODEX_HOME/hooks.json нашими хуками, не трогая
    чужие записи (см. докстринг раздела выше — на машине автора задачи там
    уже жил hooks.json стороннего инструмента). Битый существующий файл
    (не наш формат, не наша забота чинить) не роняет установку — честнее
    переписать его нашими хуками, чем упасть на разборе чужого JSON.
    `repo_root` — корень клона, чьи скрипты прописываем (см.
    codex_hook_definitions); по умолчанию наш собственный.
    Возвращает True, если файл реально изменился."""
    os.makedirs(codex_home_dir, exist_ok=True)
    path = os.path.join(codex_home_dir, "hooks.json")
    existing = {}
    current_text = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            current_text = f.read()
        try:
            existing = json.loads(current_text)
        except ValueError:
            existing = {}
    merged = merge_codex_hooks(existing, codex_hook_definitions(repo_root),
                               repo_root)
    new_text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    if current_text == new_text:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True
