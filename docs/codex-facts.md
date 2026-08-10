# Разведка Codex: факты, полученные запуском

> Дата: 07.08.2026. Машина: macOS 26.4 (aarch64), Node v22.22.3, npm 10.9.8.
> Правило этого документа: если факт не подтверждён запуском — он помечен явно как предположение/непроверено. Ничего не выдаётся за факт по умолчанию.
> **Обновление 07.08.2026 (вечер):** владелец выполнила `codex login` вручную, блокер по авторизации снят. Раздел 2, 3, часть раздела 5 и раздел "Формат транскрипта" переснял живым запуском (`codex exec`) вместо статического анализа бинаря. Старые статические находки оставлены как перепроверенные — где живой запуск их подтвердил, это отмечено явно; расхождения со статикой — тоже.
> **Обновление 07.08.2026 (ночь):** отдельно разобрал диалог доверия хукам (раздел 7) — интерактивный `codex` гонял через `pexpect`/`pyte` (единственный сторонний инструментарий за всю разведку), т.к. интерактивный TUI не отдаёт осмысленный текст без эмуляции терминала.

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

**Обновление: блокер снят.** Владелец выполнила `codex login` вручную на этой машине:
```bash
$ codex login status
Logged in using ChatGPT
$ codex doctor | grep -A2 "✓ auth"
  ✓ auth         auth is configured
      auth storage mode        File
      auth file                ~/.codex/auth.json
      stored auth mode         chatgpt
```
Для экспериментов ниже (разделы 2, 3, часть 5) не трогал реальный `~/.codex` — поднял изолированный `CODEX_HOME` во временной папке и скопировал в него только `auth.json`, чтобы унаследовать авторизацию без риска для рабочего окружения:
```bash
cp ~/.codex/auth.json <temp>/codex-home-live/auth.json
CODEX_HOME=<temp>/codex-home-live codex login status   # → Logged in using ChatGPT
```

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

### Codex — проверено живым запуском (обновление после снятия блокера)

Собрал точно такой же хук-логгер, зарегистрировал его в `hooks.json` изолированного `CODEX_HOME`:
```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 <scratch>/codex_logger.py"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 <scratch>/codex_logger.py"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "python3 <scratch>/codex_logger.py"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 <scratch>/codex_logger.py"}]}]
  }
}
```

**Первая находка ещё до payload'ов — хуки Codex нужно явно доверить, иначе они молча не срабатывают.** Первый прогон `codex exec` в новом `CODEX_HOME` не записал ни одной строки в лог хука — не ошибка конфига, а встроенный защитный механизм: хуки, ещё не прошедшие проверку доверия, Codex просто не запускает и не сообщает об этом никак в обычном выводе. Обнаружил это через `codex --help`:
```
--dangerously-bypass-hook-trust
    Run enabled hooks without requiring persisted hook trust for this invocation. DANGEROUS.
```
С этим флагом хуки заработали. **Это отдельный, важный для мастера установки факт**: генератор конфигов Codex должен либо провести пользователя через интерактивное доверие хукам (в интерактивном `codex`, не проверял отдельно), либо автоматизация (headless-запуски, CI, `codex exec`) должна использовать `--dangerously-bypass-hook-trust` осознанно — иначе хуки будут молча не работать, и это ещё один способ "колонка тихо остаётся пустой", тот же класс дефекта, что уже ловили на `https`/`tool_error`.

Команда воспроизведения (успешный вызов):
```bash
codex exec "Выполни ровно одну shell-команду: echo hello-codex-hook-test. Затем остановись." \
  -s workspace-write --skip-git-repo-check --dangerously-bypass-hook-trust
```

Реальный `PostToolUse` (успех):
```json
{"session_id": "019fdcd6-258d-7873-b324-82bbef4736b7",
 "turn_id": "019fdcd6-25bc-7f73-aa2a-fd3b7dd43106",
 "transcript_path": ".../codex-home-live/sessions/2026/08/07/rollout-2026-08-07T19-27-29-019fdcd6-258d-7873-b324-82bbef4736b7.jsonl",
 "cwd": ".../codex-project",
 "hook_event_name": "PostToolUse",
 "model": "gpt-5.6-sol",
 "permission_mode": "bypassPermissions",
 "tool_name": "Bash",
 "tool_input": {"command": "echo hello-codex-hook-test-2"},
 "tool_response": "hello-codex-hook-test-2\n",
 "tool_use_id": "exec-7338f5f5-1024-486e-9d64-0182641355b0"}
```

`SessionStart`:
```json
{"session_id": "019fdcd6-258d-...", "transcript_path": ".../rollout-....jsonl", "cwd": "...", "hook_event_name": "SessionStart", "model": "gpt-5.6-sol", "permission_mode": "bypassPermissions", "source": "startup"}
```

`SessionEnd`:
```json
{"session_id": "019fdcd6-258d-...", "transcript_path": ".../rollout-....jsonl", "cwd": "...", "hook_event_name": "SessionEnd", "reason": "other"}
```

`PostToolUse` при падении инструмента (`exit 9`, воспроизведено, см. раздел 3):
```json
{"...": "...", "hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "exit 9"},
 "tool_response": "", "tool_use_id": "exec-f44253d2-20ef-4636-9a93-786e39701991"}
```

### Таблица сравнения (обе стороны — живой запуск)

| Поле | Claude Code (живой запуск) | Codex (живой запуск, `codex exec` + hook-логгер) | Совпадает? |
|---|---|---|---|
| ID сессии | `session_id`, UUID v4 (`32190b20-ba76-4975-8ff7-...`) | `session_id`, тоже есть в каждом событии, но формат другой — time-ordered ID (`019fdcd6-258d-7873-...`, похож на UUIDv7) | имя поля совпадает, формат значения — нет |
| Путь к транскрипту | `transcript_path` → `~/.claude/projects/<slug>/<session_id>.jsonl`, один плоский файл на проект | `transcript_path` → `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<timestamp>-<session_id>.jsonl`, раскладка по датам | имя поля совпадает, путь и раскладка — нет; оба JSONL |
| Имя инструмента | `tool_name`, напр. `"Bash"`, `"Read"` | `tool_name`, в hook-payload тоже `"Bash"` (хотя в транскрипте это же событие называется `custom_tool_call` / `name: "exec"` — hook и транскрипт называют один и тот же вызов по-разному, см. раздел про транскрипт) | имя поля совпадает; значение в hook совпало по счастливой случайности словаря, но источники истины разные |
| Вход инструмента | `tool_input`: `{"command": "...", "description": "..."}` | `tool_input`: `{"command": "..."}`, без `description` | имя поля совпадает, состав уже |
| Текст ошибки | Поля `tool_error` нет вообще. `tool_response` — объект `{stdout, stderr, interrupted, isImage, noOutputExpected}`. При ошибке инструмента хук `PostToolUse` **не вызывается совсем** (см. раздел 3) | Поля `tool_error` тоже нет. `tool_response` — не объект, а **голая строка** (только stdout). При ошибке (`exit 9`) хук `PostToolUse` **вызывается**, но `tool_response` — пустая строка `""`, без кода выхода, без признака ошибки, без stderr (см. раздел 3, где на самом деле лежит текст ошибки) | НЕ совпадает ни по имени (`tool_error` нет нигде), ни по типу значения (`object` vs `string`), ни по поведению (не вызывается / вызывается пусто) |
| Длительность | `duration_ms`, целое число мс, есть в `PostToolUse`, но только при успехе | Поля `duration_ms`/`durationMs` в `PostToolUse` **нет вообще** — ни при успехе, ни при ошибке. Длительность есть только в транскрипте, на уровне всего turn'а (`event_msg` с `type: "task_complete"`, поле `duration_ms`), не на уровне отдельного тула | НЕ совпадает: у Claude Code — в хуке, на уровне тула; у Codex — в хуке нет вовсе, есть только в транскрипте, на уровне turn'а |
| Причина завершения | `reason` в `SessionEnd`, наблюдал `"other"` (выход из `-p`) | `reason` в `SessionEnd`, наблюдал **тоже** `"other"` (выход из `exec`) | имя поля и наблюдённое значение совпадают; полный набор значений не проверен ни там ни там |
| Модель, `turn_id`, `permission_mode` | Отсутствуют как отдельные поля в этом виде (модель не передаётся в hook payload Claude Code) | Есть дополнительно: `model` (`"gpt-5.6-sol"`), `turn_id`, `permission_mode` (`"bypassPermissions"` — то же слово, что и у Claude Code, хотя флаг для получения этого режима был другой: `-s workspace-write`, а не `--dangerously-skip-permissions`) | Codex-специфичные лишние поля, у Claude Code их нет |

