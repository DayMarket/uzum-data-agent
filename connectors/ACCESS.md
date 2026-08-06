# Как получить доступы

| Что | Где взять | Сколько ждать |
|---|---|---|
| ClickHouse | Логин — корп-почта через дефис. Пароль — заявка в JSM, тип «Доступ к DWH» | до 1 дня |
| Jira | Профиль → Personal Access Tokens → Create token | сразу |
| Superset | Ничего вводить не нужно: вход через Keycloak SSO в браузере | сразу |
| Trino | Ничего вводить не нужно: OAuth2 SSO в браузере при первом запросе | сразу |
| Grafana | Запросить сервисный токен у платформы | до 1 дня |
| OpenMetadata | Профиль → Access Token | сразу |
| GrowthBook | Settings → API Keys → read-only ключ | сразу |
| Google Sheets | Файл сервисного аккаунта — у Насти. Папку с таблицами расшарить на его почту | сразу |

Netbird обязателен: прод ClickHouse, Trino, OpenMetadata и Grafana доступны только
из корпоративной сети. Установка требует прав администратора на ноутбуке.

## Куда кладём секреты

Все токены и пароли — в один файл `~/.config/uzum-ai/secrets.env` (не в git, не в
`.mcp.json`). Формат — `KEY=VALUE`, по строке на секрет:

```
CH_HOST=...
CH_USER=...
CH_PASSWORD=...
JIRA_URL=https://jira.uzum.com
JIRA_TOKEN=...
CONFLUENCE_URL=https://confluence.uzum.com
SUPERSET_URL=https://bi.uzum.uz
TRINO_USER=твой.email@uzum.com
GRAFANA_URL=...
GRAFANA_TOKEN=...
OMD_URL=...
OMD_TOKEN=...
GROWTHBOOK_TOKEN=...
GOOGLE_SA_FILE=...
GOOGLE_SHEETS_FOLDER_ID=...
```

`setup.sh` (задача 9) подставляет эти значения в окружение перед запуском Claude
Code — `.mcp.json` ссылается на них через `${VAR}` и никогда не хранит значения
напрямую.

Коннекторы `trino` и `superset` — свои Python-скрипты в `connectors/`, у них есть
несколько собственных зависимостей (httpx, `trino`, `mcp`, `pandas`), которых нет в
стандартной библиотеке. `.mcp.json` запускает их через `uv run` — так же, как
остальные пять серверов запускаются через `uvx`: `uv`/`uvx` сам разворачивает
изолированное окружение под нужные пакеты при первом запуске, без ручного
`pip install`. Для этого нужен установленный `uv` (`brew install uv` или
`curl -LsSf https://astral.sh/uv/install.sh | sh`).

`TRINO_USER` не передаётся через `.mcp.json` (это не секрет, но и не то, что стоит
хардкодить в закоммиченном файле у каждого своё) — коннектор `trino_proxy.py`
читает его напрямую из `~/.config/uzum-ai/secrets.env`, если переменная не задана
в окружении. Так же `superset_mcp.py` читает `SUPERSET_USERNAME`/`SUPERSET_PASSWORD`
из того же файла, если они не заданы — обычно не нужны: вход в Superset идёт через
Keycloak SSO без пароля.

## Если что-то не работает

`/fix-access` прямо в сессии Claude Code. Не помогло — Настя Бир.
