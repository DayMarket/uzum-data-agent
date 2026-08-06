# Как получить доступы

| Что | Где взять | Сколько ждать |
|---|---|---|
| ClickHouse | Логин — корп-почта через дефис. Пароль — заявка в JSM, тип «Доступ к DWH» | до 1 дня |
| Телеметрия | Отдельного доступа не нужно: те же логин и пароль ClickHouse | — |
| Jira | Профиль → Personal Access Tokens → Create token | сразу |
| Superset | Ничего вводить не нужно: вход через Keycloak SSO в браузере | сразу |
| Trino | Ничего вводить не нужно: OAuth2 SSO в браузере при первом запросе | сразу |
| Grafana | Запросить сервисный токен у платформы | до 1 дня |
| OpenMetadata | Профиль → Access Token | сразу |
| GrowthBook | Settings → API Keys → read-only ключ | сразу |
| Google Sheets | Файл сервисного аккаунта — у Насти. Папку с таблицами расшарить на его почту | сразу |

Netbird обязателен: прод ClickHouse, Trino, OpenMetadata и Grafana доступны только
из корпоративной сети. Установка требует прав администратора на ноутбуке.

## Хосты ClickHouse

| Кластер | Хост | Порт | Для чего |
|---|---|---|---|
| WMS (склады) | `wms-clickhouse.prod.um.internal` | 8123 | основной для операционной аналитики; здесь же таблицы телеметрии `sandbox.ai_usage_*` |
| DWH (общий) | `dwh-clickhouse.prod.um.internal` | 8123 | продажи, финансы, маркетинг |

Домен `*.prod-data.internal.daymarket.uz` — это Trino, не ClickHouse.

Телеметрия спрашивается отдельным вопросом мастера и по умолчанию идёт на WMS,
даже если рабочим кластером выбран DWH: таблиц `sandbox.ai_usage_*` на DWH нет.
Переподключить отдельно — `./setup.sh --add telemetry`.

## Что должно быть на машине, кроме `uv`

- `mcp-grafana` — Go-бинарь (`brew install mcp-grafana`), пакета с таким именем
  на PyPI нет. Сервер читает токен из `GRAFANA_SERVICE_ACCOUNT_TOKEN`.
- `npx` (Node.js 18+, `brew install node`) — только для GrowthBook: официальный
  сервер это npm-пакет `@growthbook/mcp`, питоновского аналога нет.
- OpenMetadata запускается модулем (`python -m mcp_openmetadata.server`), у пакета
  нет исполняемого файла; версии `fastmcp` и `pydantic` пришлось прижать —
  с текущими пакет падает на старте.

## Куда кладём секреты

Все токены и пароли — в один файл `~/.config/uzum-ai/secrets.env` (не в git, не в
`.mcp.json`). Формат — `KEY=VALUE`, по строке на секрет:

```
CH_HOST=wms-clickhouse.prod.um.internal
CH_PORT=8123
CH_USER=имя-фамилия
CH_PASSWORD=<пароль из той же заявки JSM>
JIRA_URL=https://jira.uzum.com
JIRA_TOKEN=<Personal Access Token из профиля Jira>
CONFLUENCE_URL=https://confluence.uzum.com
SUPERSET_URL=https://bi.uzum.uz
TRINO_USER=твой.email@uzum.com
GRAFANA_URL=<URL из ответа платформы вместе с токеном>
GRAFANA_TOKEN=<сервисный токен от платформы>
OMD_URL=<URL инстанса OpenMetadata — спроси в платформе, если нет под рукой>
OMD_TOKEN=<Access Token из профиля OpenMetadata>
GROWTHBOOK_TOKEN=<read-only ключ из Settings → API Keys>
GOOGLE_SA_FILE=~/.config/uzum-ai/google-service-account.json
GOOGLE_SHEETS_FOLDER_ID=<ID папки из её URL: drive.google.com/drive/folders/ЭТОТ_ID>
```

Значения в `<угловых скобках>` — не значения для копирования, а подсказка, где
их взять; остальные строки — рабочие значения, их можно использовать как есть.

`setup.sh` (задача 9) подставляет эти значения в окружение перед запуском Claude
Code — `.mcp.json` ссылается на них через `${VAR}` и никогда не хранит значения
напрямую.

Коннекторы `trino`, `superset` и `sheets` — свои Python-скрипты в `connectors/`, у
них есть несколько собственных зависимостей (httpx, `trino`, `mcp`, `pandas`,
`google-auth`, `requests`), которых нет в стандартной библиотеке. `.mcp.json`
запускает их через `uv run` — так же, как остальные пять серверов запускаются
через `uvx`: `uv`/`uvx` сам разворачивает изолированное окружение под нужные
пакеты при первом запуске, без ручного `pip install`. Для этого нужен
установленный `uv` (`brew install uv` или
`curl -LsSf https://astral.sh/uv/install.sh | sh`).

`sheets_mcp.py` — аутентификация сервисного аккаунта через `google-auth`
(`google.oauth2.service_account`), а не ручная подпись JWT через `openssl`:
пакет `google-auth` подписывает JWT сам через встроенный pure-Python бэкенд
(`google.auth.crypt.rsa`), внешний бинарник не нужен.

`TRINO_USER` не передаётся через `.mcp.json` (это не секрет, но и не то, что стоит
хардкодить в закоммиченном файле у каждого своё) — коннектор `trino_proxy.py`
читает его напрямую из `~/.config/uzum-ai/secrets.env`, если переменная не задана
в окружении. Так же `superset_mcp.py` читает `SUPERSET_USERNAME`/`SUPERSET_PASSWORD`
из того же файла, если они не заданы — обычно не нужны: вход в Superset идёт через
Keycloak SSO без пароля.

## Если что-то не работает

`/fix-access` прямо в сессии Claude Code. Не помогло — Настя Бир.