---

## 3. Есть ли `PostToolUseFailure`

**Официальный список событий Codex (из брифа) не содержит `PostToolUseFailure` — и статический анализ бинаря это подтверждает.** Извлёк из бинаря Codex полный перечень строк, входящих в enum имён hook-событий (`HookEventName`/`HookEventNameWire`):

```bash
strings <бинарь-codex> | grep -oE "PreToolUse|PostToolUse[A-Za-z]*|SessionStart|SessionEnd|SubagentStart|SubagentStop|UserPromptSubmit|PreCompact|PostCompact|PermissionRequest|Stop" | sort -u
```
Результат — ровно: `PermissionRequest, PostCompact, PostToolUse, PreCompact, PreToolUse, SessionEnd, SessionStart, Stop, SubagentStart, SubagentStop, UserPromptSubmit`. **`PostToolUseFailure` в списке нет** ни разу, ни как отдельное имя, ни как часть составной строки. Это статическое доказательство (дизассемблирование установленного бинаря 0.147.0), не живой запуск — но это самый надёжный источник, доступный без логина: сам код, который будет исполняться.

**Обновление — проверено живым запуском на Codex после снятия блокера.** Вызвал заведомо падающий инструмент:
```bash
codex exec "Выполни ровно одну shell-команду: exit 9. Она упадёт с кодом 9 — это ожидаемо, зафиксируй результат. Затем остановись." \
  -s workspace-write --skip-git-repo-check --dangerously-bypass-hook-trust
```
Живой вывод самого `codex exec` (видно по строкам `hook: ...`):
```
exec
/bin/zsh -c 'exit 9' in .../codex-project
 exited 9 in 0ms:
hook: PostToolUse
hook: PostToolUse Completed
codex
Команда завершилась с ожидаемым кодом `9`. Вывода нет.
```
**Факт: у Codex `PostToolUse` срабатывает и при падении инструмента** (в отличие от Claude Code, см. ниже) — событие `PostToolUseFailure` для этого не нужно, `PostToolUse` покрывает оба случая. Но:

**Текста ошибки/кода выхода в самом hook payload нет.** Реальный payload `PostToolUse` для упавшей команды:
```json
{"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "exit 9"},
 "tool_response": "", "tool_use_id": "exec-f44253d2-20ef-4636-9a93-786e39701991"}
```
`tool_response` — пустая строка. Никакого `exit_code`, `is_error`, `stderr` в hook-событии нет, хотя эти имена полей и встречались в бинаре статическим анализом (структура `PostToolUseCommandOutputWire`) — на практике в `PostToolUse`-хуке они не заполняются вообще, во всяком случае для локального shell-тула в этой версии.

**Где текст ошибки реально есть — в транскрипте (rollout JSONL), не в хуке.** Разобрал файл из `transcript_path` той же сессии и нашёл пару `response_item`:
```json
{"type": "response_item", "payload": {"type": "custom_tool_call", "name": "exec",
  "input": "const r = await tools.exec_command({\"cmd\":\"exit 9\",...});\ntext(JSON.stringify(r));\n", ...}}
{"type": "response_item", "payload": {"type": "custom_tool_call_output",
  "output": [{"type": "input_text", "text": "Script completed\nWall time 0.1 seconds\nOutput:\n"},
             {"type": "input_text", "text": "{\"chunk_id\":\"d5be48\",\"wall_time_seconds\":0.000002792,\"exit_code\":9,\"original_token_count\":0,\"output\":\"\"}"}], ...}}
```
Код выхода (`"exit_code":9`) лежит **внутри второго текстового элемента массива `output`, который сам является JSON-строкой** — то есть нужно распарсить транскрипт и раскодировать вложенный JSON, а не просто прочитать поле верхнего уровня.

**Вывод для дизайна хуков (пункт риска из спеки "теряется главное: где падает"):** в Codex `PostToolUse` ловит сам факт падения (событие приходит), но не даёт код ошибки/текст — для колонки "где падает" нужно либо парсить транскрипт по `transcript_path` после каждого хука, либо смириться с тем, что в реальном времени (из хука) видно только "что инструмент назывался Х", без деталей ошибки.

**Для Claude Code получил обратный по форме, но тоже важный факт живым запуском**, который отвечает на смежный вопрос "что происходит при падении инструмента":

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
**`PostToolUse` не сработал вообще** — ни в этом изолированном тесте, ни в комбинированном тесте с тремя шагами (успешный echo → `PostToolUse` сработал; `exit 7` → не сработал; `Read` несуществующего файла → не сработал). Повторил трижды с одинаковым результатом. Значит в текущей версии Claude Code (2.1.224) при ошибке инструмента `PostToolUse` попросту не вызывается — ни под своим именем, ни под именем `PostToolUseFailure` (такого события в списке хуков Claude Code не существует в принципе, а `PostToolUse` в момент ошибки молчит). Текст ошибки в хуках Claude Code в моих тестах увидеть не удалось вообще — только в самом ответе модели, не в hook payload; в транскрипте Claude Code (см. следующий раздел) текст ошибки, наоборот, есть — в `tool_result.content` с `is_error: true`.

**Итог по обоим движкам (оба — живой запуск, вопрос закрыт):**
- Claude Code: `PostToolUse` при ошибке не вызывается вовсе → колонку "где падает" из хуков в реальном времени не построить, только постфактум разбором транскрипта.
- Codex: `PostToolUse` при ошибке вызывается, но без деталей ошибки в payload → колонку "где падает" тоже не построить из одного хука, нужен разбор транскрипта по `transcript_path`, который хук как раз и даёт.
- Практическое следствие одинаковое для дизайна: код хуков в общем ядре обязан после `PostToolUse` (Codex) или после `Stop`/`SessionEnd` (Claude Code, раз `PostToolUse` молчит) дочитывать транскрипт по `transcript_path`, а не полагаться на то, что текст ошибки придёт в самом событии хука.

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

### `env_vars` не умеет переименовывать — и это стоило шести коннекторов (проверено живым запуском)

Дополнение по итогам живой приёмки. Из «переслать под тем же именем» следует то, чего в первой формулировке не было сказано прямо: **всё, что отличает имя у аналитика от имени, которого ждёт процесс коннектора, обязано случиться ДО запуска `codex`.** `source secrets.env` этого не делает — он кладёт в окружение наши имена (`JIRA_TOKEN`), а `uvx mcp-atlassian` ждёт `JIRA_PERSONAL_TOKEN`. То же с дефолтами: `${JIRA_URL:-https://jira.uzum.com}` в `.mcp.json` разворачивает Claude Code, а в `config.toml` подставлять дефолт некому.

