#!/usr/bin/env bash
# Мастер установки: спрашивает доступы и проверяет каждый живым запросом.
#
# Запуск:
#   ./setup.sh              — полный мастер, все коннекторы за один заход
#   ./setup.sh --add NAME   — настроить/переподключить один коннектор,
#                             не трогая остальные (clickhouse-wms,
#                             clickhouse-dwh, atlassian, superset, trino,
#                             grafana, openmetadata, growthbook, sheets),
#                             либо телеметрию: ./setup.sh --add telemetry
#   ./setup.sh --non-interactive
#                           — без единого вопроса: значения только из .env,
#                             по недостающим — список и выход с кодом 1
#
# Заполнить доступы файлом вместо вопросов: cp .env.example .env, впиши
# значения, запусти ./setup.sh как обычно — он найдёт .env сам (или файл по
# пути в $UZUM_ENV_FILE, если он лежит не в этой папке). Заполненные значения
# не спрашиваются, но живая проверка всё равно выполняется и печатается —
# смысл мастера не в том, чтобы просто принять значения, а в том, чтобы
# показать, что каждый доступ реально работает. Что не заполнено — спросится
# как обычно.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="$HOME/.config/uzum-ai/secrets.env"
SETTINGS_LOCAL="$REPO_DIR/.claude/settings.local.json"
SERVERS_LIST="clickhouse-wms clickhouse-dwh atlassian superset trino grafana openmetadata growthbook sheets"

# Профиль, которым Codex подключает наш конфиг: `codex -p uzum` /
# `codex exec -p uzum`, слоем поверх $CODEX_HOME/config.toml — тот же
# CODEX_PROFILE_NAME, что и в lib/setup_helpers.py (держи в синхроне).
CODEX_PROFILE_NAME="uzum"

# Заполняется определением движков ниже (check_environment). Двумя
# отдельными флагами, не массивом — на пустом массиве под `set -u` bash 3.2
# роняет скрипт (см. комментарий про ENABLED ниже), а тут значения читаются
# ещё до того, как массивы вообще стоило бы заводить.
ENGINE_CLAUDE_FOUND=0
ENGINE_CODEX_FOUND=0

# Заполнено из .env одним из ask_*-хелперов ниже, но не введено человеком и
# отсутствует в файле — только при --non-interactive (иначе просто спросили
# бы). Список меток, а не имён переменных: тут читает человек.
MISSING=()
NONINTERACTIVE=0
ENV_FILE=""

# Коннекторы, включённые за этот запуск. Bash 3.2 (дефолтный на macOS) роняет
# скрипт с "unbound variable" на "${ENABLED[@]}"/"${ENABLED[*]}", если массив
# пустой и включён `set -u` — поэтому везде ниже массив разворачивается только
# через `[ "${#ENABLED[@]}" -gt 0 ]` или через явный псевдо-дефолт `:-`.
ENABLED=()

# Настраивается мастером, но не является MCP-сервером и не попадает в
# enabledMcpjsonServers: телеметрия и доставка Codex-хуков — это хуки и
# конфиг, а не коннектор.
EXTRAS_LIST="telemetry codex-hooks"

# Смоук-тест складского ClickHouse (WMS) прошёл в этом запуске
# (setup_clickhouse_wms). Нужен setup_telemetry: переиспользовать логин/пароль
# можно только если они на этом хосте реально сработали, а не просто были
# введены. Общий DWH (setup_clickhouse_dwh) сюда не годится — таблиц
# sandbox.ai_usage_* там нет, см. комментарий в setup_telemetry.
CH_WMS_OK=0

say()  { printf "\n%s\n" "$1"; }
ok()   { printf "  \xe2\x9c\x93 %s\n" "$1"; }
fail() { printf "  \xe2\x9c\x97 %s\n" "$1"; }
# Просто факт, не «хорошо» и не «плохо»: например, что второй движок на
# машине не стоит — это штатная ситуация, а не ошибка (✗ читается как ошибка).
note() { printf "  \xc2\xb7 %s\n" "$1"; }

# Временные файлы смоук-проверок: непредсказуемые имена (mktemp, не
# фиксированные /tmp/uzum_*) и гарантированная уборка при выходе — даже при
# early exit из проверки окружения. Тела ответов (email, displayName и т.п.)
# не должны переживать сам процесс setup.sh.
TMP_FILES=()
mk_tmp() {
  local f
  f=$(mktemp "${TMPDIR:-/tmp}/uzum-setup.XXXXXX") || { fail "не удалось создать временный файл"; exit 1; }
  TMP_FILES+=("$f")
  printf "%s" "$f"
}
cleanup_tmp() {
  if [ "${#TMP_FILES[@]}" -gt 0 ]; then
    rm -f "${TMP_FILES[@]}"
  fi
}
trap cleanup_tmp EXIT

# Живой запрос без секрета в argv: заголовки уходят в curl через конфиг на
# стандартном вводе (--config -), а не через -H аргумент — иначе пароль/токен
# на время запроса виден в выводе `ps` любому процессу того же пользователя.
# $1 = файл для тела ответа, $2 = max-time, $3 = url, $4.. = заголовки
# в формате "Имя: значение".
curl_check() {
  local out="$1" maxtime="$2" url="$3" cfg="" h esc
  shift 3
  for h in "$@"; do
    esc="${h//\\/\\\\}"
    esc="${esc//\"/\\\"}"
    cfg="$cfg
header = \"$esc\""
  done
  # stderr curl'а (например "could not resolve host") в терминал не идёт —
  # для диагностики отказа хватает http_code и тела ответа в $out.
  printf '%s\n' "$cfg" | curl -sS --max-time "$maxtime" -o "$out" -w "%{http_code}" --config - "$url" 2>/dev/null
}

# Значение уходит в python не через подстановку в исходный код (это ломается
# на паролях с кавычками), а через переменную окружения — python читает её
# через os.environ, шелл ничего не интерполирует внутрь строки.
put_env() {
  PUT_ENV_KEY="$1" PUT_ENV_VALUE="$2" python3 -c "
import os, sys
sys.path.insert(0, '$REPO_DIR/lib')
import setup_helpers
setup_helpers.write_env('$SECRETS', {os.environ['PUT_ENV_KEY']: os.environ['PUT_ENV_VALUE']})
"
}

# Пишет итоговый список включённых серверов, объединяя с уже включёнными
# раньше (а не затирая их) — иначе ./setup.sh --add одного коннектора молча
# выключил бы все остальные, настроенные в прошлый раз.
write_enabled() {
  local existing joined x
  existing="$(python3 -c "
import json, os
p = '$SETTINGS_LOCAL'
if os.path.exists(p):
    try:
        data = json.load(open(p, encoding='utf-8'))
        print(chr(10).join(data.get('enabledMcpjsonServers', [])))
    except Exception:
        pass
")"
  joined="$existing"
  if [ "${#ENABLED[@]}" -gt 0 ]; then
    for x in "${ENABLED[@]}"; do
      joined="$joined
$x"
    done
  fi
  UZUM_SERVERS="$joined" python3 -c "
import os, sys
sys.path.insert(0, '$REPO_DIR/lib')
import setup_helpers
servers = [s for s in os.environ.get('UZUM_SERVERS', '').split(chr(10)) if s.strip()]
setup_helpers.write_enabled_servers('$SETTINGS_LOCAL', servers)
"
}

