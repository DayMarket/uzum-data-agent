#!/usr/bin/env bash
# Мастер установки: спрашивает доступы и проверяет каждый живым запросом.
#
# Запуск:
#   ./setup.sh              — полный мастер, все коннекторы за один заход
#   ./setup.sh --add NAME   — настроить/переподключить один коннектор,
#                             не трогая остальные (clickhouse, atlassian,
#                             superset, trino, grafana, openmetadata,
#                             growthbook, sheets), либо телеметрию:
#                             ./setup.sh --add telemetry
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS="$HOME/.config/uzum-ai/secrets.env"
SETTINGS_LOCAL="$REPO_DIR/.claude/settings.local.json"
SERVERS_LIST="clickhouse atlassian superset trino grafana openmetadata growthbook sheets"

# Коннекторы, включённые за этот запуск. Bash 3.2 (дефолтный на macOS) роняет
# скрипт с "unbound variable" на "${ENABLED[@]}"/"${ENABLED[*]}", если массив
# пустой и включён `set -u` — поэтому везде ниже массив разворачивается только
# через `[ "${#ENABLED[@]}" -gt 0 ]` или через явный псевдо-дефолт `:-`.
ENABLED=()

# Настраивается мастером, но не является MCP-сервером и не попадает в
# enabledMcpjsonServers: телеметрия — это хуки, а не коннектор.
EXTRAS_LIST="telemetry"

# Смоук-тест рабочего ClickHouse прошёл в этом запуске (setup_clickhouse).
# Нужен setup_telemetry: переиспользовать логин/пароль можно только если они
# на этом хосте реально сработали, а не просто были введены.
CH_OK=0

say()  { printf "\n%s\n" "$1"; }
ok()   { printf "  \xe2\x9c\x93 %s\n" "$1"; }
fail() { printf "  \xe2\x9c\x97 %s\n" "$1"; }

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