Снято в изолированной песочнице (свой `CODEX_HOME`, свой `HOME`, клон репозитория; в `secrets.env` заполнены только `CH_WMS_*` и `JIRA_TOKEN`), один и тот же профиль, одна и та же формулировка запроса — разница только в том, кто запускает движок:

```
$ codex -p uzum exec "У тебя есть MCP-сервер atlassian. Перечисли названия
  всех его инструментов (tools)…" < /dev/null
  → В текущем реестре нет инструментов MCP-сервера `atlassian`.

$ bin/uzum --codex exec "<тот же запрос>" < /dev/null
  → Доступно 98 инструментов:
    mcp__atlassian__confluence_add_comment
    …
    mcp__atlassian__jira_update_version
```

98 инструментов, включая `confluence_*` — то есть доехали и переименованный секрет (`CONFLUENCE_PERSONAL_TOKEN` ← `JIRA_TOKEN`), и дефолтные адреса (`JIRA_URL`, `CONFLUENCE_URL`), которых в `secrets.env` нет вовсе. Мостик — `connectors/codex_env_bridge.py`, вызывается из `bin/uzum` перед `execvpe`.

**Практическое следствие для человека:** голый `codex -p uzum` в папке репозитория — это НЕ то же самое, что `uzum --codex`. Профиль разрешений он подхватит, а коннекторы получит пустыми. Тексты мастера, советовавшие «запусти просто codex -p uzum», исправлены.

### Глобальный `-p` стоит перед подкомандой (проверено живым запуском)

`codex -p uzum exec "<промпт>"` работает: профиль применяется (`sandbox: custom permissions` в шапке), подкоманда `exec` отрабатывает как обычно. Именно в таком порядке аргументы собирает `bin/uzum`.

### MCP-сервер, не успевший стартовать, просто отсутствует в сессии — без единого сообщения (проверено живым запуском)

Найдено случайно, при проверке доставки конфига после `./setup.sh --add trino`. Коннектор был в `$CODEX_HOME/uzum.config.toml`, но сессия его не видела: «Инструменты MCP-сервера `trino` в текущем реестре отсутствуют». Ни ошибки, ни предупреждения, ни строки в баннере.

Причина — холодное окружение `uv`: `uv run connectors/trino_proxy.py` при первом запуске разрешает и скачивает зависимости из PEP 723-заголовка, и это дольше, чем Codex ждёт старта MCP-сервера. Воспроизведено в обе стороны, на одном и том же конфиге и одном и том же запросе:

| Состояние кэша `uv` | Что видит сессия |
|---|---|
| очищен (`rm -rf $HOME/.cache/uv`) | инструментов `trino` нет |
| прогрет одним ручным `uv run connectors/trino_proxy.py` | все 5: `mcp__trino__describe_table`, `execute_query`, `list_catalogs`, `list_schemas`, `list_tables` |

Проверено дважды, второй раз — специально с очисткой кэша, чтобы отделить «не успел стартовать» от «конфиг не доставлен».

Границы: точный механизм (именно таймаут, а не что-то другое) по логам не подтверждён — `$CODEX_HOME/logs_2.sqlite` в этих прогонах пуст. Но в схеме конфига самого бинаря (`RawMcpServerConfig`, `codex-cli 0.147.0`) есть поля `startup_timeout_sec` и `startup_timeout_ms`, и там же лежит строка ошибки «…seconds. Add or adjust `startup_timeout_sec` in your config.toml». Само поле в бою НЕ проверено — это следующий шаг, а не сделанный факт.

**Для дизайна:** первая сессия после установки — самая опасная, ровно там связка «всё настроено, а ничего нет» и проявляется. Локальные коннекторы (`trino`, `superset`, `sheets`, обёртка ClickHouse) стоит либо прогревать в мастере, либо поднимать им `startup_timeout_sec`.

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

**Обновление — то же самое подтверждено живой сессией с моделью (не только `debug prompt-input`).** В той же папке, где рядом лежат и `.agents/skills/recon-probe-skill`, и симлинк `.claude/skills/recon-probe-skill`, запустил:
```bash
codex exec "У тебя есть кастомный скилл recon-probe-skill? Используй его и процитируй строку." \
  -s workspace-write --skip-git-repo-check --dangerously-bypass-hook-trust
```
Codex сам прочитал файл именно по прямому пути (видно из его же команды в логе):
```
exec
/bin/zsh -lc "sed -n '1,240p' .agents/skills/recon-probe-skill/SKILL.md" in .../skilltest-src
...
codex
Да, скилл найден и использован.
> «Если ты видишь этот файл — симлинк или прямой путь сработал.»
```
**Проверка симметрии, которую просил координатор: Codex не обращается к `.claude/skills` и не путается от присутствия симлинка рядом** — он читает `.agents/skills/...` напрямую, симлинк для него просто нерелевантный файл в дереве проекта. Значит наличие `.claude/skills`-симлинков (нужных Claude Code) никак не мешает и не дублирует список скиллов у Codex.

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

## 6. Формат транскрипта: JSONL у обоих, структура — разная

**Проверено живым запуском для обоих движков** — сравнил файл из `transcript_path` реальной сессии Claude Code и файл из `transcript_path` реальной сессии Codex (обе сессии — из тестов выше).

### Claude Code

Файл: `~/.claude/projects/<slug-от-пути-проекта>/<session_id>.jsonl`. Один JSON-объект на строку, верхнеуровневые `type`: `queue-operation`, `attachment`, `user`, `assistant`, `last-prompt`.

Ключевое для парсинга: у строк типа `assistant` есть `message.id` (это ID сообщения от Anthropic API, например `msg_011CdoNQmtBMpN86SNWuBL9i`) — **один и тот же `message.id` может повторяться на нескольких соседних строках**, если один логический ответ модели разбит на несколько content-блоков (например, строка с `content:[{"type":"thinking",...}]` и следующая строка с `content:[{"type":"tool_use",...}]` у меня в тесте имели одинаковый `message.id`). Отсюда и правило дедупликации по `message.id`, о котором писал координатор: без него токены/использование по одному логическому ответу модели посчитаются дважды.

Результат тула лежит в отдельной строке `type: "user"` с `message.content: [{"type": "tool_result", "tool_use_id": "...", "content": "...", "is_error": true|false}]` — то есть **код ошибки/факт ошибки в транскрипте Claude Code есть** (`is_error: true`, `content: "Exit code 7"` — снял живьём на тесте с `exit 7` из раздела 2), просто не долетает до hook payload.

### Codex

Файл: `$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ISO-таймстамп>-<session_id>.jsonl`. Тоже один JSON-объект на строку, но структура другая: каждая строка — `{"timestamp": "...", "type": "<тип>", "payload": {...}}`, где `type` — один из `session_meta`, `event_msg`, `response_item`, `world_state`, `turn_context`.

Наблюдённые вложенные типы:
- `response_item` с `payload.type: "message"` — сообщения (developer/user/assistant), у каждого свой уникальный `payload.id` (`msg_...`), повторов на соседних строках не увидел (в отличие от Claude Code) — то есть классическая дедупликация "тот же message.id на нескольких строках" тут, похоже, не нужна.
- `response_item` с `payload.type: "custom_tool_call"` / `"custom_tool_call_output"` — вызов инструмента и его результат — это **отдельный тип объекта, не вложенный content-блок внутри message**, в отличие от Claude Code, где `tool_use`/`tool_result` — это блоки внутри `message.content`.
- `event_msg` с `payload.type: "token_count"` — **готовые агрегированные токены** (`total_token_usage`, `last_token_usage`) на каждом шаге, включая `cached_input_tokens`. Codex сам отдаёт куммулятивную сумму — не нужно суммировать/дедуплицировать по `message.id`, как у Claude Code.
- `event_msg` с `payload.type: "task_complete"` — здесь лежит `duration_ms` **всего turn'а** (в тесте — `17377`), это единственное место, где вообще нашёл длительность живьём для Codex (в hook `PostToolUse` её нет, см. раздел 2).
- Код ошибки инструмента — внутри `custom_tool_call_output.output`, причём как **JSON-строка внутри текстового элемента массива**, а не как отдельное поле верхнего уровня (пример — в разделе 3).

