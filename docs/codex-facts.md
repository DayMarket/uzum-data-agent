# Разведка Codex: факты, полученные запуском

> Дата: 07.08.2026. Машина: macOS 26.4 (aarch64), Node v22.22.3, npm 10.9.8.
> Правило этого документа: если факт не подтверждён запуском — он помечен явно как предположение/непроверено. Ничего не выдаётся за факт по умолчанию.

---

## 1. Установка Codex

**Статус: сделано, не BLOCKED.**

В PATH стояла обёртка стороннего инструмента Superset (`~/.superset/bin/codex`), которая сама печатает `Superset: codex not found in PATH` и завершается кодом 127, если настоящего бинаря нет нигде в PATH за вычетом её собственных `.superset/bin` каталогов (это видно из её кода — она явно пропускает `~/.superset/bin` и `~/.superset-*/bin` при поиске `find_real_binary`).

Установил настоящий Codex CLI командой:

```bash
npm install -g @openai/codex
```

Результат:
```
added 2 packages in 23s
```

Бинарь встал в `/opt/homebrew/bin/codex` (npm prefix `/opt/homebrew`) — симлинк на `../lib/node_modules/@openai/codex/bin/codex.js`.

Проверка версии:
```bash
$ codex --version
codex-cli 0.147.0
```

Важный нюанс для инструкции аналитикам: `~/.superset/bin` стоит в PATH раньше `/opt/homebrew/bin`, поэтому команда `codex` по-прежнему сначала попадает в обёртку Superset — но это не проблема: обёртка сама ищет настоящий бинарь по остальному PATH и передаёт ему управление, поэтому `codex --version` через голую команду `codex` тоже отработал:
```bash
$ which -a codex
/Users/anastasiabir/.superset/bin/codex
/opt/homebrew/bin/codex
$ codex --version
codex-cli 0.147.0
```

`codex doctor` подтвердил консистентную установку (`install: consistent`, `managed by npm: yes`).

**Авторизация: BLOCKED для всего, что требует реального обращения к модели.**

```bash
$ codex login status
Not logged in
$ codex doctor
...
Notes
   ✗ auth         no Codex credentials were found - Run codex login or provide an API key through a supported auth env var.
```

Проверил переменные окружения — `OPENAI_API_KEY` и любые `CODEX_*`-токены отсутствуют. Попытка `codex exec "say hi"` подтверждает: соединение падает на аутентификации, а не на чём-то другом:
```
2026-08-07T12:13:03Z ERROR ... failed to connect to websocket: HTTP error: 401 Unauthorized, url: wss://api.openai.com/v1/responses
```

**Что нужно, чтобы разблокировать:** либо `codex login` с браузерным OAuth от рабочего ChatGPT/OpenAI-аккаунта с оплаченной подпиской (интерактивный, у агента браузера нет), либо `OPENAI_API_KEY`, переданный через `printenv OPENAI_API_KEY | codex login --with-api-key`. Ни того ни другого в этой среде нет.

**Способ установки для инструкции аналитикам:** `npm install -g @openai/codex` (альтернатива — `brew install --cask codex`, доступна в Homebrew, версия в кэше `0.146.1`, не проверял её живьём, т.к. уже стоит npm-версия и ставить вторую бессмысленно).

---

## 2. Содержимое события хука в обоих движках

### Claude Code — проверено живым запуском

Собрал хук-логгер (`.claude/hooks/logger.py`), который дописывает JSON со stdin в `events.jsonl`, зарегистрировал на `SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SessionEnd` во временном проекте (`.claude/settings.json`), прогнал:
```bash
claude -p "<промт с Bash echo, Bash exit 9, Read несуществующего файла>" --dangerously-skip-permissions --allowedTools "Bash Read"
```