check_environment() {
  say "Проверяю окружение…"
  if command -v claude >/dev/null 2>&1; then
    ok "claude найден"
  else
    fail "Claude Code не установлен: https://claude.com/code"
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
  # mcp-grafana — не PyPI-пакет, а Go-бинарь (репозиторий grafana/mcp-grafana,
  # в homebrew-core). Раньше .mcp.json запускал его как `uvx mcp-grafana` —
  # такого пакета на PyPI нет, коннектор не поднимался ни у кого, при этом
  # смоук-тест токена ниже честно печатал «вижу организацию».
  # Не фатально: без Grafana остальные семь коннекторов работают.
  if command -v mcp-grafana >/dev/null 2>&1; then
    ok "mcp-grafana найден"
  else
    fail "mcp-grafana не найден — коннектор grafana не поднимется. Поставь: brew install mcp-grafana"
  fi
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
# Реальный HTTP-интерфейс ClickHouse — http, порт 8123 (не https из брифа:
# тот же дефект уже находили в lib/telemetry.py, из-за него не доходило ни
# одной строки телеметрии). Порт спрашиваем, а не хардкодим, и подбираем
# схему по факту ответа, а не гадаем — https пробуем вторым номером.
#
# CH_SECURE пишется в secrets.env наравне с TELEMETRY_CH_SECURE: .mcp.json
# подставляет её в CLICKHOUSE_SECURE (см. CH_SECURE в этом же файле) —
# раньше там был захардкожен литерал "true", из-за чего рабочий коннектор
# clickhouse считался бы настроенным и не подключался в первой сессии, хотя
# смоук-тест здесь честно нашёл рабочую схему.
setup_clickhouse() {
  say "── ClickHouse ── логин это корп-почта через дефис, пароль выдаётся заявкой в JSM"
  printf "  Складской кластер (WMS) — основной для операционной аналитики: wms-clickhouse.prod.um.internal\n"
  printf "  Общий DWH (продажи, финансы, маркетинг):                       dwh-clickhouse.prod.um.internal\n"
  read -rp "  Хост [wms-clickhouse.prod.um.internal]: " CH_HOST
  CH_HOST=${CH_HOST:-wms-clickhouse.prod.um.internal}
  read -rp "  Порт [8123]: " CH_PORT
  CH_PORT=${CH_PORT:-8123}
  read -rp "  Логин: " CH_USER
  read -rsp "  Пароль: " CH_PASSWORD; echo

  # Пробуем каждую схему в свой файл — иначе при обрыве соединения на втором
  # запросе (например https, если сервер вообще не говорит по TLS) curl не
  # трогает файл первого запроса, и в диагностике легко перепутать код одной
  # попытки с телом ответа другой.
  local http_out https_out http_code https_code
  http_out="$(mk_tmp)"
  http_code=$(curl_check "$http_out" 8 \
    "http://$CH_HOST:$CH_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_USER" "X-ClickHouse-Key: $CH_PASSWORD")

  if [ "$http_code" = "200" ]; then
    ok "вижу $(cat "$http_out" 2>/dev/null) баз (по http, порт $CH_PORT)"
    put_env CH_HOST "$CH_HOST"
    put_env CH_PORT "$CH_PORT"
    put_env CH_USER "$CH_USER"
    put_env CH_PASSWORD "$CH_PASSWORD"
    put_env CH_SECURE "false"
    CH_OK=1
    ENABLED+=("clickhouse")
    return
  fi

  https_out="$(mk_tmp)"
  https_code=$(curl_check "$https_out" 8 \
    "https://$CH_HOST:$CH_PORT/?query=SELECT+count()+FROM+system.databases" \
    "X-ClickHouse-User: $CH_USER" "X-ClickHouse-Key: $CH_PASSWORD")

  if [ "$https_code" = "200" ]; then
    ok "вижу $(cat "$https_out" 2>/dev/null) баз (по https, порт $CH_PORT)"
    put_env CH_HOST "$CH_HOST"
    put_env CH_PORT "$CH_PORT"
    put_env CH_USER "$CH_USER"
    put_env CH_PASSWORD "$CH_PASSWORD"
    put_env CH_SECURE "true"
    CH_OK=1
    ENABLED+=("clickhouse")
    return
  fi

  fail "не подключился ни по http (код: $http_code), ни по https (код: $https_code) на $CH_HOST:$CH_PORT"
  # Показываем текст той попытки, которая реально дошла до сервера (код не
  # 000) — там настоящая причина отказа, а не обрыв TCP/TLS соединения.
  if [ "$http_code" != "000" ]; then
    fail "$(head -c 300 "$http_out" 2>/dev/null)"
  elif [ "$https_code" != "000" ]; then
    fail "$(head -c 300 "$https_out" 2>/dev/null)"
  fi
  fail "пропущено — подключить позже: ./setup.sh --add clickhouse"
}

# ── Телеметрия ───────────────────────────────────────────────────────────
# Отдельный вопрос, а не «тот же хост, что и рабочий ClickHouse»: таблицы
# sandbox.ai_usage_{sessions,events,verdicts} созданы ТОЛЬКО на складском
# кластере (wms-clickhouse). Аналитик, который выбрал рабочим хостом
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
  printf "  Таблицы живут на складском кластере — обычно это дефолт ниже.\n"
  read -rp "  Хост телеметрии [wms-clickhouse.prod.um.internal]: " T_HOST
  T_HOST=${T_HOST:-wms-clickhouse.prod.um.internal}
  read -rp "  Порт [8123]: " T_PORT
  T_PORT=${T_PORT:-8123}

  local T_USER T_PASSWORD
  if [ "$CH_OK" = "1" ] && [ "$T_HOST" = "${CH_HOST:-}" ] && [ "$T_PORT" = "${CH_PORT:-}" ]; then
    T_USER="$CH_USER"
    T_PASSWORD="$CH_PASSWORD"
    ok "тот же хост, что и рабочий ClickHouse — беру уже проверенный логин $T_USER"
  else
    read -rp "  Логин: " T_USER
    read -rsp "  Пароль: " T_PASSWORD; echo
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
  read -rsp "  Токен: " JIRA_TOKEN; echo
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
  read -rp "  URL Superset [https://bi.uzum.uz]: " SUPERSET_URL
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
  read -rp "  Твой корп. email (для атрибуции запросов, Enter — пропустить): " TRINO_USER
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
  read -rp "  URL Grafana (Enter — пропустить): " GRAFANA_URL
  if [ -z "$GRAFANA_URL" ]; then
    fail "пропущено — подключить позже: ./setup.sh --add grafana"
    return
  fi
  read -rsp "  Токен: " GRAFANA_TOKEN; echo
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
  read -rp "  URL OpenMetadata (Enter — пропустить): " OMD_URL
  if [ -z "$OMD_URL" ]; then
    fail "пропущено — подключить позже: ./setup.sh --add openmetadata"
    return
  fi
  read -rsp "  Токен: " OMD_TOKEN; echo
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
  read -rsp "  Токен (Enter — пропустить): " GROWTHBOOK_TOKEN; echo
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
  read -rp "  Путь к файлу (Enter — пропустить): " GOOGLE_SA_FILE
  if [ -z "$GOOGLE_SA_FILE" ]; then
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
  read -rp "  ID папки для таблиц (из URL: drive.google.com/drive/folders/ЭТОТ_ID): " GOOGLE_SHEETS_FOLDER_ID
  put_env GOOGLE_SA_FILE "$GOOGLE_SA_FILE"
  put_env GOOGLE_SHEETS_FOLDER_ID "$GOOGLE_SHEETS_FOLDER_ID"
  ENABLED+=("sheets")
  ok "сервисный аккаунт: $email — не забудь расшарить на него папку с таблицами"
}