# ── Заполнение доступов файлом (.env) вместо вопросов ───────────────────
#
# Ищем .env: сперва $UZUM_ENV_FILE (человек может держать файл вне рабочей
# папки), потом .env в корне репозитория. Нет ни того, ни другого — ничего
# не меняется, ведём себя ровно как раньше.
find_env_file() {
  if [ -n "${UZUM_ENV_FILE:-}" ]; then
    if [ -f "$UZUM_ENV_FILE" ]; then
      printf "%s" "$UZUM_ENV_FILE"
    else
      fail "UZUM_ENV_FILE указывает на несуществующий файл: $UZUM_ENV_FILE" >&2
    fi
    return
  fi
  if [ -f "$REPO_DIR/.env" ]; then
    printf "%s" "$REPO_DIR/.env"
  fi
}

# Читает .env тем же разборщиком, что и secrets.env (lib/envfile.py — второго
# парсера нет, см. lib/setup_helpers.read_dotenv), поднимает права до 600,
# если они были шире, и печатает найденные значения в формате KEY='value'
# (envfile.format_line — та же функция, что пишет write_env). Эти строки
# ниже читаются построчно и заводятся в переменные через read+eval, а не
# просто `source .env` целиком: .env написан руками и может содержать что
# угодно, включая бэктики — их нельзя отдавать шеллу на интерпретацию,
# значение должно остаться литеральным текстом.
load_env_file() {
  local path="$1" tightened_marker warnings_marker line key
  tightened_marker="$(mk_tmp)"
  warnings_marker="$(mk_tmp)"
  UZUM_DOTENV_PATH="$path" UZUM_TIGHTENED_MARKER="$tightened_marker" \
  UZUM_WARNINGS_MARKER="$warnings_marker" python3 -c "
import os, sys
sys.path.insert(0, '$REPO_DIR/lib')
import envfile, setup_helpers
values, tightened, warnings = setup_helpers.read_dotenv(os.environ['UZUM_DOTENV_PATH'])
if tightened:
    open(os.environ['UZUM_TIGHTENED_MARKER'], 'w').write('1')
if warnings:
    with open(os.environ['UZUM_WARNINGS_MARKER'], 'w', encoding='utf-8') as wf:
        for line_no, key in warnings:
            wf.write('%d\t%s\n' % (line_no, key))
for k, v in values.items():
    sys.stdout.write(envfile.format_line(k, v))
"
  # >&2, не в стандартный вывод: вызывающий код перенаправляет stdout
  # load_env_file в файл, который потом `source`-ит — сообщение сюда же
  # испортило бы этот файл (была найдена именно так: "line N: ✗: command
  # not found" при source).
  if [ -s "$tightened_marker" ]; then
    fail "права на $path были шире 600 — исправил" >&2
  fi
  # Непарный апостроф/кавычка вне кавычек (envfile.lint, вызывается внутри
  # read_dotenv): значение не подменяем, только предупреждаем — иначе
  # человек увидит совсем не связанную с опечаткой ошибку вида "✗ токен не
  # принят (код: 401)" на шаге живой проверки и пойдёт перевыпускать токен,
  # хотя дело в .env.
  if [ -s "$warnings_marker" ]; then
    while IFS="$(printf '\t')" read -r line key; do
      [ -n "$key" ] && fail "$path:$line: в значении $key не закрыт апостроф/кавычка — дальше по файлу могло съесть не то. Оберни всё значение в двойные кавычки." >&2
    done <"$warnings_marker"
  fi
}

# ask VAR "приглашение" ["дефолт"]
# ask_secret VAR "приглашение" [обязателен: метка для --non-interactive]
#
# shellcheck (если запущен на этом файле) пометит `read -r "$__var"` как
# SC2229 и использование $CH_WMS_USER/$CH_WMS_PASSWORD дальше по файлу как
# SC2153 —
# оба ложные срабатывания: shellcheck не умеет проследить чтение в
# переменную по имени, переданному строкой. Это стандартный приём для
# bash 3.2 (дефолтный на macOS, где нет `local -n`/namerefs) — `read` внутри
# вложенной функции пишет в переменную вызывающей функции благодаря
# динамической области видимости bash, без eval. Проверено вручную:
# ask вызывается из setup_clickhouse_wms/setup_clickhouse_dwh (CH_WMS_HOST,
# CH_DWH_HOST и т.д. — глобальные) и из setup_telemetry (T_HOST и т.д. —
# local), в обоих случаях значение долетает до вызывающей функции.
#
# Значение уже есть (из .env, или переиграно из другого коннектора, как
# TELEMETRY_* от складского ClickHouse, CH_WMS_*) — не спрашиваем, используем
# как есть;
# живая проверка ниже по коду всё равно выполнится по-настоящему. Значения
# нет и вопросов не задаём (--non-interactive) — просто оставляем пустым:
# позвать ask_required/ask_required_secret с меткой, если пустое значение
# должно попасть в отчёт "чего не хватает".
#
# `read -r VAR <<< ""` в ветке --non-interactive, а не просто return: скрипт
# работает под `set -u`, а VAR до этого места вообще не присвоена (это не
# то же самое, что присвоена пустой строкой) — код ниже, который читает
# "$VAR" (например `if [ -z "$GRAFANA_URL" ]`), уронил бы скрипт с "unbound
# variable" на самом первом коннекторе без .env-значения. Такое же
# присваивание по имени переменной, что и во всех ask*, без eval.
ask() {
  local __var="$1" __prompt="$2"
  if [ -n "${!__var:-}" ]; then
    return
  fi
  if [ "$NONINTERACTIVE" = "1" ]; then
    read -r "$__var" <<< ""
    return
  fi
  read -rp "$__prompt" "$__var"
}

ask_secret() {
  local __var="$1" __prompt="$2"
  if [ -n "${!__var:-}" ]; then
    return
  fi
  if [ "$NONINTERACTIVE" = "1" ]; then
    read -r "$__var" <<< ""
    return
  fi
  read -rsp "$__prompt" "$__var"; echo
}

# Для значений, без которых конкретный шаг живой проверки не имеет смысла
# (например, JIRA_TOKEN) — если и .env, и вопрос недоступны
# (--non-interactive), значение попадает в MISSING для итогового отчёта.
ask_required() {
  local __var="$1" __prompt="$2" __label="$3"
  if [ -n "${!__var:-}" ]; then
    return
  fi
  if [ "$NONINTERACTIVE" = "1" ]; then
    MISSING+=("$__label")
    read -r "$__var" <<< ""
    return
  fi
  read -rp "$__prompt" "$__var"
}

ask_required_secret() {
  local __var="$1" __prompt="$2" __label="$3"
  if [ -n "${!__var:-}" ]; then
    return
  fi
  if [ "$NONINTERACTIVE" = "1" ]; then
    MISSING+=("$__label")
    read -r "$__var" <<< ""
    return
  fi
  read -rsp "$__prompt" "$__var"; echo
}

