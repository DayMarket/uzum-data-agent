---
description: Используй, когда нужно посмотреть, изменить или заскриншотить дашборд/чарт/датасет Superset, или выполнить SQL Lab запрос — через MCP-коннектор superset, без браузера.
---

# Superset BI (через коннектор `superset`)

Коннектор `superset` в `.mcp.json` — свой Python-скрипт (`connectors/superset_mcp.py`),
логинится в Superset через Keycloak SSO автоматически (куки кешируются).
Браузер и ручной логин не нужны.

Инструменты видны в Claude Code как `mcp__superset__<name>`. Список — команда `/mcp`.

## Когда применять
- Посмотреть / изучить / заскриншотить дашборд или чарт
- Прочитать данные за чартом или выполнить SQL Lab запрос
- Создать или изменить датасет / чарт / дашборд

Для сырых данных без привязки к объектам Superset — витрины через `clickhouse` /
`trino`; коннектор `superset` — когда вопрос именно про объекты Superset.

## Инструменты

**Discover / inspect**
- `mcp__superset__list_dashboards(search, limit)` — поиск дашбордов (**search обязателен**).
- `mcp__superset__get_dashboard(id_or_slug)` — детали + список чартов.
- `mcp__superset__list_charts(search, dashboard_id, limit)` / `mcp__superset__list_dashboard_charts(dashboard_id)`.
- `mcp__superset__list_datasets(search, limit)` / `mcp__superset__get_dataset(dataset_id)` / `mcp__superset__get_dataset_summary(dataset_id)`.
- `mcp__superset__get_chart_params_summary(chart_id)` — компактный конфиг чарта.
- `mcp__superset__get_dashboard_layout_summary(dashboard_id, chart_ids?)` — раскладка / позиции.

**Данные**
- `mcp__superset__get_chart_data(chart_id)` — выполняет запрос чарта; вернёт строки или **ошибку**, если запрос падает (так находятся сломанные чарты).
- `mcp__superset__sql_query(sql, database_id, schema)` — SQL Lab.

**Визуал (PNG, ~800×600)**
- `mcp__superset__get_dashboard_screenshot(dashboard_id)` — рендер дашборда.
- `mcp__superset__get_chart_screenshot(chart_id)` — рендер чарта.

**Создание / изменение / удаление**
- `mcp__superset__create_dataset` / `mcp__superset__update_dataset` (метрики, колонки, SQL, описание).
- `mcp__superset__create_chart` / `mcp__superset__update_chart` (ставь `clear_query_context=true` после смены параметров/датасорса).
- `mcp__superset__create_dashboard` / `mcp__superset__update_dashboard` (json_metadata, position) / `mcp__superset__patch_dashboard_position` (точечные правки раскладки).
- `mcp__superset__normalize_dashboard_metrics(dashboard_id, dry_run)` — заменяет ad-hoc SQL метрики на сохранённые метрики датасета (сначала `dry_run=true`).
- `mcp__superset__delete(object_type, object_id)` — чарт / датасет / дашборд.

## Заметки
- Логин автоматический; `mcp__superset__refresh_token` вызывай только если инструмент вернул 401.
- `mcp__superset__list_dashboards` ТРЕБУЕТ search term (без него список висит в таймаут → 502).
- Скриншоты — серверный рендер миниатюр (~800×600, кешируется) — годится проверить «грузится / как выглядит», не для пиксельного зума.
- Мутации (create/update/delete) меняют **реальные** объекты Superset — подтверди с человеком перед применением.

## Диагностика
- Нет инструментов `mcp__superset__*` → используй скилл `fix-access`: проверь переменную `SUPERSET_URL` в `~/.config/uzum-ai/secrets.env` и что коннектор `superset` включён.
- 401 / ошибка логина → истекла SSO-сессия, откроется браузер для повторного входа; если не помогло — `mcp__superset__refresh_token`.