run_server() {
  case "$1" in
    clickhouse)   setup_clickhouse ;;
    atlassian)    setup_jira ;;
    superset)     setup_superset ;;
    trino)        setup_trino ;;
    grafana)      setup_grafana ;;
    openmetadata) setup_openmetadata ;;
    growthbook)   setup_growthbook ;;
    sheets)       setup_sheets ;;
    telemetry)    setup_telemetry ;;
    *)
      printf "Неизвестный коннектор: %s\nДоступные: %s %s\n" "$1" "$SERVERS_LIST" "$EXTRAS_LIST" >&2
      exit 1
      ;;
  esac
}

# ── разбор аргументов ────────────────────────────────────────────────────
MODE="full"
ADD_SERVER=""
case "${1:-}" in
  --add)
    MODE="add"
    ADD_SERVER="${2:-}"
    if [ -z "$ADD_SERVER" ]; then
      printf "Использование: ./setup.sh --add <коннектор>\nДоступные: %s %s\n" "$SERVERS_LIST" "$EXTRAS_LIST" >&2
      exit 1
    fi
    ;;
  --help|-h)
    cat <<EOF
Использование:
  ./setup.sh              полный мастер установки — все коннекторы за один заход
  ./setup.sh --add NAME   настроить/переподключить один коннектор ($SERVERS_LIST)
                          либо телеметрию: ./setup.sh --add telemetry
EOF
    exit 0
    ;;
esac

check_environment

if [ "$MODE" = "add" ]; then
  run_server "$ADD_SERVER"
else
  setup_clickhouse
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

cat <<'EOF'

ВАЖНО: при первом запуске Claude Code спросит, доверяешь ли ты этой папке —
ответь «да» (Yes, proceed). Клонированный репозиторий не может сам одобрить
свои же MCP-серверы: если ответить «нет» или пропустить диалог, ни один
коннектор не поднимется, хотя всё настроено правильно.

Запускай:       uzum
Первая задача:  /task <ключ задачи из Jira>
Если сломалось: /fix-access прямо в сессии
Добавить доступ позже: ./setup.sh --add <коннектор>
EOF