### Итог для дизайна разбора транскрипта

Формат — **разный настолько, что общий парсер транскрипта переиспользовать нельзя**, только общий факт "это JSONL, читаем построчно". Отличия, которые ломают прямое переиспользование логики Claude Code:
1. Дедупликация по `message.id` — специфика Claude Code; в Codex-транскрипте увиденные `id` у `response_item` не повторялись, но токены и так агрегированы отдельным событием `token_count`, дедуплицировать нечего.
2. Вызов тула — блок внутри `message.content` у Claude Code vs отдельный top-level `response_item` (`custom_tool_call`/`custom_tool_call_output`) у Codex.
3. Ошибка/код выхода — `is_error`+`content` прямо в `tool_result` у Claude Code vs JSON-строка на два уровня вложенности внутри `output` у Codex.
4. Путь и раскладка файла на диске — плоский файл на сессию у Claude Code vs раскладка по датам (`YYYY/MM/DD`) у Codex.

Значит разбор транскрипта под "sandbox.ai_usage_sessions" придётся писать двумя отдельными парсерами с общим только на уровне "прочитать JSONL построчно", а не одним общим модулем с двумя конфигурациями полей.

---

## 7. Доверие хукам: интерактивный путь, где хранится, pre-trust без диалога, сигнал при отсутствии

> Дозапрошено координатором отдельно: находка про `--dangerously-bypass-hook-trust` (раздел 2) звучит тревожно сама по себе — неясно, разовый это диалог (как доверие папке у Claude Code) или ежедневный "опасный" флаг. Проверено полностью живым запуском, интерактивный `codex` гонял через pty (`pexpect` + `pyte`, поставил через `pip3 install --user`, т.к. штатно не было — это единственный сторонний инструментарий, использованный в разведке).

### Вопрос 1 — есть ли интерактивный путь: ДА, подтверждено живым запуском

Запустил обычный интерактивный `codex` (без `exec`, без флагов доверия) в свежей папке с новым `hooks.json`. Первый экран — уже знакомый диалог доверия папке:
```
Do you trust the contents of this directory? Working with untrusted contents comes
with higher risk of prompt injection. Trusting the directory allows project-local
config, hooks, and exec policies to load.
› 1. Yes, continue
  2. No, quit
  Press enter to continue
```
После подтверждения (`Enter` на пункте 1) — **сразу второй, отдельный экран именно про хуки**, которого раньше не видел (в `codex exec` он не показывается вообще, там либо флаг, либо тишина):
```
Hooks need review
2 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.
› 1. Review hooks
  2. Trust all and continue
  3. Continue without trusting (hooks won't run)
  Press enter to confirm or esc to go back
```
Выбрал `2. Trust all and continue`. Сразу после этого — обычный REPL, отправил `Run the shell command: echo interactive-hook-probe`, инструмент выполнился, в лог-файле хука реально появилась запись `PostToolUse` — то есть **хуки заработали в этой же сессии сразу после диалога, без флага**.

Дальше проверил живьём, что доверие не разовое для одной сессии, а именно persist:
- **Новая интерактивная сессия** в той же папке/`CODEX_HOME` — диалогов не было вообще (ни про папку, ни про хуки), сразу обычный REPL; отправил другую команду — `SessionStart` и `PostToolUse` реально прилетели в лог хука без единого вопроса.
- **`codex exec` в той же папке** (тот же `CODEX_HOME`, тот же путь проекта) — без `--dangerously-bypass-hook-trust` (только `--skip-git-repo-check`, он не про хуки, а про то, что тестовая папка не git-репозиторий) — в выводе появились те самые строки `hook: SessionStart` / `hook: SessionStart Completed` / `hook: PostToolUse` / `hook: PostToolUse Completed`, и в JSON-логе — реальные payload'ы.

**Вывод: интерактивный путь — ровно тот сценарий, на который надеялся координатор.** Диалог одноразовый (пока хуки не меняются), решение переживает и следующие интерактивные сессии, и `codex exec`. `--dangerously-bypass-hook-trust` не единственный вариант и не нужен для повседневной работы аналитика — только для автоматизации без терминала (CI и т.п.).

### Вопрос 2 — где хранится доверие: `$CODEX_HOME/config.toml`, два независимых ключа

После согласия появился (раньше файла не было вовсе) `config.toml`:
```toml
[projects."/private/.../codex-trust-project3"]
trust_level = "trusted"

[hooks.state]

[hooks.state."/private/.../codex-home-trust-test3/hooks.json:post_tool_use:0:0"]
trusted_hash = "sha256:cd7fcb6762a7bb5adf9fa9eea0d48ece1e73eeea9da37dd8499301eba0ef6e51"

[hooks.state."/private/.../codex-home-trust-test3/hooks.json:session_start:0:0"]
trusted_hash = "sha256:81de5c81941f881c33ebeb1044c2c37181c0bba0386184804e819938fcb72fac"
```
Два независимых механизма в одном файле:
1. `[projects."<abs-путь-к-проекту>"] trust_level = "trusted"` — доверие *папке* (тот же уровень, что открывает project-local config вообще, не только хуки).
2. `[hooks.state."<abs-путь-к-hooks.json>:<event_в_snake_case>:<индекс>:<индекс>"] trusted_hash = "sha256:<hex>"` — **отдельная запись на каждый хук** (событие + позиция в массиве обработчиков для этого события), а не одна запись на весь `hooks.json`.

**Хэш детерминирован по содержимому хука, не привязан к машине/сессии** — проверил отдельно: завёл два полностью независимых `CODEX_HOME` с побайтово одинаковым `hooks.json` (одна и та же команда), провёл интерактивное доверие в обоих по отдельности — `trusted_hash` получился **идентичным** в обоих (`sha256:a17e59939bf0259523533d10e44da3583466b61a5825c6da95a1fda89aad2c8b`). Значит если изменить команду хука хоть на символ — хэш изменится и доверие придётся подтверждать заново (ожидаемое защитное поведение).

**Отказ ("3. Continue without trusting") ничего не пишет в `[hooks.state]` вовсе** — проверил отдельно: после явного отказа в `config.toml` осталось только `trust_level = "trusted"` для папки, секции `hooks.state` не появилось. Из-за этого диалог про хуки **выскакивает заново при каждой следующей интерактивной сессии**, пока не подтвердить (или пока хуки не убрать) — отказ не запоминается как "запомненное нет".

### Вопрос 3 — способ довериться заранее, без интерактива: ДА, есть, и он пригоден для мастера установки

Два подтверждённых способа:
1. `--dangerously-bypass-hook-trust` — разовый флаг на каждый вызов, ничего не сохраняет (уже описано в разделе 2).
2. **Ручная запись `config.toml` заранее — подтверждено, что работает.** Взял хэш, полученный на шаге выше (детерминирован и переносим между `CODEX_HOME`), и **вручную**, без единого интерактивного диалога, записал в третий, совершенно свежий `CODEX_HOME`:
   ```toml
   [projects."<путь-к-проекту>"]
   trust_level = "trusted"

   [hooks.state."<путь-к-hooks.json>:session_start:0:0"]
   trusted_hash = "sha256:a17e59939bf0259523533d10e44da3583466b61a5825c6da95a1fda89aad2c8b"
   ```
   Запустил `codex exec` в этой папке **без `--dangerously-bypass-hook-trust`** — хук сработал сразу, никакого диалога никогда не показывалось:
   ```
   hook: SessionStart
   hook: SessionStart Completed
   ```
   и в JSON-логе — реальный `SessionStart`-payload.

