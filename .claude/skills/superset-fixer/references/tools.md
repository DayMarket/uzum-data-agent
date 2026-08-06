# Инструменты и справочные ссылки

## Справочные ссылки

- Обзорный дашборд Superset: https://bi.uzum.uz/superset/dashboard/superset_overview/
- Требования сертификации: см. скилл `superset-certifier`
- Порог error rate: < 7% (уровень сертификации 1)

## Доступные инструменты

| Инструмент | Назначение | Коннектор |
|---|---|---|
| `mcp__superset__get_dashboard` | Список чартов дашборда | superset |
| `mcp__superset__get_chart_data` | Прогнать запрос чарта → строки или ошибка | superset |
| `mcp__superset__get_chart_params_summary` | Конфиг/датасорс чарта | superset |
| `mcp__superset__get_chart_screenshot` / `mcp__superset__get_dashboard_screenshot` | Рендер PNG (визуальная проверка) | superset |
| `mcp__superset__update_chart` / `mcp__superset__normalize_dashboard_metrics` / `mcp__superset__update_dataset` | Применить фиксы | superset |
| SQL-запросы | Проверить таблицу/данные | clickhouse / trino |
| Поиск и lineage сущностей | Найти источник/пайплайн | openmetadata |
