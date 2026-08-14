#!/usr/bin/env bash
# Первый шаг установки на Windows: доводит чистую Ubuntu из WSL до
# состояния, в котором можно запускать ./setup.sh, и запускает его сам.
#
# Запуск (внутри WSL, из корня уже склонированного репозитория):
#   ./install-windows.sh
#
# Аргументы уходят в setup.sh как есть:
#   ./install-windows.sh --non-interactive
#
# Почему этот скрипт лежит в репозитории, а не скачивается одной строкой
# curl'ом, как принято у подобных установщиков: репозиторий приватный, и
# raw.githubusercontent.com отдаёт по нему 404 всякому, кто не залогинен.
# Значит клон всё равно первым, а до клона нужен только git — его и просим
# поставить руками, остальное берёт на себя этот скрипт.
#
# Что он НЕ делает: не спрашивает ни одного доступа и не пишет ни одного
# секрета. Это работа ./setup.sh, и она не дублируется здесь — иначе
# появилось бы второе место, где живёт список коннекторов, и они бы
# разошлись. Здесь только окружение: пакеты, uv, Node, движок.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf "\n%s\n" "$1"; }
ok()   { printf "  \xe2\x9c\x93 %s\n" "$1"; }
fail() { printf "  \xe2\x9c\x97 %s\n" "$1"; }
note() { printf "  \xc2\xb7 %s\n" "$1"; }

# Одна реализация правила «команда есть И запускается здесь» на все три
# точки входа — разбор и история отказа внутри файла.
# shellcheck source=lib/win_path.sh
. "$REPO_DIR/lib/win_path.sh"

# ── Мы вообще там, где надо ───────────────────────────────────────────────
#
# Три разных «не там», и каждое требует своего ответа. Общее «запусти в WSL»
# на все три было бы бесполезно человеку, который уже в Linux.
case "$(uname -s 2>/dev/null || echo unknown)" in
  MINGW*|MSYS*|CYGWIN*)
    fail "это Git Bash (MSYS/Cygwin), а не WSL"
    fail "Установка здесь пройдёт наполовину и оставит сессию, которая выглядит рабочей, но без профиля разрешений Codex — то есть без запрета читать .env и ~/.ssh"
    fail "Открой PowerShell от администратора, выполни 'wsl --install -d Ubuntu', перезагрузись — и повтори всё из терминала Ubuntu"
    fail "Инструкция по шагам: docs/install/windows.md"
    exit 1
    ;;
  Darwin)
    fail "это macOS — установщик для Windows тут не нужен"
    fail "Запускай сразу: ./setup.sh"
    exit 1
    ;;
esac

IS_WSL=0
if [ -n "${WSL_DISTRO_NAME:-}" ]; then
  IS_WSL=1
elif [ -r /proc/sys/kernel/osrelease ] &&
     grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  IS_WSL=1
fi
if [ "$IS_WSL" = "0" ]; then
  fail "это обычный Linux, а не WSL — установщик для Windows тут не нужен"
  fail "Запускай сразу: ./setup.sh"
  exit 1
fi

say "Windows + WSL: готовлю окружение перед ./setup.sh"
note "Доступы этот скрипт не спрашивает — их спросит ./setup.sh, которым всё закончится"

