---
name: trino-iceberg
description: Используй, когда нужен Trino-запрос поверх ClickHouse через system.query() или временная таблица в dwh-iceberg.sandbox для последующих сложных JOIN/агрегаций.
---

# Trino + ClickHouse Optimization

## Проверка реестра — обязательно до первого запроса

Прежде чем писать или выполнять запрос к конкретной витрине/таблице,
сверься с `context/marts.md`. Витрина помечена «🚫 запрещено — правило 1» —
останавливаешься сразу: не пишешь SQL, не создаёшь temp-таблицу в
`dwh-iceberg.sandbox`, не запрашиваешь через `system.query()`. Скажи
человеку, что задача касается зарплатных данных и почему (правило 1,
`CLAUDE.md`), и на этом всё.

## The system.query() Pattern

**CRITICAL:** When querying `dwh-clickhouse` from Trino, **always** create a temp table first in `dwh-iceberg.sandbox`.

### Why?
- Direct Trino → ClickHouse queries are slow
- Temp tables in Iceberg enable fast subsequent queries
- Allows complex Trino operations (joins, aggregations) on optimized data

### Naming Convention
```
<USER_PREFIX>_tmp_<task_name>_<YYYYMMDD>
```

Example: `<USER_PREFIX>_tmp_retention_analysis_20260121`

## Complete Pattern

### Step 1: Create temp table from ClickHouse
```sql
CREATE TABLE "dwh-iceberg".sandbox.<USER_PREFIX>_tmp_market_metrics_20260121 AS
SELECT
    session_id,
    user_id,
    order_amount
FROM TABLE(
    "dwh-clickhouse".system.query(
        query => 'SELECT
            session_id,
            user_id,
            order_amount
        FROM marts.order_items
        WHERE order_item_status NOT IN (''CREATED'', ''NOT_CREATED'')
          AND order_date_created >= today() - INTERVAL 90 DAY
          AND order_date_created < today()'
    )
);
```

### Step 2: Query temp table with Trino
```sql
SELECT
    user_id,
    COUNT(DISTINCT session_id) as sessions,
    SUM(order_amount) as total_amount
FROM "dwh-iceberg".sandbox.<USER_PREFIX>_tmp_market_metrics_20260121
GROUP BY user_id;
```

## Important Notes

### String Escaping
Inside `system.query()`, use **double single quotes** for string literals:
```sql
WHERE status NOT IN (''CREATED'', ''NOT_CREATED'')
```

### Catalog Names
- `"dwh-clickhouse"` — mirrors ClickHouse tables
- `"dwh-iceberg"` — for temp/intermediate tables
- Always quote catalog names with hyphens

### When to Use Trino vs ClickHouse

| Use Case | Trino | ClickHouse |
|----------|-------|------------|
| Cross-catalog joins | ✅ | ❌ |
| Complex transformations | ✅ | ⚠️ |
| Simple queries | ❌ | ✅ (faster) |
| Ad-hoc exploration | ❌ | ✅ |

### Cleanup
Remember to drop temp tables when done:
```sql
DROP TABLE IF EXISTS "dwh-iceberg".sandbox.<USER_PREFIX>_tmp_market_metrics_20260121;
```

## Direct Trino Tables (No system.query needed)
Some tables exist natively in Iceberg and don't need the system.query pattern:
- Check `"dwh-iceberg".sandbox.*` for existing temp tables
- Check `"dwh-iceberg".marts.*` for pre-built Iceberg marts

## Доступ

Коннектор `trino` в `.mcp.json` — свой Python-скрипт (`connectors/trino_proxy.py`),
вход через OAuth2 SSO в браузере при первом запросе. Инструменты:
`execute_query`, `list_catalogs`, `list_schemas`, `list_tables`, `describe_table`.