check_environment() {
  say "Проверяю окружение…"

  # Движок теперь необязательно один: Claude Code остаётся, Codex
  # добавляется. Мастер настраивает то, что реально стоит на машине — один,
  # оба, или блокирующая ошибка, если нет ни одного (см. ниже, после
  # остальных проверок окружения — команды установки понятнее печатать
  # рядом, а не раньше python3/curl/uv, без которых мастер всё равно
  # бесполезен).
  #
  # Отсутствие ОДНОГО из двух движков — не ошибка, а штатная ситуация:
  # аналитик мог осознанно поставить только Codex или только Claude Code.
  # Раньше это печаталось красным ✗ и в первый час читалось как «что-то не
  # так» — теперь строкой-заметкой, а ✗ остаётся там, где он и правда
  # означает ошибку: когда нет ни одного движка.
  command -v claude >/dev/null 2>&1 && ENGINE_CLAUDE_FOUND=1
  command -v codex  >/dev/null 2>&1 && ENGINE_CODEX_FOUND=1

  if [ "$ENGINE_CLAUDE_FOUND" = "1" ]; then
    ok "claude найден"
  elif [ "$ENGINE_CODEX_FOUND" = "1" ]; then
    note "Claude Code в PATH нет — настраиваю только Codex"
  fi
  if [ "$ENGINE_CODEX_FOUND" = "1" ]; then
    ok "codex найден"
  elif [ "$ENGINE_CLAUDE_FOUND" = "1" ]; then
    note "Codex в PATH нет — настраиваю только Claude Code"
  fi
  if [ "$ENGINE_CLAUDE_FOUND" = "0" ] && [ "$ENGINE_CODEX_FOUND" = "0" ]; then
    fail "не найден ни один движок — нужен хотя бы один"
    fail "Claude Code: https://claude.com/code"
    fail "Codex:       npm install -g @openai/codex"
    exit 1
  fi

  if command -v python3 >/dev/null 2>&1; then
    ok "python3 найден"
  else
    fail "python3 не найден — без него не отработает ни одна проверка доступа"
    fail "Поставь: xcode-select --install (или brew install python3) — и запусти ./setup.sh заново"
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    ok "curl найден"
  else
    fail "curl не найден — без него мастер не может проверить ни один доступ"
    fail "Поставь: brew install curl — и запусти ./setup.sh заново"
    exit 1
  fi
  # uv обязателен: коннекторы trino/superset/sheets запускаются через
  # `uv run`, остальные пять — через `uvx`. Без uv не поднимется ни один.
  if command -v uv >/dev/null 2>&1 && command -v uvx >/dev/null 2>&1; then
    ok "uv найден"
  else
    fail "uv не найден — без него не поднимется ни один из восьми коннекторов"
    fail "Поставь: curl -LsSf https://astral.sh/uv/install.sh | sh — и запусти ./setup.sh заново"
    exit 1
  fi
  # mcp-grafana — пакет есть на PyPI (bin/mcp-grafana внутри wheel — тот же
  # Go-бинарь, что ставит `brew install mcp-grafana`), поэтому `.mcp.json`
  # запускает его как `uvx mcp-grafana`, как остальные пять серверов через
  # `uvx`: отдельной проверки бинаря тут не нужно — её делает проверка `uv`
  # выше, `uvx` сам скачает пакет при первом запуске.
  # npx нужен только growthbook: официальный сервер GrowthBook — npm-пакет
  # @growthbook/mcp (node >= 18), питоновского аналога нет.
  if command -v npx >/dev/null 2>&1; then
    ok "npx найден"
  else
    fail "npx не найден — коннектор growthbook не поднимется. Поставь Node.js 18+: brew install node"
  fi
  if pgrep -x netbird >/dev/null 2>&1; then
    ok "Netbird запущен"
  else
    fail "Netbird не запущен — без него не будет доступа к прод-данным (ClickHouse, Trino, OpenMetadata, Grafana). Инструкция: connectors/ACCESS.md"
  fi
}

# ── ClickHouse ───────────────────────────────────────────────────────────
# Два независимых коннектора, два независимых кластера — разные учётки,
# разный смоук-тест, разный отказ не трогает второй. WMS (склад, основной для
# операционной аналитики) и DWH (продажи/финансы/маркетинг) исторически были
# одним коннектором с одним вопросом про хост — человек мог настроить только
# один кластер за раз, второй оставался недоступен вовсе.
#
# Реальный HTTP-интерфейс ClickHouse — http, порт 8123 (не https из брифа:
# тот же дефект уже находили в lib/telemetry.py, из-за него не доходило ни
# одной строки телеметрии). Порт спрашиваем, а не хардкодим, и подбираем
# схему по факту ответа, а не гадаем — https пробуем вторым номером.
#
# CH_WMS_SECURE/CH_DWH_SECURE пишутся в secrets.env наравне с
# TELEMETRY_CH_SECURE: .mcp.json подставляет их в CLICKHOUSE_SECURE каждого
# коннектора — раньше (один коннектор, один CH_SECURE) там был захардкожен
# литерал "true", из-за чего коннектор считался бы настроенным и не
# подключался в первой сессии, хотя смоук-тест здесь честно нашёл рабочую
# схему.
setup_clickhouse_wms() {
  say "── ClickHouse: складской кластер (WMS) ── основной для операционной аналитики; логин это корп-почта через дефис, пароль выдаётся заявкой в JSM"
  ask CH_WMS_HOST "  Хост [wms-clickhouse.prod.um.internal]: "
  CH_WMS_HOST=${CH_WMS_HOST:-wms-clickhouse.prod.um.internal}
  ask CH_WMS_PORT "  Порт [8123]: "
  CH_WMS_PORT=${CH_WMS_PORT:-8123}
  ask_required CH_WMS_USER "  Логин: " "CH_WMS_USER (логин ClickHouse WMS)"
  ask_required_secret CH_WMS_PASSWORD "  Пароль: " "CH_WMS_PASSWORD (пароль ClickHouse WMS)"

  # Пробуем каждую схему в свой файл — иначе при обрыве соединения на втором
  # запросе (например https, если сервер вообще не говорит по TLS) curl не
  # трогает файл первого запроса, и в диагностике легко перепутать код одной
  # попытки с телом ответа другой.
  local http_out https_out http_code https_code
  http_out="$(mk_tmp)"
  http_code=$(curl_check "$http_out" 8 \
    "http://$CH_WMS_HOST:$CH_WMS_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_WMS_USER" "X-ClickHouse-Key: $CH_WMS_PASSWORD")

  if [ "$http_code" = "200" ]; then
    ok "вижу $(cat "$http_out" 2>/dev/null) баз (по http, порт $CH_WMS_PORT)"
    put_env CH_WMS_HOST "$CH_WMS_HOST"
    put_env CH_WMS_PORT "$CH_WMS_PORT"
    put_env CH_WMS_USER "$CH_WMS_USER"
    put_env CH_WMS_PASSWORD "$CH_WMS_PASSWORD"
    put_env CH_WMS_SECURE "false"
    CH_WMS_OK=1
    ENABLED+=("clickhouse-wms")
    return
  fi

  https_out="$(mk_tmp)"
  https_code=$(curl_check "$https_out" 8 \
    "https://$CH_WMS_HOST:$CH_WMS_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_WMS_USER" "X-ClickHouse-Key: $CH_WMS_PASSWORD")

  if [ "$https_code" = "200" ]; then
    ok "вижу $(cat "$https_out" 2>/dev/null) баз (по https, порт $CH_WMS_PORT)"
    put_env CH_WMS_HOST "$CH_WMS_HOST"
    put_env CH_WMS_PORT "$CH_WMS_PORT"
    put_env CH_WMS_USER "$CH_WMS_USER"
    put_env CH_WMS_PASSWORD "$CH_WMS_PASSWORD"
    put_env CH_WMS_SECURE "true"
    CH_WMS_OK=1
    ENABLED+=("clickhouse-wms")
    return
  fi

  fail "не подключился ни по http (код: $http_code), ни по https (код: $https_code) на $CH_WMS_HOST:$CH_WMS_PORT"
  # Показываем текст той попытки, которая реально дошла до сервера (код не
  # 000) — там настоящая причина отказа, а не обрыв TCP/TLS соединения.
  if [ "$http_code" != "000" ]; then
    fail "$(head -c 300 "$http_out" 2>/dev/null)"
  elif [ "$https_code" != "000" ]; then
    fail "$(head -c 300 "$https_out" 2>/dev/null)"
  fi
  fail "пропущено — подключить позже: ./setup.sh --add clickhouse-wms"
}