**Годится ли это для мастера установки — да, принципиально годится**, но с оговоркой: я не реверс-инжинирил точный алгоритм/вход хэш-функции (что именно хэшируется — сама команда, весь JSON-объект хука, с каноникализацией или без). Я лишь эмпирически подтвердил, что хэш детерминирован и переносим между машинами при побайтово одинаковом `hooks.json`. Значит рабочий, но не самый элегантный путь для мастера: **провести доверие интерактивно один раз на эталонной машине (или в любой временной папке), подсмотреть получившийся `trusted_hash` из `config.toml`, и зашить именно это значение в шаблон, который `setup.sh` пишет для каждого аналитика** — при условии, что итоговый текст команды хука у всех аналитиков будет идентичным (что реально при генерации из общего источника, как и планируется в дизайне порта). Отдельной команды типа `codex hooks trust` в CLI нет — проверил полный список подкоманд (`codex --help`), там только `exec, review, login, logout, mcp, plugin, mcp-server, app-server, remote-control, app, completion, update, doctor, sandbox, debug, apply, resume, archive, delete, unarchive, fork, cloud, exec-server, features, help` — ни одна не про доверие хукам.

### Вопрос 4 — что видит человек при отсутствии доверия: по-разному в `exec` и в интерактиве

**В `codex exec` (автоматизация, headless) — сигнала нет вообще.** Прогнал живьём полный, ничем не отфильтрованный вывод `codex exec` в свежей, никогда не доверявшейся папке, без флага:
```
Reading additional input from stdin...
OpenAI Codex v0.147.0
--------
...
codex
Выполняю указанную команду.
exec
/bin/zsh -lc 'echo no-trust-probe-3' in .../codex-project-notrust
 succeeded in 0ms:
no-trust-probe-3

codex
no-trust-probe-3
tokens used
4 162
no-trust-probe-3
```
Ни строки `hook:`, ни предупреждения, ни намёка на то, что хук не выполнился — команда просто отработала, ответ модели корректный, а `codex_events_notrust.jsonl` (файл, куда должен был писать хук) вообще не создался. **Это ровно тот же класс дефекта, что уже случался дважды** (`https` вместо `http`, `tool_error`): выглядит как рабочая сессия, а телеметрия молча не пишется.

**В интерактивном `codex` — сигнал есть, но только в момент запуска сессии, не постоянно.** Пока хуки не доверены (или доверие явно отклонено на предыдущей сессии — отказ не запоминается, см. вопрос 2), при **каждом** новом интерактивном запуске появляется экран "Hooks need review". Но если на этом экране явно выбрать `3. Continue without trusting (hooks won't run)` — дальше в транскрипте сессии **никакого отличия от обычной работы не видно**: инструмент выполняется, ответ приходит, никакой пометки "(hooks skipped)" или похожей нигде не появляется. Проверил это отдельно — после явного отказа выполнил `Run the shell command: echo decline-probe`, в интерфейсе всё выглядело как обычно, а `codex_events_decline.jsonl` не появился вовсе.

**Вывод для инструкции аналитикам (отдельным пунктом, как просил координатор):** в headless-режиме (`codex exec`, автоматизация, CI) отсутствие доверия к хукам **абсолютно неотличимо** от исправной работы по одному только выводу команды — единственный способ заметить дыру в телеметрии — проверять целевую таблицу (`sandbox.ai_usage_sessions`) на предмет отсутствующих строк с признаком движка `codex`, а не полагаться на "команда же отработала". В интерактивном режиме сигнал есть, но только в момент запуска сессии и только если пользователь его не проигнорирует.

---

## 8. Относительные пути в `args` MCP-серверов — резолвятся, но от рабочего каталога `codex`, не от `CODEX_HOME` и не от расположения `config.toml`

Контекст: задача Codex-3 (единый реестр коннекторов, `connectors/registry.py` + `tools/render_configs.py`). Три локальных коннектора (`trino`, `superset`, `sheets`) и обёртка `clickhouse_proxy.py` запускаются как `uv run <путь-до-скрипта>`. У Claude Code путь в `.mcp.json` — `${CLAUDE_PROJECT_DIR:-.}/connectors/trino_proxy.py`, макрос разворачивает сам Claude Code. У Codex такого макроса нет, а `${VAR}`-подстановка в `config.toml` не работает вообще (см. раздел 4) — из этого на первом проходе задачи Codex-3 **ошибочно** заключили по аналогии, что путь в `args` обязан быть абсолютным. Это была догадка, не факт: раздел 4 — про переменные окружения, а не про элементы `args`, и её никто не проверял живым запуском.

**Проверено живым MCP-рукопожатием (ревью задачи Codex-3), догадка опровергнута.** Конфиг с относительным путём:
```toml
[mcp_servers.trino]
command = "uv"
args = ["run", "connectors/trino_proxy.py"]
env_vars = ["TRINO_HOST", "TRINO_CATALOG"]
```
Запуск **из корня репозитория** (`cd ~/Desktop/uzum-data-agent`, изолированный `CODEX_HOME` в другом месте, скопирован только `auth.json`):
```bash
$ cd /Users/anastasiabir/Desktop/uzum-data-agent
$ CODEX_HOME=<изолированный, НЕ в репозитории> codex exec \
  "У тебя есть MCP-сервер trino. Перечисли названия всех его инструментов (tools). Не вызывай ни один из них. Затем остановись." \
  -s workspace-write --skip-git-repo-check < /dev/null
```
Вывод:
```
workdir: /Users/anastasiabir/Desktop/uzum-data-agent
...
codex
- `mcp__trino__describe_table`
- `mcp__trino__execute_query`
- `mcp__trino__list_catalogs`
- `mcp__trino__list_schemas`
- `mcp__trino__list_tables`
```
Реальный список из 5 инструментов — сервер стартовал, относительный путь резолвился.

**Ключевой факт: резолвится путь относительно `workdir` — рабочего каталога, из которого запущен сам `codex`, а НЕ относительно `CODEX_HOME` (который в этом тесте был в другом месте вообще) и НЕ относительно расположения `config.toml`.** Это подтверждено самой структурой теста: `CODEX_HOME` указывал на изолированную временную папку, `config.toml` лежал внутри неё, но относительный путь `connectors/trino_proxy.py` разрешился в файл внутри репозитория — то есть Codex не пытается резолвить его относительно `config.toml`, а передаёт как есть в `uv run`, который резолвит его как обычный shell — относительно cwd процесса.

**Важное следствие для мастера установки/лаунчера (`bin/uzum`, задача Codex-6), не проверенное отдельно, а прямое логическое продолжение факта выше:** если аналитик запустит `codex` не из корня репозитория (например, из домашней папки, или у него `CODEX_HOME` настроен на автозапуск через systemd/launchd с другим cwd), относительные пути в `args` не найдутся, и локальные коннекторы (`trino`, `superset`, `sheets`, `clickhouse-wms`/`clickhouse-dwh` через `clickhouse_proxy.py`) не запустятся. **Лаунчер обязан либо `cd` в корень репозитория перед вызовом `codex`, либо явно фиксировать это требование в инструкции аналитику** — сам Codex это не гарантирует и не проверяет. Не проверял, есть ли у `codex` встроенный флаг вида `--cwd`/`--project-dir` для принудительной установки рабочего каталога независимо от того, откуда его запустили, — стоит проверить отдельно перед тем, как писать лаунчер.