Получил реальные payload'ы. Пример `PostToolUse` (единственный успешный вызов, `echo hello-hook-test`):
```json
{
  "session_id": "32190b20-...",
  "transcript_path": "/Users/anastasiabir/.claude/projects/.../32190b20-....jsonl",
  "cwd": "/private/tmp/.../claude-hooktest",
  "hook_event_name": "PostToolUse",
  "tool_name": "Bash",
  "tool_input": {"command": "echo hello-hook-test", "description": "..."},
  "tool_response": {"stdout": "hello-hook-test", "stderr": "", "interrupted": false, "isImage": false, "noOutputExpected": false},
  "tool_use_id": "toolu_01L9...",
  "duration_ms": 51
}
```

`SessionEnd`:
```json
{"session_id": "...", "transcript_path": "...", "hook_event_name": "SessionEnd", "reason": "other"}
```

### Codex — частично проверено (без модели), частично не проверено (BLOCKED)

Живой сессии с моделью снять не удалось (см. п.1, BLOCKED). Но структуру hook-событий Codex удалось получить из **самого установленного бинаря** статическим анализом (`strings` по `codex.js`-таргету `codex-darwin-arm64/vendor/.../bin/codex`):

```bash
strings <бинарь-codex> | grep -oE "session_id|transcript_path|hook_event_name|tool_name|tool_input|tool_response|tool_use_id|duration_ms|exit_code|stderr|is_error"
```

нашёл в одном непрерывном куске байт (сериализованные имена полей серде-структуры хука):
```
session_id turn_id agent_type transcript_path cwd hook_event_name model permission_mode
trigger tool_name tool_input tool_use_id ... tool_response hookEventName permissionDecision
```
и отдельно (у другой структуры, `PostToolUseCommandOutputWire`): `duration_ms`/`durationMs`, `exit_code`/`exitCode`, `stderr`, `is_error`/`isError`, `success`.

Это даёт основания полагать (**не факт, вывод из статического анализа кода, не из запуска**), что имена полей в Codex почти дословно совпадают с Claude Code — тот же `snake_case`, те же имена `session_id`, `transcript_path`, `tool_name`, `tool_input`, `tool_response`, `duration_ms`. Отдельного поля `tool_error` в строках бинаря не нашёл ни у Codex, ни (что важно) в реальном payload'е Claude Code — там текст ошибки, если он вообще доходит до хука, физически нет: в живом Claude Code `PostToolUse` в принципе не вызывается для упавших инструментов (см. п.3) — то есть колонка "текст ошибки" у обоих движков как минимум под вопросом, а не просто "неправильное имя поля".

### Таблица сравнения

| Поле | Claude Code (живой запуск) | Codex (статический анализ бинаря, НЕ живой запуск) | Совпадает? |
|---|---|---|---|
| ID сессии | `session_id`, есть в каждом событии | `session_id` — имя поля есть в бинаре | похоже да, но Codex-значение не проверено живьём |
| Путь к транскрипту | `transcript_path`, абсолютный путь до `.jsonl` в `~/.claude/projects/...` | `transcript_path` — имя поля есть в бинаре | похоже да, но не проверено живьём |
| Имя инструмента | `tool_name` | `tool_name` — есть в бинаре | похоже да, не проверено живьём |
| Вход инструмента | `tool_input` (объект, специфичный для тула) | `tool_input` — есть в бинаре | похоже да, не проверено живьём |
| Текст ошибки | **Поля `tool_error` в реальных событиях НЕТ.** `tool_response` содержит `stdout`/`stderr`/`interrupted`, но хук **не вызывается вовсе**, когда инструмент падает (см. п.3) | Отдельного `tool_error` в строках бинаря не нашёл; есть `stderr`, `is_error`/`isError`, `exit_code`/`exitCode` рядом с `PostToolUseCommandOutputWire` | НЕ ПРОВЕРЕНО для Codex; для Claude Code факт — поля с таким именем не существует, и хук на ошибке не срабатывает вовсе |
| Длительность | `duration_ms`, только в `PostToolUse` при успехе | `duration_ms`/`durationMs` — есть в бинаре | похоже да, не проверено живьём |
| Причина завершения | `reason` в `SessionEnd` (наблюдал только значение `"other"` при выходе из `-p`-режима) | `reason`-подобные строки встречаются в бинаре рядом с `SessionEnd`, но конкретный набор значений не вытащил | похоже да по имени поля, набор значений не проверен |