# Репозиторий на диске Windows. Здесь это жёстче, чем в setup.sh: там мы
# застаём готовую установку и уже нечего советовать, а тут человек в самом
# начале и переклонировать ему стоит одну минуту.
case "$REPO_DIR" in
  /mnt/*)
    fail "репозиторий лежит на диске Windows: $REPO_DIR"
    fail "Так он будет работать медленно, а git — показывать изменения, которых никто не делал; хук обновления скиллов на старте сессии на этом обрывается"
    fail "Переклонируй в домашнюю папку WSL и запусти установщик оттуда:"
    fail "    cd ~ && git clone https://github.com/DayMarket/uzum-data-agent.git && cd uzum-data-agent && ./install-windows.sh"
    exit 1
    ;;
esac
ok "репозиторий в файловой системе WSL"

# ── Сеть до прод-данных ───────────────────────────────────────────────────
#
# Самое частое место отказа, и раньше оно было ручным шагом в инструкции:
# создать %UserProfile%\.wslconfig блокнотом на стороне Windows. Шаг
# пропускали, а расплата приходила через десять минут — смоук-тест
# ClickHouse не проходил, и это читалось как неверный пароль. Файл лежит на
# диске Windows, то есть отсюда он виден: /mnt/c/Users/<имя>/.wslconfig.
# Значит, шаг можно не объяснять, а сделать.
#
# Дальше по коду важно различать три исхода, а не два: «уже включено»,
# «дописали, нужен перезапуск WSL» и «сами не смогли, вот что сделать
# руками». Третий случай остаётся — Windows-сторона может быть недоступна
# (диск не примонтирован, cmd.exe вне PATH), и молча пройти мимо этого
# нельзя.
NEED_WSL_RESTART=0

# Домашняя папка пользователя Windows глазами WSL. Через cmd.exe, а не
# разбором /mnt/c/Users: имя папки профиля не обязано совпадать с именем
# учётки, а профиль может лежать и не на C:. Переход в /mnt/c — не
# косметика: из /home cmd.exe ругается на UNC-путь и молча подставляет
# C:\Windows, после чего мы бы записали .wslconfig не туда. Но и жёстким
# условием он быть не должен: точка монтирования настраивается
# (/etc/wsl.conf, root=), и там, где её нет, спросить cmd.exe всё равно
# стоит — хуже пустого ответа уже не будет.
windows_home() {
  local raw
  raw="$( { cd /mnt/c 2>/dev/null || cd /; } && cmd.exe /c 'echo %UserProfile%' 2>/dev/null | tr -d '\r\n')"
  case "$raw" in
    ?:\\*) wslpath -u "$raw" 2>/dev/null ;;
    *) printf "" ;;
  esac
}

say "Сеть до прод-данных"
note "ClickHouse, Trino, Grafana и OpenMetadata видны только через Netbird. Клиент стоит на Windows, и это правильно — внутрь WSL его ставить не нужно"

WIN_HOME="$(windows_home)"
WSLCONFIG=""
[ -n "$WIN_HOME" ] && [ -d "$WIN_HOME" ] && WSLCONFIG="$WIN_HOME/.wslconfig"

if [ -z "$WSLCONFIG" ]; then
  # Не смогли дотянуться до Windows-стороны. Говорим ровно то, что знаем.
  fail "не нашёл домашнюю папку Windows — настрою сеть за тебя не смогу"
  note "Создай %UserProfile%\\.wslconfig блокнотом на стороне Windows:"
  note "    [wsl2]"
  note "    networkingMode=mirrored"
  note "    dnsTunneling=true"
  note "затем в PowerShell: wsl --shutdown — и открой Ubuntu заново"
elif [ -f "$WSLCONFIG" ] && grep -qE '^[[:space:]]*networkingMode[[:space:]]*=[[:space:]]*mirrored' "$WSLCONFIG"; then
  # Прописано — но применено ли, отсюда не видно: для этого нужен был
  # `wsl --shutdown` после правки. Утверждать «сеть в порядке» мы не имеем
  # права и не утверждаем; настоящую проверку сделает смоук-тест ClickHouse.
  ok "зеркальный режим сети уже прописан ($WSLCONFIG)"
elif [ -f "$WSLCONFIG" ]; then
  # Файл есть, но настройки в нём нет. Не переписываем: там может лежать
  # чужая конфигурация (лимиты памяти, ядро), и разбирать INI шеллом,
  # чтобы вложиться в нужную секцию, — способ испортить рабочий файл.
  fail "$WSLCONFIG уже есть, но зеркального режима в нём нет — не трогаю чужой файл"
  note "Допиши в него сам, в секцию [wsl2]:"
  note "    networkingMode=mirrored"
  note "    dnsTunneling=true"
  note "затем в PowerShell: wsl --shutdown — и открой Ubuntu заново"
  NEED_WSL_RESTART=1
else
  if printf '[wsl2]\nnetworkingMode=mirrored\ndnsTunneling=true\n' > "$WSLCONFIG" 2>/dev/null; then
    ok "создал $WSLCONFIG — WSL будет видеть сеть Windows вместе с туннелем Netbird"
    NEED_WSL_RESTART=1
  else
    fail "не смог записать $WSLCONFIG — создай его блокнотом на стороне Windows:"
    note "    [wsl2]"
    note "    networkingMode=mirrored"
    note "    dnsTunneling=true"
  fi
fi

if [ "$NEED_WSL_RESTART" = "1" ]; then
  note "Настройка сети применится только после перезапуска WSL — это в конце, сейчас продолжаю"
  note "Зеркальный режим требует Windows 11 22H2 или новее"
fi

# ── Пакеты ────────────────────────────────────────────────────────────────
#
# Ставим только недостающее и показываем команду до её запуска: sudo, тем
# более из чужого скрипта, не должен срабатывать молча.
NEED_APT=""
for pkg_cmd in "curl:curl" "git:git" "python3:python3"; do
  cmd="${pkg_cmd%%:*}"
  pkg="${pkg_cmd##*:}"
  if have_native "$cmd"; then
    ok "$cmd уже стоит"
  else
    NEED_APT="$NEED_APT $pkg"
  fi
done

if [ -n "$NEED_APT" ]; then
  say "Не хватает системных пакетов:$NEED_APT"
  note "Выполню: sudo apt-get update && sudo apt-get install -y$NEED_APT"
  note "sudo спросит пароль твоей учётки Ubuntu (её ты задавал при первом запуске WSL), а не пароль Windows"
  if ! sudo apt-get update -qq || ! sudo apt-get install -y $NEED_APT; then
    fail "не удалось поставить пакеты:$NEED_APT"
    fail "Поставь их сам и запусти ./install-windows.sh заново"
    exit 1
  fi
  ok "поставлено:$NEED_APT"
fi

# uv — обязателен: через него поднимается каждый из девяти коннекторов
# (`uv run` для наших, `uvx` для сторонних). Ставим официальным
# установщиком, а не из apt: в репозиториях Ubuntu он старый или его нет.
if have_native uv && have_native uvx; then
  ok "uv уже стоит"
else
  say "Ставлю uv (через него работают все коннекторы)"
  if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
    fail "не удалось поставить uv"
    exit 1
  fi
  # Установщик кладёт бинарь сюда и правит только профиль оболочки — в уже
  # запущенном процессе PATH сам не обновится, а он нужен прямо сейчас,
  # ниже по этому же скрипту.
  export PATH="$HOME/.local/bin:$PATH"
  have_native uv && ok "uv поставлен"
fi

# Node нужен для двух вещей сразу: им ставится Codex (npm install -g) и на
# нём работает коннектор growthbook (npx @growthbook/mcp). Ни то, ни другое
# не обязательно — поэтому отказ здесь не роняет установку.
if have_native npx; then
  ok "Node.js уже стоит"
else
  if [ -n "$(foreign_path npx)" ]; then
    note "npx нашёлся на стороне Windows ($(foreign_path npx)) — отсюда он не работает, ставлю Node внутрь Ubuntu"
  fi
  say "Ставлю Node.js 18+ (нужен для Codex и для коннектора growthbook)"
  if curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - &&
     sudo apt-get install -y nodejs; then
    ok "Node.js поставлен"
  else
    fail "не удалось поставить Node.js — Codex через npm поставить не выйдет, коннектор growthbook не поднимется"
    note "Остальное будет работать; вернуться к этому можно позже"
  fi
fi

# ── Движок ────────────────────────────────────────────────────────────────
#
# Спрашиваем, а не ставим оба: у нас на Windows пока приходят с Codex, и
# лишний движок — это лишний диалог доверия и лишний повод решить, что
# что-то настроено неправильно.
HAVE_CLAUDE=0
HAVE_CODEX=0
have_native claude && HAVE_CLAUDE=1
have_native codex  && HAVE_CODEX=1

# Движок стоит, но на стороне Windows. Молчать тут нельзя: раньше мы в
# этом месте решали «уже стоит» и пропускали установку — сессия потом
# падала с `exec: node: not found`, и связать одно с другим было нечем.
for eng in claude codex; do
  if ! have_native "$eng" && [ -n "$(foreign_path "$eng")" ]; then
    note "$eng найден на стороне Windows ($(foreign_path "$eng")) — отсюда он не запускается, поставлю внутрь Ubuntu"
  fi
done

if [ "$HAVE_CLAUDE" = "1" ] || [ "$HAVE_CODEX" = "1" ]; then
  [ "$HAVE_CODEX"  = "1" ] && ok "codex уже стоит"
  [ "$HAVE_CLAUDE" = "1" ] && ok "claude уже стоит"
else
  say "Движок не найден — нужен хотя бы один"
  printf "  1) Codex\n  2) Claude Code\n  3) оба\n"
  read -rp "  Что ставим? [1]: " ENGINE_CHOICE
  case "${ENGINE_CHOICE:-1}" in
    2) WANT="claude" ;;
    3) WANT="codex claude" ;;
    *) WANT="codex" ;;
  esac
  for engine in $WANT; do
    case "$engine" in
      codex)
        if npm install -g @openai/codex; then
          ok "Codex поставлен"
          HAVE_CODEX=1
        else
          fail "не удалось поставить Codex: npm install -g @openai/codex"
        fi
        ;;
      claude)
        if npm install -g @anthropic-ai/claude-code; then
          ok "Claude Code поставлен"
          HAVE_CLAUDE=1
        else
          fail "не удалось поставить Claude Code — поставь по инструкции с https://claude.com/code"
        fi
        ;;
    esac
  done
fi

if [ "$HAVE_CLAUDE" = "0" ] && [ "$HAVE_CODEX" = "0" ]; then
  fail "ни одного движка так и нет — ./setup.sh дальше не пойдёт"
  exit 1
fi

# ── Логин в Codex — до setup.sh, а не после ───────────────────────────────
#
# Порядок здесь не произволен. Мастер проверяет доверие хукам Codex живым
# запуском `codex exec` и без ~/.codex/auth.json честно отказывается его
# делать. Человек, установивший Codex и сразу запустивший ./setup.sh, читает
# в конце «Codex не авторизован» и понимает это как поломку установки.
if [ "$HAVE_CODEX" = "1" ] && [ ! -f "${CODEX_HOME:-$HOME/.codex}/auth.json" ]; then
  say "Codex ещё не авторизован — это нужно сделать сейчас, до мастера"
  note "Откроется браузер Windows. Если не откроется — Codex напечатает ссылку, скопируй её в браузер руками"
  codex login
  if [ ! -f "${CODEX_HOME:-$HOME/.codex}/auth.json" ]; then
    fail "войти не удалось. Установку это не останавливает, но проверить доверие хукам мастер не сможет"
    note "Сделай позже: codex login, затем ./setup.sh --add codex-hooks"
  else
    ok "Codex авторизован"
  fi
fi

# ── Дальше работает мастер ────────────────────────────────────────────────
#
# Но не всегда прямо сейчас. Если сеть мы только что настроили, мастер
# запускать бессмысленно: до перезапуска WSL старый сетевой стек остаётся
# на месте, смоук-тест ClickHouse не пройдёт, и человек получит красную
# строку про доступ, которого на самом деле у него нет только до
# перезагрузки. Лучше остановиться на понятном шаге, чем показать отказ,
# который ничего не значит.
#
# Всё остальное к этому моменту уже поставлено, поэтому возвращаться сюда
# не нужно — следующая команда сразу ./setup.sh.
if [ "$NEED_WSL_RESTART" = "1" ]; then
  say "Окружение готово. Остался один шаг — перезапустить WSL, иначе прод-данных не будет"
  cat <<EOF
  1. Закрой это окно и открой PowerShell:
       wsl --shutdown
  2. Открой Ubuntu заново и запусти мастер:
       cd $REPO_DIR && ./setup.sh
EOF
  note "Netbird при этом должен быть поднят на Windows как обычно"
  exit 0
fi

# exec, а не вызов: дальше всё происходит в setup.sh, и он должен получить
# терминал и код возврата напрямую. Свой обработчик поверх его вывода нам
# добавить нечего — а если добавится, он будет спорить с итогом мастера,
# который специально печатается самой последней строкой.
say "Окружение готово — передаю управление мастеру установки"
exec "$REPO_DIR/setup.sh" "$@"