setup_clickhouse_dwh() {
  say "── ClickHouse: общий кластер (DWH) ── продажи, финансы, маркетинг; учётка отдельная от складского кластера — независимый доступ, заявка в JSM подаётся отдельно"
  ask CH_DWH_HOST "  Хост [dwh-clickhouse.prod.um.internal]: "
  CH_DWH_HOST=${CH_DWH_HOST:-dwh-clickhouse.prod.um.internal}
  ask CH_DWH_PORT "  Порт [8123]: "
  CH_DWH_PORT=${CH_DWH_PORT:-8123}
  ask_required CH_DWH_USER "  Логин: " "CH_DWH_USER (логин ClickHouse DWH)"
  ask_required_secret CH_DWH_PASSWORD "  Пароль: " "CH_DWH_PASSWORD (пароль ClickHouse DWH)"

  local http_out https_out http_code https_code
  http_out="$(mk_tmp)"
  http_code=$(curl_check "$http_out" 8 \
    "http://$CH_DWH_HOST:$CH_DWH_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_DWH_USER" "X-ClickHouse-Key: $CH_DWH_PASSWORD")

  if [ "$http_code" = "200" ]; then
    ok "вижу $(cat "$http_out" 2>/dev/null) баз (по http, порт $CH_DWH_PORT)"
    put_env CH_DWH_HOST "$CH_DWH_HOST"
    put_env CH_DWH_PORT "$CH_DWH_PORT"
    put_env CH_DWH_USER "$CH_DWH_USER"
    put_env CH_DWH_PASSWORD "$CH_DWH_PASSWORD"
    put_env CH_DWH_SECURE "false"
    ENABLED+=("clickhouse-dwh")
    return
  fi

  https_out="$(mk_tmp)"
  https_code=$(curl_check "$https_out" 8 \
    "https://$CH_DWH_HOST:$CH_DWH_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_DWH_USER" "X-ClickHouse-Key: $CH_DWH_PASSWORD")

  if [ "$https_code" = "200" ]; then
    ok "вижу $(cat "$https_out" 2>/dev/null) баз (по https, порт $CH_DWH_PORT)"
    put_env CH_DWH_HOST "$CH_DWH_HOST"
    put_env CH_DWH_PORT "$CH_DWH_PORT"
    put_env CH_DWH_USER "$CH_DWH_USER"
    put_env CH_DWH_PASSWORD "$CH_DWH_PASSWORD"
    put_env CH_DWH_SECURE "true"
    ENABLED+=("clickhouse-dwh")
    return
  fi

  fail "не подключился ни по http (код: $http_code), ни по https (код: $https_code) на $CH_DWH_HOST:$CH_DWH_PORT"
  if [ "$http_code" != "000" ]; then
    fail "$(head -c 300 "$http_out" 2>/dev/null)"
  elif [ "$https_code" != "000" ]; then
    fail "$(head -c 300 "$https_out" 2>/dev/null)"
  fi
  fail "пропущено — подключить позже: ./setup.sh --add clickhouse-dwh"
}

# ── Телеметрия ───────────────────────────────────────────────────────────
# Отдельный вопрос, а не «тот же хост, что и складской ClickHouse»: таблицы
# sandbox.ai_usage_{sessions,events,verdicts} созданы ТОЛЬКО на складском
# кластере (wms-clickhouse). Аналитик, который вместо него указал бы здесь
# dwh-clickhouse, раньше получал TELEMETRY_CH_HOST=dwh-... — каждый INSERT
# падал бы с «unknown table», а telemetry.write() по контракту не бросает
# исключений и молча складывал бы строку в локальную очередь навсегда: ноль
# телеметрии при зелёной установке.
#
# Поэтому и смоук-запрос здесь не «жив ли сервер» (SELECT count() FROM
# system.databases отвечает на любом кластере), а SELECT count() FROM
# sandbox.ai_usage_sessions — он проверяет ровно тот путь, которым пойдут
# хуки: тот кластер, та база, та таблица, те права.
setup_telemetry() {
  say "── Телеметрия ── куда хуки пишут статистику работы (sandbox.ai_usage_*)"
  printf "  Таблицы живут на складском кластере (WMS) — обычно это дефолт ниже.\n"
  # Seed из .env (TELEMETRY_CH_* — так значения названы в secrets.env, а не
  # T_HOST/T_PORT — те локальные для этой функции, см. put_env ниже).
  T_HOST="${TELEMETRY_CH_HOST:-}"
  ask T_HOST "  Хост телеметрии [wms-clickhouse.prod.um.internal]: "
  T_HOST=${T_HOST:-wms-clickhouse.prod.um.internal}
  T_PORT="${TELEMETRY_CH_PORT:-}"
  ask T_PORT "  Порт [8123]: "
  T_PORT=${T_PORT:-8123}

  local T_USER T_PASSWORD
  if [ "$CH_WMS_OK" = "1" ] && [ "$T_HOST" = "${CH_WMS_HOST:-}" ] && [ "$T_PORT" = "${CH_WMS_PORT:-}" ]; then
    T_USER="$CH_WMS_USER"
    T_PASSWORD="$CH_WMS_PASSWORD"
    ok "тот же хост, что и складской ClickHouse (clickhouse-wms) — беру уже проверенный логин $T_USER"
  else
    T_USER="${TELEMETRY_CH_USER:-}"
    ask T_USER "  Логин: "
    T_PASSWORD="${TELEMETRY_CH_PASSWORD:-}"
    ask_secret T_PASSWORD "  Пароль: "
  fi

  local http_out https_out http_code https_code scheme
  http_out="$(mk_tmp)"
  http_code=$(curl_check "$http_out" 8 \
    "http://$T_HOST:$T_PORT/?query=SELECT+count()+FROM+sandbox.ai_usage_sessions" \
    "X-ClickHouse-User: $T_USER" "X-ClickHouse-Key: $T_PASSWORD")
  scheme=""
  if [ "$http_code" = "200" ]; then
    scheme="false"
  else
    https_out="$(mk_tmp)"
    https_code=$(curl_check "$https_out" 8 \
      "https://$T_HOST:$T_PORT/?query=SELECT+count()+FROM+sandbox.ai_usage_sessions" \
      "X-ClickHouse-User: $T_USER" "X-ClickHouse-Key: $T_PASSWORD")
    if [ "$https_code" = "200" ]; then
      scheme="true"
      http_out="$https_out"
    fi
  fi

  if [ -n "$scheme" ]; then
    ok "sandbox.ai_usage_sessions на месте, строк: $(cat "$http_out" 2>/dev/null)"
    put_env TELEMETRY_CH_HOST "$T_HOST"
    put_env TELEMETRY_CH_PORT "$T_PORT"
    put_env TELEMETRY_CH_USER "$T_USER"
    put_env TELEMETRY_CH_PASSWORD "$T_PASSWORD"
    put_env TELEMETRY_CH_SECURE "$scheme"
    return
  fi

  # Ничего не записываем: без TELEMETRY_CH_HOST телеметрия просто выключена
  # (см. Config.from_env в lib/telemetry.py). Это честнее, чем записать
  # заведомо нерабочий хост и копить очередь на диске месяцами.
  fail "таблица sandbox.ai_usage_sessions не ответила (http: $http_code, https: ${https_code:-—})"
  if [ "$http_code" != "000" ]; then
    fail "$(head -c 300 "$http_out" 2>/dev/null)"
  fi
  fail "телеметрия выключена — включить позже: ./setup.sh --add telemetry"
}