---

## 3. Есть ли `PostToolUseFailure`

**Официальный список событий Codex (из брифа) не содержит `PostToolUseFailure` — и статический анализ бинаря это подтверждает.** Извлёк из бинаря Codex полный перечень строк, входящих в enum имён hook-событий (`HookEventName`/`HookEventNameWire`):

```bash
strings <бинарь-codex> | grep -oE "PreToolUse|PostToolUse[A-Za-z]*|SessionStart|SessionEnd|SubagentStart|SubagentStop|UserPromptSubmit|PreCompact|PostCompact|PermissionRequest|Stop" | sort -u
```
Результат — ровно: `PermissionRequest, PostCompact, PostToolUse, PreCompact, PreToolUse, SessionEnd, SessionStart, Stop, SubagentStart, SubagentStop, UserPromptSubmit`. **`PostToolUseFailure` в списке нет** ни разу, ни как отдельное имя, ни как часть составной строки. Это статическое доказательство (дизассемблирование установленного бинаря 0.147.0), не живой запуск — но это самый надёжный источник, доступный без логина: сам код, который будет исполняться.

**Живым запуском с падающим инструментом это подтвердить не смог** (нужна модель → нужна аутентификация → BLOCKED, см. п.1).

**Зато для Claude Code получил неожиданный и важный факт живым запуском**, который отвечает на смежный вопрос "что происходит при падении инструмента":

Изолированный тест — один Bash-вызов `exit 9` (гарантированно падает):
```bash
claude -p "вызови Bash 'exit 9' и сразу остановись" --dangerously-skip-permissions --allowedTools "Bash"
```
Зарегистрированы были: `SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, PermissionRequest, Stop, SessionEnd`. Реально сработали:
```
0 SessionStart
1 UserPromptSubmit
2 PreToolUse
3 Stop
4 SessionEnd
```
**`PostToolUse` не сработал вообще** — ни в этом изолированном тесте, ни в комбинированном тесте с тремя шагами (успешный echo → `PostToolUse` сработал; `exit 7` → не сработал; `Read` несуществующего файла → не сработал). Повторил трижды с одинаковым результатом. Значит в текущей версии Claude Code (2.1.224) при ошибке инструмента `PostToolUse` попросту не вызывается — ни под своим именем, ни под именем `PostToolUseFailure` (такого события в списке хуков Claude Code не существует в принципе, а `PostToolUse` в момент ошибки молчит). Текст ошибки в хуках Claude Code в моих тестах увидеть не удалось вообще — только в самом ответе модели, не в hook payload.

**Для Codex это осталось непроверенным фактом-предположением**: если поведение аналогично, ловить сбои из `PostToolUse` (как предлагает риск-таблица в спеке) может не сработать и для Codex тоже — но подтвердить или опровергнуть это можно только живой сессией с логином.

---

## 4. Как в конфиг Codex попадают секреты

**Проверено полностью запуском, без логина** — эта часть не требует общения с моделью, потому что MCP-серверы Codex поднимаются локально (как отдельные процессы) до/независимо от обращения к API модели.

### `${VAR}`-подстановка НЕ работает