---

## 9. Обновление репозитория на старте сессии Codex: событие есть, текст хука долетает до модели

> Дозапрошено финальным ревью (Important 3): `codex_hook_definitions()` не регистрировал `on_session_start.sh`, то есть сессия Codex никогда не делала `git pull` — скиллы у аналитика застыли бы на дне установки. Вопрос: есть ли у Codex подходящее событие старта сессии и работает ли на нём наш скрипт. Проверено живым запуском 08.08.2026, codex-cli 0.147.0, изолированный `CODEX_HOME` во временной папке (скопирован только `auth.json`), временный git-репозиторий с настоящим апстримом — рабочий `~/.codex` и рабочий репозиторий не участвовали.

**Событие есть и называется `SessionStart`** — то же имя, что и у Claude Code (это уже было снято в разделе 2, здесь подтверждено повторно на другом скрипте). Регистрация в `hooks.json` изолированного `CODEX_HOME`:

```json
{"hooks": {"SessionStart": [{"hooks": [{"type": "command",
  "command": "bash .claude/hooks/on_session_start.sh --plain"}]}]}}
```

**Факт 1: хук реально выполняется и `git pull --ff-only` реально подтягивает коммит.** Клон отставал от апстрима на один коммит; после `codex exec` HEAD клона совпал с апстримом, содержимое файла скилла обновилось:

```
HEAD проекта до сессии: 418346b
HEAD апстрима:          e1a5d80
...
hook: SessionStart
hook: SessionStart Completed
==================================
HEAD проекта ПОСЛЕ сессии: e1a5d80
содержимое скилла: НОВЫЙ скилл приехал
```

**Факт 2: обычный текст из stdout хука долетает до модели дословно.** Второй прогон (клон снова отставал на коммит), промпт — «Не запускай никаких команд. Просто ответь: приходил ли тебе какой-нибудь текст от хука старта сессии (session start)? Если да — процитируй его дословно. Если нет — ответь ровно 'НИЧЕГО НЕ ПРИХОДИЛО'». Ответ модели:

```
codex
Репозиторий обновлён. Изменилось:
.agents/skills/demo/SKILL.md
```

Это дословно то, что напечатал наш хук в режиме `--plain`. То есть у Codex не нужен аналог `hookSpecificOutput.additionalContext`: обычный stdout хука и есть способ передать текст в контекст сессии.

**Следствие для кода:** `on_session_start.sh` принимает флаг `--plain` (обычный текст вместо Claude-JSON) и зарегистрирован в `codex_hook_definitions()` на `SessionStart`. Обновление в обоих движках теперь одинаковое.

**Границы проверки (не выдаю за большее, чем есть):**

- Оба прогона — `codex exec` с `--dangerously-bypass-hook-trust`. Интерактивный `codex` с диалогом «Hooks need review» на этом конкретном скрипте отдельно не гонял: механизм доверия уже разобран в разделе 7 и от содержимого хука не зависит (меняется только `trusted_hash`, а значит при обновлении hooks.json диалог придётся пройти заново — это ожидаемое поведение, `setup.sh` его проверяет).
- Формат `hookSpecificOutput` под Codex так и **не проверялся** — ни подтверждён, ни опровергнут. Он просто не нужен: обычный текст работает.
- Поведение при неудачном `git pull` (конфликт, нет сети) под Codex живьём не воспроизводил. Скрипт в этом случае печатает текст про неудачу и завершается нулём — это покрыто юнит-тестом (`tests/test_session_start.py`), но не живым запуском Codex.

---

## 10. У Codex нет аналога `/mcp`, а `codex mcp list` наш профиль не видит

> Дозапрошено финальным ревью (Important 6): три скилла отправляли смотреть список инструментов командой `/mcp`, которая есть только у Claude Code. Проверено живым запуском 08.08.2026 в той же изолированной песочнице.

`codex mcp list` существует как подкоманда CLI и реально печатает таблицу серверов, но **читает только базовый `$CODEX_HOME/config.toml`** — флага `-p/--profile` у неё нет вовсе:

```
$ codex mcp list -p uzum
error: unexpected argument '-p' found
```

Тот же `codex mcp list`, когда наш профиль положен базовым конфигом, сервера показывает (проверено, вывод — таблица с `clickhouse-wms` и `trino`, значения env замаскированы `*****`). Но в рабочей установке профиль лежит именно как `$CODEX_HOME/uzum.config.toml` и подключается флагом `-p uzum`, поэтому `codex mcp list` в ней покажет пусто или чужое.

**Следствие для скиллов:** отправлять аналитика в Codex за списком инструментов некуда — ни `/mcp`, ни `codex mcp list` не годятся. Тексты скиллов теперь называют `/mcp` явно как команду Claude Code, а для Codex отсылают к исходнику коннектора.

**Границы:** внутрисессионные команды интерактивного `codex` (TUI) на предмет чего-то похожего на `/mcp` отдельно не перебирал — проверялась только CLI-подкоманда `codex mcp`.

---

## 11. Отсутствующий файл хука — не тишина, а `Blocked`; граница «наша сессия» — рабочий каталог

> Дозапрошено по дефекту «хуки Codex ломают все остальные проекты аналитика». Проверено живым запуском 08.08.2026, codex-cli 0.147.0 и Claude Code 2.1.220, изолированный `CODEX_HOME` во временной папке (скопирован только `auth.json`), временные каталоги — рабочий `~/.codex` и рабочий репозиторий не участвовали.

### Факт 1: относительный путь в `hooks.json` ломает чужие сессии Codex — опровергнутое предположение

`lib/setup_helpers.py::codex_hook_definitions()` регистрировал хуки относительным путём (`python3 .claude/hooks/log_event.py`), и его докстринг утверждал: «для сессий Codex в других проектах файла по этому относительному пути просто не будет — хук тихо не сработает, и это и есть желаемое поведение». Это было предположение, не факт.

`hooks.json` изолированного `CODEX_HOME` — ровно то, что генерировал код на коммите `168268c`; запуск в постороннем каталоге, где `.claude/hooks/` нет:

```
$ CODEX_HOME=<изолированный> codex exec --skip-git-repo-check --dangerously-bypass-hook-trust \
    "Ответь ровно одним словом: ПРИВЕТ. Не запускай никаких команд." </dev/null
...
user
Ответь ровно одним словом: ПРИВЕТ. Не запускай никаких команд.
hook: SessionStart
hook: SessionStart
hook: SessionStart Failed
hook: SessionStart Failed
hook: UserPromptSubmit
hook: UserPromptSubmit Blocked
```

Ответа нет вообще — сессия обрывается на `UserPromptSubmit Blocked`. Причина: `python3` не находит файл и завершается ненулевым кодом, а ненулевой код на `UserPromptSubmit` Codex трактует как блокировку промпта. `SessionStart Failed` сессию не рушит, но тоже шумит. Практический смысл: аналитик, поставивший наш инструмент, получал нерабочий Codex во **всех остальных** своих проектах — `hooks.json` один на весь `$CODEX_HOME`.

### Факт 2: рабочий каталог процесса хука == `payload["cwd"]` == каталог сессии — у обоих движков

Хук-зонд (абсолютный путь, дописывает в файл `payload`, `os.getcwd()` и окружение), все четыре события, которые мы регистрируем.

**Codex 0.147.0**, запуск в постороннем каталоге `.../hookscope/foreign`:

| Событие | `payload["cwd"]` | `os.getcwd()` хука |
|---|---|---|
| SessionStart | `.../hookscope/foreign` | `.../hookscope/foreign` |
| UserPromptSubmit | `.../hookscope/foreign` | `.../hookscope/foreign` |
| PostToolUse | `.../hookscope/foreign` | `.../hookscope/foreign` |
| SessionEnd | `.../hookscope/foreign` | `.../hookscope/foreign` |

То же значение стоит в баннере сессии (`workdir:`) и в переменной `PWD`. Уточнение к разделу 2: `cwd` приходит у Codex в **каждом** из четырёх событий, включая `SessionEnd` (там был приведён сокращённый JSON).

**Claude Code 2.1.220**, те же четыре события во временном проекте: `payload["cwd"]` == `os.getcwd()` == `CLAUDE_PROJECT_DIR` == корень проекта. То есть для Claude Code проверка «рабочий каталог внутри нашего корня» — безвредный no-op: его хуки и так живут в `.claude/settings.json` своего проекта.

Никакой переменной окружения вида `CODEX_PROJECT_DIR` у Codex в окружении хука нет — только `CODEX_HOME`. Опираться на окружение не на что, опора — рабочий каталог.

### Факт 3: абсолютный путь + собственная проверка внутри скриптов — чужие сессии целы, своя работает

`hooks.json` с абсолютными путями к скриптам клона, лежащего **не** по рабочему пути (`.../hookscope/clone`), скрипты — с проверкой из `lib/hook_scope.py`.

Посторонний каталог (`.../hookscope/foreign`), тот же промпт, что и в факте 1:

```
hook: SessionStart
hook: SessionStart Completed
hook: UserPromptSubmit
hook: UserPromptSubmit Completed
codex
ПРИВЕТ
```

`Blocked` нет, `Failed` нет, промпт дошёл до модели, ответ получен. В очереди телеметрии (`UZUM_STATE_DIR`) — пусто: чужая сессия в наши данные не попала.

Клон нашего репозитория по другому пути (`.../hookscope/clone`, не `~/Desktop/uzum-data-agent`), тот же изолированный `CODEX_HOME`, промпт с одной shell-командой:

```
hook: SessionStart
hook: SessionStart
hook: SessionStart Completed
hook: SessionStart Completed
hook: UserPromptSubmit
hook: UserPromptSubmit Completed
...
hook: PostToolUse
hook: PostToolUse Completed
```

В очереди телеметрии (ClickHouse намеренно недоступен, `127.0.0.1:1`) — три строки одной и той же сессии:

```
ai_usage_events   | event_type=UserPromptSubmit           engine=codex
ai_usage_events   | event_type=PostToolUse tool_name=Bash engine=codex
ai_usage_sessions | n_prompts=1 n_tools=1 duration_s=5    engine=codex
```

То есть путь «сессия наша» не пострадал: и пошаговые события, и итоговая строка сессии на месте.

Тот же клон под Claude Code (`claude -p`, хуки из `.claude/settings.json`) — те же три строки, с `engine=claude`. Проверка ничего не сломала и на этой стороне.

### Факт 4: команда хука исполняется шеллом — `test -f … && exec … || exit 0` работает

Абсолютный путь сам по себе не гарантирует, что файл на месте: аналитик может перенести, переименовать или удалить папку репозитория, а `$CODEX_HOME/hooks.json` при этом не меняется. Проверено: `hooks.json` с абсолютным путём на **несуществующий** клон, посторонний каталог — тот же `SessionStart Failed` / `UserPromptSubmit Blocked`, что и в факте 1, только через другой вход и уже во всех проектах разом.

С обёрткой `test -f '<путь>' && exec python3 '<путь>' || exit 0` тот же прогон (путь указывает на несуществующий каталог `…/clone2-переехал`):

```
hook: SessionStart
hook: SessionStart Completed
hook: UserPromptSubmit
hook: UserPromptSubmit Completed
codex
ПРИВЕТ
```

Значит команда действительно исполняется шеллом: `&&`, `||` и `exec` отработали. Обратная сторона — та же `hooks.json`, но путь указывает на настоящий клон: хуки выполняются, в очередь легли `UserPromptSubmit`, `PostToolUse` и строка сессии (`engine=codex`). Обёртка не съедает работающий хук.

### Факт 5: если рабочего каталога сессии больше нет, движок не может даже запустить процесс хука

Сценарий: аналитик сидит в `work/OE-1234`, каталог исчезает во время сессии (переключение ветки, `git clean`, `rm -rf` в соседнем окне). Проверено на обоих движках — каталог удалялся фоновым процессом посреди работающей сессии.

**Codex:** сессия доходит до конца (exit 0), модель отвечает полностью, `Blocked` не появляется ни разу. В выводе — `SessionStart Failed` и `UserPromptSubmit Failed`: Codex не смог **породить** процесс хука, потому что рабочего каталога нет (та же ошибка, что и на обычном инструменте: `Failed to create unified exec process: No such file or directory (os error 2)`). Это происходит до нашего кода и одинаково на любой его версии.

**Claude Code:** сессия тоже доходит до конца (exit 0, полный ответ). Провалы хуков он показывает явно, текстом: `SessionEnd hook […] failed: Error occurred while executing hook command: ENOENT: no such file or directory, posix_spawn '/bin/sh'` — в том прогоне так упали два ЧУЖИХ хука пользовательского уровня.

Отсюда вывод, важный для кода: сам по себе исчезнувший каталог даёт «хук не запустился», а не «промпт заблокирован». Но если процесс хука всё-таки стартовал (каталог исчез между порождением и первым обращением к нему), `os.getcwd()` кидает `FileNotFoundError` — и это уже ненулевой код возврата хука, то есть `Blocked` на UserPromptSubmit. Воспроизводится процессом напрямую и покрыто тестами (`tests/test_hook_scope.py`, «vanished working directory»); поэтому `lib/hook_scope.session_is_ours()` не бросает исключений, а bash-хук выходит нулём с тем же результатом.

### Факт 6: Codex выполняет ВСЕ записи события, и одной упавшей достаточно, чтобы заблокировать промпт

Проверено на `hooks.json`, где на одном событии оказались две наши записи разом (старая форма команды и новая — так выглядела машина, обновившаяся с прошлой версии инструмента):

```
hook: UserPromptSubmit
hook: UserPromptSubmit
hook: UserPromptSubmit Blocked
hook: UserPromptSubmit Completed
```

Обе записи запускаются; исход у каждой свой, и `Completed` соседа `Blocked` не отменяет. Отсюда два следствия для кода:

- лишняя (устаревшая) запись — это не просто шум: она блокирует промпт сама по себе. Поэтому `merge_codex_hooks` наши прежние хуки вытесняет, а не дополняет (`lib/setup_helpers.py`, `OUR_HOOK_MARKER` и `LEGACY_HOOK_FORMS`);
- пока записи дублировались, каждое событие обрабатывалось дважды. Измерено в изолированной песочнице: на сломанном коде очередь телеметрии получила `UserPromptSubmit ×2`, `PostToolUse ×2`, строку сессии ×2; после починки — по одной.

### Факт 7: `hook: <событие>` печатается на каждый ХУК, а не на запись

Уточнение к факту 6. Проверено на `hooks.json`, где на `UserPromptSubmit` лежали две записи: первая — из двух команд сразу (два чужих инструмента), вторая — наша, из одной. Живой прогон, посторонний каталог:

```
hook: UserPromptSubmit
hook: UserPromptSubmit
hook: UserPromptSubmit
hook: UserPromptSubmit Completed
hook: UserPromptSubmit Completed
hook: UserPromptSubmit Completed
```