# ── Jira / Confluence (общий токен) ─────────────────────────────────────
setup_jira() {
  say "── Jira ── Профиль → Personal Access Tokens → Create token"
  ask_required_secret JIRA_TOKEN "  Токен: " "JIRA_TOKEN (токен Jira)"
  local out code name
  out="$(mk_tmp)"
  code=$(curl_check "$out" 10 "https://jira.uzum.com/rest/api/2/myself" \
    "Authorization: Bearer $JIRA_TOKEN")
  if [ "$code" = "200" ]; then
    name=$(UZUM_CHECK_FILE="$out" python3 -c "import json,os;print(json.load(open(os.environ['UZUM_CHECK_FILE']))['displayName'])" 2>/dev/null || echo "пользователя")
    ok "вижу тебя как $name"
    put_env JIRA_TOKEN "$JIRA_TOKEN"
    ENABLED+=("atlassian")
  else
    fail "токен не принят (код: $code)"
    fail "пропущено — подключить позже: ./setup.sh --add atlassian"
  fi
}

# ── Superset ── SSO, но SUPERSET_URL — обязательная переменная в .mcp.json
# без дефолта: без неё superset_mcp.py падает на старте, а не "не смог
# подключиться" — поэтому URL всё равно нужно записать, даже без кредов.
setup_superset() {
  say "── Superset ── кредов не нужно, вход через Keycloak SSO в браузере при первом обращении"
  ask SUPERSET_URL "  URL Superset [https://bi.uzum.uz]: "
  SUPERSET_URL=${SUPERSET_URL:-https://bi.uzum.uz}
  put_env SUPERSET_URL "$SUPERSET_URL"
  ENABLED+=("superset")
  ok "включён"
}

# ── Trino ── тоже SSO. TRINO_USER не идёт через .mcp.json (это не секрет),
# его trino_proxy.py читает из secrets.env сам при первом запросе — если не
# указать, коннектор поднимется, но первый же запрос упадёт с понятной
# ошибкой вместо тихого молчания.
setup_trino() {
  say "── Trino ── кредов не нужно, OAuth2 SSO в браузере при первом запросе"
  ask TRINO_USER "  Твой корп. email (для атрибуции запросов, Enter — пропустить): "
  if [ -n "$TRINO_USER" ]; then
    put_env TRINO_USER "$TRINO_USER"
    ok "включён"
  else
    fail "email не указан — Trino всё равно включится, но первый запрос упадёт с понятной ошибкой, пока не добавишь TRINO_USER в $SECRETS"
  fi
  ENABLED+=("trino")
}

# ── Grafana ── GRAFANA_URL тоже обязателен и без дефолта в .mcp.json, а в
# брифе его никто не спрашивал — добавляю запрос URL, иначе включённый
# коннектор гарантированно не поднимется.
setup_grafana() {
  say "── Grafana ── сервисный токен у платформы (URL приходит вместе с ним)"
  ask GRAFANA_URL "  URL Grafana (Enter — пропустить): "
  if [ -z "$GRAFANA_URL" ]; then
    # GRAFANA_TOKEN мог прийти из .env сам по себе — без URL он бесполезен,
    # и молчание тут выглядело бы как "мастер его не заметил", хотя на
    # самом деле он просто не может включить коннектор без адреса.
    if [ -n "${GRAFANA_TOKEN:-}" ]; then
      fail "GRAFANA_TOKEN в .env есть, а GRAFANA_URL нет — токен не использован"
    fi
    fail "пропущено — подключить позже: ./setup.sh --add grafana"
    return
  fi
  ask_required_secret GRAFANA_TOKEN "  Токен: " "GRAFANA_TOKEN (Grafana токен — GRAFANA_URL уже указан)"
  local out code org
  out="$(mk_tmp)"
  code=$(curl_check "$out" 10 "${GRAFANA_URL%/}/api/org" \
    "Authorization: Bearer $GRAFANA_TOKEN")
  if [ "$code" = "200" ]; then
    org=$(UZUM_CHECK_FILE="$out" python3 -c "import json,os;print(json.load(open(os.environ['UZUM_CHECK_FILE'])).get('name','?'))" 2>/dev/null || echo "?")
    ok "вижу организацию $org"
    put_env GRAFANA_URL "$GRAFANA_URL"
    put_env GRAFANA_TOKEN "$GRAFANA_TOKEN"
    ENABLED+=("grafana")
  else
    fail "токен не принят (код: $code)"
    fail "пропущено — подключить позже: ./setup.sh --add grafana"
  fi
}

# ── OpenMetadata ── тот же случай, что и с Grafana: OMD_URL обязателен и
# без дефолта, брифом не спрашивался.
setup_openmetadata() {
  say "── OpenMetadata ── Профиль → Access Token (URL — спроси в платформе, если нет под рукой)"
  ask OMD_URL "  URL OpenMetadata (Enter — пропустить): "
  if [ -z "$OMD_URL" ]; then
    if [ -n "${OMD_TOKEN:-}" ]; then
      fail "OMD_TOKEN в .env есть, а OMD_URL нет — токен не использован"
    fi
    fail "пропущено — подключить позже: ./setup.sh --add openmetadata"
    return
  fi
  ask_required_secret OMD_TOKEN "  Токен: " "OMD_TOKEN (OpenMetadata токен — OMD_URL уже указан)"
  local out code who
  out="$(mk_tmp)"
  code=$(curl_check "$out" 10 "${OMD_URL%/}/api/v1/users/loggedInUser" \
    "Authorization: Bearer $OMD_TOKEN")
  if [ "$code" = "200" ]; then
    who=$(UZUM_CHECK_FILE="$out" python3 -c "import json,os;print(json.load(open(os.environ['UZUM_CHECK_FILE'])).get('name','?'))" 2>/dev/null || echo "?")
    ok "вижу тебя как $who"
    put_env OMD_URL "$OMD_URL"
    put_env OMD_TOKEN "$OMD_TOKEN"
    ENABLED+=("openmetadata")
  else
    fail "токен не принят (код: $code)"
    fail "пропущено — подключить позже: ./setup.sh --add openmetadata"
  fi
}

# ── GrowthBook ── у GROWTHBOOK_TOKEN нет своего URL в .mcp.json (адрес API
# зашит внутри самого mcp-growthbook), поэтому в отличие от Grafana/OMD
# здесь живым запросом проверить нечего — принимаем токен как есть, как и
# было в брифе, и проверка перенесётся на первое обращение внутри сессии.
setup_growthbook() {
  say "── GrowthBook ── Settings → API Keys → read-only ключ"
  ask_secret GROWTHBOOK_TOKEN "  Токен (Enter — пропустить): "
  if [ -n "$GROWTHBOOK_TOKEN" ]; then
    put_env GROWTHBOOK_TOKEN "$GROWTHBOOK_TOKEN"
    ENABLED+=("growthbook")
    ok "записан — по-настоящему проверится при первом обращении в сессии"
  else
    fail "пропущено — подключить позже: ./setup.sh --add growthbook"
  fi
}

# ── Google Sheets ── живой запрос к Google из bash нецелесообразен (подпись
# JWT сервисного аккаунта завязана на google-auth, см. connectors/ACCESS.md),
# поэтому вместо "ok"/"fail по факту существования файла" — разбираем сам
# файл и показываем email сервисного аккаунта, чтобы ошибка (не тот файл,
# битый JSON, не тот тип ключа) была видна сразу, а не при первом запросе.
setup_sheets() {
  say "── Google Sheets ── файл сервисного аккаунта — у Насти"
  ask GOOGLE_SA_FILE "  Путь к файлу (Enter — пропустить): "
  if [ -z "$GOOGLE_SA_FILE" ]; then
    if [ -n "${GOOGLE_SHEETS_FOLDER_ID:-}" ]; then
      fail "GOOGLE_SHEETS_FOLDER_ID в .env есть, а GOOGLE_SA_FILE нет — не использован"
    fi
    fail "пропущено — подключить позже: ./setup.sh --add sheets"
    return
  fi
  GOOGLE_SA_FILE="${GOOGLE_SA_FILE/#\~/$HOME}"
  if [ ! -f "$GOOGLE_SA_FILE" ]; then
    fail "файла нет: $GOOGLE_SA_FILE"
    fail "пропущено — подключить позже: ./setup.sh --add sheets"
    return
  fi
  local email
  email=$(GOOGLE_SA_FILE_PATH="$GOOGLE_SA_FILE" python3 -c "
import json, os
try:
    data = json.load(open(os.environ['GOOGLE_SA_FILE_PATH'], encoding='utf-8'))
    if data.get('type') != 'service_account' or not data.get('private_key'):
        raise ValueError('это не похоже на ключ сервисного аккаунта')
    print(data['client_email'])
except Exception as e:
    print('ERROR:' + str(e))
" 2>/dev/null)
  case "$email" in
    ERROR:*|"")
      fail "файл не подходит: ${email#ERROR:}"
      fail "пропущено — подключить позже: ./setup.sh --add sheets"
      return
      ;;
  esac
  ask_required GOOGLE_SHEETS_FOLDER_ID "  ID папки для таблиц (из URL: drive.google.com/drive/folders/ЭТОТ_ID): " "GOOGLE_SHEETS_FOLDER_ID (ID папки Google Sheets — GOOGLE_SA_FILE уже указан)"
  put_env GOOGLE_SA_FILE "$GOOGLE_SA_FILE"
  put_env GOOGLE_SHEETS_FOLDER_ID "$GOOGLE_SHEETS_FOLDER_ID"
  ENABLED+=("sheets")
  ok "сервисный аккаунт: $email — не забудь расшарить на него папку с таблицами"
}

run_server() {
  case "$1" in
    clickhouse-wms) setup_clickhouse_wms ;;
    clickhouse-dwh) setup_clickhouse_dwh ;;
    atlassian)    setup_jira ;;
    superset)     setup_superset ;;
    trino)        setup_trino ;;
    grafana)      setup_grafana ;;
    openmetadata) setup_openmetadata ;;
    growthbook)   setup_growthbook ;;
    sheets)       setup_sheets ;;
    telemetry)    setup_telemetry ;;
    codex-hooks)  run_codex_hooks_target ;;
    *)
      printf "Неизвестный коннектор: %s\nДоступные: %s %s\n" "$1" "$SERVERS_LIST" "$EXTRAS_LIST" >&2
      exit 1
      ;;
  esac
}