Собрал “фейковый” MCP-сервер — shell-скрипт, который сбрасывает полученные переменные окружения в файл и завершается. Зарегистрировал его в `config.toml` (через `CODEX_HOME=<temp>`):
```toml
[mcp_servers.testenv]
command = "/private/.../mcp_env_stub.sh"

[mcp_servers.testenv.env]
TEST_PLAIN = "${TEST_PLAIN}"
TEST_SPACE = "${TEST_SPACE}"
TEST_DOLLAR = "${TEST_DOLLAR}"
TEST_APOSTROPHE = "${TEST_APOSTROPHE}"
TEST_BACKTICK = "${TEST_BACKTICK}"
TEST_LITERAL_VAR_SYNTAX = "literal-no-braces-$HOME-end"
```
Экспортировал в шелл реальные `TEST_PLAIN=plainvalue`, `TEST_SPACE="value with spaces"` и т.д., запустил `codex exec "say hi"` (фоном, 6 секунд, оборвал — достаточно, чтобы MCP-сервер успел стартовать до 401 на модели). Дамп, который сделал сам stub-процесс:
```
TEST_PLAIN=[${TEST_PLAIN}]
TEST_SPACE=[${TEST_SPACE}]
TEST_DOLLAR=[${TEST_DOLLAR}]
TEST_APOSTROPHE=[${TEST_APOSTROPHE}]
TEST_BACKTICK=[${TEST_BACKTICK}]
TEST_LITERAL_VAR_SYNTAX=[literal-no-braces-$HOME-end]
```
**Итог: `env = {...}` в `config.toml` передаётся дочернему процессу буквально, как строка. Никакой подстановки `${VAR}` из окружения Codex не делает** — ни для `${VAR}`, ни для голого `$VAR`. Это прямо противоположно поведению `.mcp.json` у Claude Code, где `${VAR}` подставляется.

### Настоящий механизм — `env_vars` (список имён)

В бинаре нашёл отдельное поле `env_vars` (в отличие от `env`) в структуре описания MCP-сервера. Проверил:
```toml
[mcp_servers.testenv]
command = "/private/.../mcp_env_stub.sh"
env_vars = ["TEST_PLAIN", "TEST_SPACE", "TEST_DOLLAR", "TEST_APOSTROPHE", "TEST_BACKTICK"]
```
`codex mcp list` / `codex mcp get testenv` приняли конфиг без ошибок схемы и замаскировали значения в выводе:
```
env: TEST_PLAIN=*****, TEST_SPACE=*****, TEST_DOLLAR=*****, TEST_APOSTROPHE=*****, TEST_BACKTICK=*****
```
Повторный запуск `codex exec` с реальными переменными в окружении и `env_vars`-списком дал в дампе stub-процесса:
```
TEST_PLAIN=[plainvalue]
TEST_SPACE=[value with spaces]
TEST_DOLLAR=[price$100]
TEST_APOSTROPHE=[it's a test]
TEST_BACKTICK=[back`tick`value]
```
**Все спецсимволы (пробел, `$`, апостроф, обратная кавычка) дошли до дочернего процесса без искажений.** Это и есть правильный способ передать секрет в Codex, не записывая его значение в файл: перечислить *имя* переменной в `env_vars = [...]`, значение при этом должно быть уже экспортировано в окружении процесса, который запускает `codex`.

**Вывод для дизайна:** модель хранения секретов из спеки (переменные из `.env`, подстановка `${VAR}`) для Claude Code остаётся как есть, но для `.codex/config.toml` генератор мастера должен писать не `env = { KEY = "${KEY}" }`, а `env_vars = ["KEY", ...]`, и сам процесс, запускающий `codex`, должен получить `KEY` в собственном окружении (например, через `source .env` перед запуском, а не через подстановку внутри TOML).

---

## 5. Симлинки скиллов

**Полностью проверено запуском, для обоих движков.**

Создал `/tmp/.../skilltest-src/.agents/skills/recon-probe-skill/SKILL.md` и симлинк `.claude/skills/recon-probe-skill -> ../../.agents/skills/recon-probe-skill`.

**Claude Code видит скилл через симлинк** — живой запуск:
```bash
claude -p "У тебя есть кастомный скилл recon-probe-skill? Вызови его и процитируй строку." --dangerously-skip-permissions --allowedTools "Skill"
```
Ответ:
```
Да, скилл есть и он вызвался.
Путь: .../.claude/skills/recon-probe-skill
Цитата: «Если ты видишь этот файл — симлинк или прямой путь сработал.»
```

