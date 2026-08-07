# Обнаружение ошибок и трассировка источника данных

## Обнаружение ошибок

### Источник 1: обзорный дашборд Superset

**URL:** `https://bi.uzum.uz/superset/dashboard/superset_overview/`

#### Какие фильтры применить
- **Фильтр по команде:** своя команда (например `ops` или `search`)
- **Период:** последние 7 дней (рекомендуется)

#### Ключевые метрики
- Чарты с ошибками (error rate > 7%)
- Упавшие запросы
- Медленные чарты (загрузка > 10 сек)

#### Как посмотреть
```python
# Логин автоматический (коннектор superset). Ищем дашборд по названию:
mcp__superset__list_dashboards(search="ops")
# Открываем, чтобы получить список чартов:
mcp__superset__get_dashboard(id_or_slug="<id_or_slug>")
```

### Источник 2: прямая проверка конкретного дашборда

Когда человек дал конкретный дашборд (id или slug — из URL):

```python
# Список чартов дашборда
mcp__superset__get_dashboard(id_or_slug="<id_or_slug>")
# Опционально: рендер всего дашборда, чтобы посмотреть глазами
mcp__superset__get_dashboard_screenshot(dashboard_id=<id>)
```

#### Поиск чартов с ошибкой
Прогони запрос каждого чарта и посмотри результат:
- `mcp__superset__get_chart_data(chart_id=...)` → падающий запрос вернёт **ошибку** (SQL/датасорс) прямо в ответе.
- Пусто там, где ожидались данные → проверь `mcp__superset__get_chart_params_summary` + `mcp__superset__get_dataset`.
- «Пусто, но без ошибки» (проблема визуализации/фронта) → `mcp__superset__get_chart_screenshot(chart_id=...)`.

#### Чтение деталей ошибки
`mcp__superset__get_chart_data` уже возвращает настоящую ошибку запроса. Для конфига
чарта и датасорса за ним: `mcp__superset__get_chart_params_summary(chart_id=...)` и
`mcp__superset__get_dataset(dataset_id=...)`.

## Прослеживание источника данных

### Шаг 1: найти датасет
Из ошибки чарта возьми имя датасета:
```python
mcp__superset__get_chart_params_summary(chart_id=<id>)
```

### Шаг 2: проверить датасет в Superset
```python
mcp__superset__get_dataset(dataset_id=<id>)
```
Посмотри исходную таблицу и SQL датасета.

### Шаг 3: проверить таблицу в ClickHouse/Trino
Выбери кластер ClickHouse по колонке «Кластер» в `context/marts.md` (см. также
скилл `clickhouse-sql`): `clickhouse-wms` для складских/операционных таблиц,
`clickhouse-dwh` для продаж/финансов/маркетинга. Реестр не помог — спроси
человека, не перебирай оба коннектора подряд.
```sql
-- таблица существует?
SELECT * FROM system.tables WHERE name = '<table_name>'

-- свежие данные?
SELECT max(date_column) FROM <schema.table>
```

### Шаг 4: найти витрину и её источник
Если таблица — golden-витрина, свериться с `context/marts.md`: кто владелец, какое
доверие, есть ли оговорки. Дальше источник и пайплайн, собирающий витрину, уточняются
у владельца — жёстко зашитого пути к репозиторию с пайплайнами в этом воркспейсе нет.