# ── Codex: доставка конфига и хуков, проверка живым запуском ────────────
#
# "Конфиги обоих движков пишутся из общего реестра, через существующий
# генератор" (бриф задачи) — существующий генератор это
# tools/render_configs.py (connectors/registry.py, задача Codex-3). Он не
# был подключён к мастеру установки вообще: .mcp.json и .codex/config.toml
# лежали в git как статические файлы. Для .mcp.json это было безобидно
# (секреты — подстановка ${VAR}, машинно-независимо), но
# .codex/config.toml встраивает АБСОЛЮТНЫЙ путь к репозиторию в профиль
# разрешений (tools/render_configs.py, `_codex_permission_profile_
# filesystem_rules`) — у каждого аналитика он свой. Не перегенерировать
# конфиг при установке значило доставить всем Codex-профиль с путём с
# машины автора задачи, а не с их собственной.
render_engine_configs() {
  say "Обновляю .mcp.json и .codex/config.toml из connectors/registry.py…"
  local out
  out="$(python3 "$REPO_DIR/tools/render_configs.py" 2>&1)"
  printf "  %s\n" "$out"
}

# Доставляет .codex/config.toml профилем ($CODEX_HOME/uzum.config.toml, см.
# докстринг deploy_codex_profile в lib/setup_helpers.py про то, почему не
# слияние в базовый config.toml и не переключение $CODEX_HOME) и сливает
# хуки телеметрии в $CODEX_HOME/hooks.json, не трогая чужие записи там.
# Файл профиля с тем же именем, но без нашего маркера в первой строке —
# не наш; переносится в сторону (foreign-backup-<время>), не затирается
# молча (находка ревью №5 — та же бережность, что и у hooks.json).
deploy_codex_artifacts() {
  local codex_home_dir out
  codex_home_dir="${CODEX_HOME:-$HOME/.codex}"
  out="$(UZUM_CODEX_HOME_DIR="$codex_home_dir" python3 -c "
import os, sys
sys.path.insert(0, '$REPO_DIR/lib')
import setup_helpers
home = os.environ['UZUM_CODEX_HOME_DIR']
cfg_changed, backed_up_to = setup_helpers.deploy_codex_profile(
    '$REPO_DIR/.codex/config.toml', home)
hooks_changed = setup_helpers.deploy_codex_hooks(home)
profile_path = os.path.join(home, '%s.config.toml' % setup_helpers.CODEX_PROFILE_NAME)
if backed_up_to:
    print('ВНИМАНИЕ: %s уже существовал и не был нашим (нет маркера uzum-data-agent) — '
          'сохранён как %s, не стёрт' % (profile_path, backed_up_to))
print('профиль %s: %s' % (profile_path, 'обновлён' if cfg_changed else 'уже актуален'))
print('хуки %s: %s' % (
    os.path.join(home, 'hooks.json'),
    'обновлены' if hooks_changed else 'уже на месте'))
")"
  printf "%s\n" "$out" | while IFS= read -r line; do
    case "$line" in
      ВНИМАНИЕ:*) fail "$line" ;;
      *) ok "$line" ;;
    esac
  done
}