**Codex видит скилл напрямую по своему пути `.agents/skills/...`**, проверено без логина через `codex debug prompt-input` (эта команда рендерит то, что реально пойдёт в промпт модели — локальная операция, не требует сети/аутентификации):
```bash
CODEX_HOME=<temp> codex debug prompt-input
```
В `<skills_instructions>` среди списка навыков нашёлся:
```
- recon-probe-skill: Диагностический скилл для проверки видимости через симлинк... 
  (file: /private/.../skilltest-src/.agents/skills/recon-probe-skill/SKILL.md)
```
т.е. Codex обнаружил скилл сам, по прямому пути `.agents/skills/`, без обращения к `.claude/skills`-симлинку (что и ожидалось — у Codex своё дерево обнаружения).

**Симлинк переживает `git clone`.** `git ls-files -s` показал режим `120000` (symlink) для `.claude/skills/recon-probe-skill`:
```
100644 ... .agents/skills/recon-probe-skill/SKILL.md
120000 ... .claude/skills/recon-probe-skill
```
После `git init && git add -A && git commit` и `git clone` в соседнюю папку — симлинк в клоне рабочий:
```bash
$ readlink .claude/skills/recon-probe-skill
../../.agents/skills/recon-probe-skill
$ cat .claude/skills/recon-probe-skill/SKILL.md   # читается через симлинк
```
и `codex debug prompt-input`, запущенный уже в клоне, снова нашёл `recon-probe-skill` по пути `<клон>/.agents/skills/recon-probe-skill/SKILL.md`.

**Итог: оба движка видят единственный экземпляр скилла, симлинк переживает клонирование. Ключевой факт из спеки подтверждён живым запуском для обоих движков**, а не только для Claude Code, как предполагалось в спеке изначально.

---

## Что проверить не удалось (и почему)

| Что | Почему не проверено |
|---|---|
| Живой payload хуков Codex (`SessionStart`, `UserPromptSubmit`, `PostToolUse`, `SessionEnd`) с реальными значениями полей | Codex не авторизован (нет доступа к ChatGPT/OpenAI аккаунту с подпиской и нет `OPENAI_API_KEY`). Все hook-события в Codex требуют активной сессии с моделью — MCP-серверы стартуют без модели (см. п.4), но хуки на события сессии/тула — нет, `codex debug prompt-input` их не эмулирует, это просмотрщик промпта, не хуков. |
| Реальное срабатывание (или несрабатывание) `PostToolUse` у Codex при падении инструмента | То же самое — нужна живая сессия с моделью, которая вызовет падающий инструмент |
| Конкретные значения `session_id`/`transcript_path`/`duration_ms` и т.п. у Codex "вживую" | То же самое |
| Полный набор значений поля `reason`/`stopReason` у обоих движков (видел только `"other"` у Claude Code) | Не воспроизвёл остальные пути завершения (logout, clear, ошибка) за отведённое время |
| `brew install --cask codex` как альтернативный способ установки | Не устанавливал вторую копию — уже стоит npm-версия, ставить конфликтующую бессмысленно; факт наличия пакета в brew (`0.146.1`) подтверждён `brew info`, но сама установка этим способом не проверялась |
| Поведение `codex login --with-api-key` "вживую" | Нет реального `OPENAI_API_KEY` для проверки — использовать выдуманный ключ бессмысленно, т.к. он не пройдёт реальную аутентификацию у OpenAI |

## Явно помеченные предположения (не факты)

- Что имена полей хука Codex (`session_id`, `transcript_path`, `tool_name`, `tool_input`, `tool_response`, `duration_ms`) **в реальном payload'е** совпадают буквально с тем, что лежит в бинаре как строки сериализации serde-структуры — весьма вероятно, но не эквивалентно живому наблюдению: имя поля в бинаре доказывает, что оно *может* появиться в JSON с таким ключом, но не доказывает, при каких условиях оно появляется, чем заполняется и не пусто ли оно.
- Что поведение Codex "PostToolUse не срабатывает при ошибке" аналогично Claude Code — это перенос наблюдения с одного движка на другой безо всякой проверки; так же вероятно и обратное.
- Что набор значений `reason`/`stopReason` у Codex такой же, как подсказывают строки бинаря (`clear`, `logout`, `prompt_input_exit`, `other`) — эти строки в бинаре не нашёл, догадка не подтвердилась и не опровергнута.