Три строки на две записи: Codex выполняет каждый хук внутри записи отдельно и отчитывается о каждом. Оба чужих скрипта отметились в своих файлах.

**Следствие для кода:** запись hooks.json — не «один хук», а контейнер независимых команд, и чужая команда может лежать в одной записи с нашей. Поэтому `merge_codex_hooks` ищет совпадение и удаляет на уровне хука, а запись выбрасывает, только если после этого в ней не осталось ничего (`lib/setup_helpers.py::_without_our_hooks`).

### Факт 8: `env VAR=1` в команде хука работает — метку можно нести в самой команде

Наши записи помечены: команда запускает скрипт через `exec env UZUM_DATA_AGENT_HOOK=1 python3 …`. Проверено живым запуском (та же изолированная песочница, апгрейд с `hooks.json` прежней формы, рядом два чужих инструмента):

- посторонний каталог — `SessionStart`/`UserPromptSubmit` `Completed`, ни `Failed`, ни `Blocked`, промпт дошёл до модели, очередь телеметрии пуста;
- наш клон по временному пути — `SessionStart`/`UserPromptSubmit`/`PostToolUse` `Completed`, в очереди ровно по одной строке на событие (`UserPromptSubmit`, `PostToolUse` c `tool_name=Bash`, строка сессии, все `engine=codex`).

То есть префикс `env` не мешает ни запуску скрипта, ни `test -f … && exec … || exit 0` вокруг него (факт 4). Метка при этом видна в диалоге доверия — по ней аналитик понимает, чей это хук.

Заодно измерено, что даёт опознание записей по метке вместо родовых признаков: чужой инструмент со своим `.claude/hooks/log_event.py` пережил четыре `./setup.sh` подряд (два из одного клона, один из другого, ещё один обратно) и продолжал срабатывать; число хуков в файле после каждого — одно и то же (8: 5 наших, 3 чужих).

### Границы (не выдаю за большее, чем есть)

- Оба движка проверялись в неинтерактивном режиме (`codex exec` с `--dangerously-bypass-hook-trust`, `claude -p`). Интерактивный TUI на этом дефекте отдельно не гонял: механизм доверия разобран в разделе 7 и от содержимого хука не зависит.
- `cwd` снят для четырёх событий, которые мы регистрируем. Остальные события Codex (`PreToolUse`, `Stop`, `PreCompact`, …) на предмет наличия `cwd` не проверялись.
- Поведение при `codex resume`/`fork` и в десктопном `codex app` не проверялось — как и в разделе 7.
- Побочное наблюдение, не факт про Codex: `claude -p`, запущенный из подкаталога временного проекта, хуки из `.claude/settings.json` корня проекта не подхватил вовсе (ни одного события в зонде), даже когда корень был git-репозиторием; из самого корня — подхватил все четыре. Причину не выяснял, к этому дефекту отношения не имеет.
- Сценарий «каталог исчез» (факт 5) на живых движках не различает починенный код и сломанный: и там и там движок не успевает породить процесс хука. Различие проверено уровнем ниже — прямым запуском скрипта с исчезнувшим рабочим каталогом (`tests/test_hook_scope.py`). Момент «процесс уже стартовал, каталог исчез следом» на живом движке специально не воспроизводился: это гонка, надёжно поймать её запуском не удалось.
- Терпит ли Codex посторонние ключи в записи `hooks.json` (маркер полем JSON, а не текстом команды) — не проверял: метка живёт в самой команде, и повод проверять не возник.
- Почему в прогоне Claude Code с исчезнувшим каталогом в списке провалившихся хуков не оказалось наших — не выяснял. Наш `UserPromptSubmit` в той сессии отработал (строка с `engine=claude` в очереди), то есть каталог исчез между событиями.

---

## Что проверить не удалось (и почему)

| Что | Почему не проверено |
|---|---|
| Полный набор значений поля `reason` в `SessionEnd` у обоих движков (видел только `"other"` у обоих — это единственный путь завершения, который даёт неинтерактивный режим, `claude -p` / `codex exec`) | Не воспроизвёл остальные пути завершения (logout, clear, обрыв по ошибке, ручной `/exit` в интерактивном режиме) за отведённое время у обоих движков |
| `brew install --cask codex` как альтернативный способ установки | Не устанавливал вторую копию — уже стоит npm-версия, ставить конфликтующую бессмысленно; факт наличия пакета в brew (`0.146.1`) подтверждён `brew info`, но сама установка этим способом не проверялась |
| Поведение `codex login --with-api-key` "вживую" | Логин уже был сделан владельцем через `codex login` (браузерный ChatGPT OAuth) — способ через `OPENAI_API_KEY` отдельно не перепроверял, т.к. рабочего ключа нет и переавторизовывать рабочий аккаунт ради теста второго способа было бы разрушительно |
| Полный список hook-событий Codex живьём (`PreToolUse`, `PermissionRequest`, `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, `Stop`) | Координатор просил закрыть конкретно `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `SessionEnd` — их и снял. Остальные события из official-списка (раздел 3) подтверждены только статическим анализом бинаря, живым запуском не гонял |
| Payload `PostToolUse` для инструментов, отличных от shell (например, чтение файла, MCP-тул) — только shell (`exec`) проверен живьём для Codex | Не успел прогнать все типы инструментов Codex за отведённое время; shell выбран как самый близкий аналог теста на Claude Code |
| Точный алгоритм/вход хэш-функции `trusted_hash` в `[hooks.state]` (раздел 7) | Подтвердил только детерминированность и переносимость эмпирически (одинаковый `hooks.json` → одинаковый хэш на двух разных `CODEX_HOME`); что именно хэшируется (сама команда, весь JSON-объект, с канонизацией или без) — не реверс-инжинирил |
| Экран доверия хукам в `codex app` (десктоп-приложение) и в `resume`/`fork` сессий | Проверял только обычный `codex` (терминальный TUI) и `codex exec`; десктопное приложение и восстановление сессий не гонял |
| Работает ли `startup_timeout_sec`/`startup_timeout_ms` у `[mcp_servers.<id>]` (раздел 4, про не успевший стартовать сервер) | Поля есть в схеме конфига бинаря и упоминаются в его же тексте ошибки, но живьём я их не выставлял: сам факт «холодный `uv` → коннектора в сессии нет, прогретый → есть» снят и без них, а подбор значения — отдельная задача с отдельной проверкой |

## Явно помеченные предположения (не факты)

- Что набор значений `reason` у `SessionEnd` шире, чем наблюдённое `"other"`, и включает что-то вроде `clear`/`logout`/`prompt_input_exit` — предположение и для Claude Code, и для Codex, ни разу не подтверждённое живым запуском ни для одного из движков.
- Что поведение `PostToolUse` для не-shell инструментов Codex (MCP-тулы, встроенные инструменты вроде чтения файлов) совпадает с поведением, снятым для shell/`exec` — перенос наблюдения с одного типа тула на все остальные без проверки.
- Что мастер установки сможет заранее вычислить `trusted_hash` самостоятельно (без разового интерактивного прогона на эталонной машине) — не подтверждено: я использовал уже готовый хэш, полученный интерактивно, а не вычислил его с нуля по известному алгоритму.
- Что у `codex`/`codex exec` нет встроенного флага для принудительной установки рабочего каталога независимо от того, откуда его запустили (что-то вроде `--cwd`/`--project-dir`) — не искал специально (см. раздел 8): относительные пути в `args` MCP-серверов резолвятся от `workdir` процесса `codex`, и если такой флаг есть, он снял бы зависимость лаунчера от `cd` перед запуском; если флага нет, лаунчер обязан сам `cd` в корень репозитория. Не проверено ни в одну, ни в другую сторону.