# После доставки — живая проверка факта "хуки Codex доверены", а не догадка
# (docs/codex-facts.md, раздел 7: без доверия headless-режим ничем не
# сигналит, "команда же отработала" — тот же класс дефекта, что уже дважды
# ловили на этом проекте). Сигнал — строка "hook: SessionStart" в выводе
# `codex exec`: она печатается ТОЛЬКО когда хук реально выполнился (см.
# отчёт задачи — воспроизведено живым запуском что при отсутствии доверия
# строки нет вовсе, ни намёка).
#
# НАХОДКА РЕВЬЮ (Critical): без `</dev/null` codex exec ждёт "дополнительный
# ввод со стандартного ввода" в любом контексте, где стандартный ввод самого
# setup.sh — не голый терминал (в т.ч. запуск не интерактивным человеком, а
# из другого процесса) — зависает до срабатывания сторожевого таймера, и
# ИМЕННО ЭТО (не отсутствие доверия) даёт «доверие не выдано». Тот самый
# класс дефекта, ради поимки которого писалась вся проверка: выглядит как
# рабочая сессия, а на самом деле нет. Промпт и так передан аргументом,
# читать с stdin нечего — закрываем его явно.
#
# Сторожевой таймер (SIGKILL) и обычное "не доверено" теперь различимы:
# после `wait` разбираем код возврата — 137 (128+9, соглашение bash для
# завершения по SIGKILL) означает, что проверка не уложилась в отведённое
# время, а не что доверие точно не выдано, и об этом сообщается отдельным,
# явно другим текстом, а не тем же "доверие не выдано".
check_codex_hook_trust() {
  local codex_home_dir probe_out status
  codex_home_dir="${CODEX_HOME:-$HOME/.codex}"
  if [ ! -f "$codex_home_dir/auth.json" ]; then
    fail "Codex не авторизован (нет $codex_home_dir/auth.json) — выполни 'codex login', затем ./setup.sh --add codex-hooks, чтобы проверить доверие хукам"
    return
  fi
  probe_out="$(mk_tmp)"
  (
    cd "$REPO_DIR" || exit 1
    CODEX_HOME="$codex_home_dir" codex exec -p "$CODEX_PROFILE_NAME" --skip-git-repo-check \
      "Выполни ровно одну shell-команду: echo hook-trust-probe. Затем остановись." \
      </dev/null >"$probe_out" 2>&1 &
    child_pid=$!
    ( sleep 90; kill -9 "$child_pid" 2>/dev/null ) &
    watchdog_pid=$!
    wait "$child_pid" 2>/dev/null
    child_status=$?
    kill "$watchdog_pid" 2>/dev/null
    wait "$watchdog_pid" 2>/dev/null
    exit "$child_status"
  )
  status=$?
  if [ "$status" -eq 137 ]; then
    fail "проверка доверия хукам Codex не уложилась в 90 секунд — похоже, зависла, а не «доверие не выдано». Проверь вручную из терминала: CODEX_HOME=\"$codex_home_dir\" codex exec -p $CODEX_PROFILE_NAME --skip-git-repo-check \"echo test\" </dev/null"
    return
  fi
  if grep -q "hook: SessionStart" "$probe_out" 2>/dev/null; then
    ok "доверие хукам Codex выдано — телеметрия будет писаться"
  else
    fail "доверие хукам Codex ещё не выдано — телеметрия из Codex писаться НЕ БУДЕТ, пока это не исправить"
    printf "  Сделай один раз: запусти просто 'codex -p %s' (не exec, обычный интерактивный запуск) в этой папке.\n" "$CODEX_PROFILE_NAME"
    printf "  На первом экране подтверди доверие папке («1. Yes, continue»).\n"
    printf "  На экране «Hooks need review» выбери «2. Trust all and continue».\n"
    printf "  Доверие сохранится навсегда для этой машины — проверить снова: ./setup.sh --add codex-hooks\n"
  fi
}

# Точка входа для `./setup.sh --add codex-hooks` — находка ревью (Important):
# основной поток проверяет ENGINE_CODEX_FOUND перед доставкой/проверкой, а
# этот путь — нет. Человек с одним только Claude Code, решивший посмотреть,
# что за пункт "codex-hooks", без проверки получил бы созданный $CODEX_HOME
# каталог, которого не просил, и сбивающее с толку «доверие не выдано»
# вместо честного «Codex не установлен».
run_codex_hooks_target() {
  if [ "$ENGINE_CODEX_FOUND" != "1" ]; then
    fail "Codex не установлен на этой машине — нечего настраивать (доверие хукам относится только к Codex)"
    fail "Поставь: npm install -g @openai/codex — и запусти ./setup.sh --add codex-hooks ещё раз"
    return
  fi
  render_engine_configs
  deploy_codex_artifacts
  check_codex_hook_trust
}

# ── разбор аргументов ────────────────────────────────────────────────────
# Цикл, а не одно `case "${1:-}"`: --non-interactive должен работать в любом
# порядке рядом с --add NAME, а не только первым аргументом.
MODE="full"
ADD_SERVER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --add)
      MODE="add"
      ADD_SERVER="${2:-}"
      if [ -z "$ADD_SERVER" ]; then
        printf "Использование: ./setup.sh --add <коннектор>\nДоступные: %s %s\n" "$SERVERS_LIST" "$EXTRAS_LIST" >&2
        exit 1
      fi
      shift 2
      ;;
    --non-interactive)
      NONINTERACTIVE=1
      shift
      ;;
    --help|-h)
      cat <<EOF
Использование:
  ./setup.sh                    полный мастер установки — все коннекторы за один заход
  ./setup.sh --add NAME         настроить/переподключить один коннектор ($SERVERS_LIST),
                                 телеметрию (./setup.sh --add telemetry) либо доставку
                                 конфига/хуков Codex (./setup.sh --add codex-hooks)
  ./setup.sh --non-interactive  без вопросов: значения только из .env, по
                                 недостающим — список и код возврата 1
                                 (сочетается с --add)

Настраивает Claude Code и/или Codex — какие из них реально стоят на этой
машине (нужен хотя бы один). Оба движка используют один общий набор
коннекторов и один файл секретов; конфиги обоих генерируются заново на
каждом запуске из connectors/registry.py.

Заполнить доступы файлом вместо вопросов: cp .env.example .env, впиши
значения (или укажи путь к файлу через \$UZUM_ENV_FILE), запусти как обычно.
EOF
      exit 0
      ;;
    *)
      printf "Неизвестный аргумент: %s\nСправка: ./setup.sh --help\n" "$1" >&2
      exit 1
      ;;
  esac
done

check_environment

ENV_FILE="$(find_env_file)"
if [ -n "$ENV_FILE" ]; then
  say "Нашёл файл доступов: $ENV_FILE"
  ENV_VARS_FILE="$(mk_tmp)"
  load_env_file "$ENV_FILE" >"$ENV_VARS_FILE"
  set -a
  # shellcheck disable=SC1090
  . "$ENV_VARS_FILE"
  set +a
fi

if [ "$MODE" = "add" ]; then
  run_server "$ADD_SERVER"
else
  setup_clickhouse_wms
  setup_clickhouse_dwh
  setup_telemetry
  setup_jira
  setup_superset
  setup_trino
  setup_grafana
  setup_openmetadata
  setup_growthbook
  setup_sheets
fi

write_enabled

if [ "$NONINTERACTIVE" = "1" ] && [ "${#MISSING[@]}" -gt 0 ]; then
  say "Без вопросов (--non-interactive) не хватает значений:"
  for m in "${MISSING[@]}"; do
    fail "$m"
  done
  if [ -n "$ENV_FILE" ]; then
    fail "впиши их в $ENV_FILE и запусти заново"
  else
    fail "заполни .env (шаблон — .env.example, или укажи путь через UZUM_ENV_FILE) и запусти заново"
  fi
  exit 1
fi

# Конфиги обоих движков — из общего реестра коннекторов, через
# tools/render_configs.py, всегда (полный мастер и --add тоже): дёшево,
# идемпотентно, и без этого .codex/config.toml нёс бы путь к репозиторию с
# чужой машины (см. докстринг render_engine_configs выше). После проверки
# MISSING, не до неё — незачем генерировать и доставлять конфиг, если
# установка всё равно сейчас оборвётся с ошибкой нехватки доступов.
render_engine_configs

# Codex: доставить конфиг и хуки туда, где движок их реально видит, и
# проверить живым запуском, что доверие хукам выдано — только когда Codex
# вообще стоит на машине, и только в полном прогоне мастера (--add одного
# коннектора не должен каждый раз заново дёргать живой codex exec — это
# настоящий запрос к модели, не бесплатный curl; для точечной перепроверки
# есть ./setup.sh --add codex-hooks).
if [ "$MODE" != "add" ] && [ "$ENGINE_CODEX_FOUND" = "1" ]; then
  say "── Codex: доставка конфига и хуков ──"
  deploy_codex_artifacts
  check_codex_hook_trust
fi

mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_DIR/bin/uzum" "$HOME/.local/bin/uzum"
chmod +x "$REPO_DIR/bin/uzum" "$REPO_DIR/setup.sh" 2>/dev/null || true

say "Готово. Включено в этом запуске: ${#ENABLED[@]}"

if ! command -v uzum >/dev/null 2>&1; then
  say "Команда uzum пока не видна в PATH."
  cat <<EOF
  Добавь в ~/.zshrc (или ~/.bashrc) и открой новый терминал:
    export PATH="\$HOME/.local/bin:\$PATH"
  Либо запускай сразу так: $HOME/.local/bin/uzum
EOF
fi

if [ "$ENGINE_CLAUDE_FOUND" = "1" ]; then
  cat <<'EOF'

ВАЖНО (Claude Code): при первом запуске он спросит, доверяешь ли ты этой
папке — ответь «да» (Yes, proceed). Клонированный репозиторий не может сам
одобрить свои же MCP-серверы: если ответить «нет» или пропустить диалог,
ни один коннектор не поднимется, хотя всё настроено правильно.
EOF
fi

if [ "$ENGINE_CODEX_FOUND" = "1" ]; then
  cat <<EOF

ВАЖНО (Codex): при первом запуске (обычный интерактивный 'codex -p $CODEX_PROFILE_NAME',
не exec) он спросит доверие папке, а следом отдельным экраном — доверие
хукам («Hooks need review»). На нём выбери «2. Trust all and continue» —
без этого телеметрия из Codex писаться не будет, и никакого другого
сигнала об этом не будет (см. connectors/ACCESS.md). Мастер уже проверил
это выше живым запуском — если он сообщил «доверие хукам ещё не выдано»,
пройди диалог один раз и перепроверь: ./setup.sh --add codex-hooks
EOF
fi

# Синтаксис вызова скилла у движков разный: слэш у Claude Code, доллар у
# Codex. Блок был безусловным и всегда показывал слэш — аналитик, поставивший
# только Codex, набирал /task и не получал ничего. В первый час человек
# читает вывод мастера, а не README, поэтому развилка тут обязательна.
# Одинарные кавычки в heredoc — иначе $task/$fix-access развернутся в пустоту.
say "Запускай: uzum"

if [ "$ENGINE_CLAUDE_FOUND" = "1" ] && [ "$ENGINE_CODEX_FOUND" = "1" ]; then
  cat <<'EOF'
Синтаксис вызова скилла зависит от движка:
  Claude Code:  /task <ключ задачи из Jira>    починка: /fix-access
  Codex:        $task <ключ задачи из Jira>    починка: $fix-access
EOF
elif [ "$ENGINE_CODEX_FOUND" = "1" ]; then
  cat <<'EOF'
Первая задача:  $task <ключ задачи из Jira>
Если сломалось: $fix-access прямо в сессии
EOF
else
  cat <<'EOF'
Первая задача:  /task <ключ задачи из Jira>
Если сломалось: /fix-access прямо в сессии
EOF
fi

cat <<'EOF'
Добавить доступ позже: ./setup.sh --add <коннектор>
EOF

# .env — тот же класс данных, что и $SECRETS (пароли, токены в открытом
# виде), просто лежит внутри рабочей папки. Канонический файл секретов к
# этому моменту уже записан (put_env писал в него по ходу установки), .env
# больше не нужен — но удаляем только по явному согласию, не молча.
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
  say "$ENV_FILE хранит пароли и токены в открытом виде."
  printf "  Секреты уже записаны в %s — файл больше не нужен.\n" "$SECRETS"
  if [ "$NONINTERACTIVE" = "1" ]; then
    printf "  Удали %s вручную, когда будет удобно.\n" "$ENV_FILE"
  else
    read -rp "  Удалить $ENV_FILE сейчас? [y/N]: " DELETE_ENV
    case "$DELETE_ENV" in
      [yY]|[yY][eE][sS])
        rm -f "$ENV_FILE"
        ok "$ENV_FILE удалён"
        ;;
      *)
        printf "  Оставил как есть — удали вручную, когда будет удобно.\n"
        ;;
    esac
  fi
fi
