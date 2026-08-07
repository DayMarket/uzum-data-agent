---
description: Используй, когда пишешь или проверяешь SQL для ClickHouse — часовые пояса, тяжёлые таблицы, паттерны JOIN и типовые расчётные метрики.
---

# ClickHouse SQL Patterns

## Проверка реестра — обязательно до первого запроса

Прежде чем писать или выполнять запрос к конкретной витрине/таблице,
сверься с `context/marts.md`. Витрина помечена «🚫 запрещено — правило 1» —
останавливаешься сразу: не пишешь SQL, не тестируешь запрос через
MCP-коннектор, не смотришь «одним глазком». Скажи человеку, что задача
касается зарплатных данных и почему (правило 1, `CLAUDE.md`), и на этом всё.

## Timezone Convention
**CRITICAL:** All dates use `Asia/Tashkent` timezone:

```sql
toDate(order_date_created, 'Asia/Tashkent') >= today() - 30

toDate(received_at, 'Asia/Tashkent') = today()
```

## Heavy Tables - Handle with Care

### clickstream_b2c.events
Massive event log table. **ALWAYS** apply strict date filters:

```sql
SELECT event_type, platform, widget_space_name
FROM clickstream_b2c.events
WHERE toDate(received_at, 'Asia/Tashkent') = today()
```

**Rules:**
- Testing: Use only today's data
- Multi-day (2-3+ days): Use daily loops to avoid timeouts
- **NEVER** query 30+ days in one statement

### marketing.sessions_with_attribution
Large table partitioned by `date_uz`:

```sql
SELECT session_id, user_id, attributed_source
FROM marketing.sessions_with_attribution
WHERE date_uz = today()
```

**Always filter by `date_uz`** for partition pruning.

## Core Tables Reference

| Table | Purpose | Date Filter |
|-------|---------|-------------|
| `marts.order_items` | Order-level sales | `toDate(order_date_created, 'Asia/Tashkent')` |
| `marts_b2c.finance_margin_by_order_item` | Financial metrics | `dt = date_created` or `dt = date_issued` |
| `marts.sellers_info` | Seller reference data | N/A |
| `golden.efficiency_mart` | OPH/OPS производительность | см. `context/marts.md` |
| `golden.hrops_main_metrics` | HR Ops метрики | всегда дедуплицируй по `worker_id`, см. `context/marts.md` |

### Finance Table Usage
```sql
SELECT daily_uzs_to_usd(dt, gmv_completed) as gmv_usd
FROM marts_b2c.finance_margin_by_order_item
WHERE dt = date_issued
```

## CM2 (Contribution Margin 2) Pattern

```sql
SELECT 
    round(sum(IF(isNaN(prlcm2.cm_2_var_1_2_usd), 0, prlcm2.cm_2_var_1_2_usd)) 
          / nullIf(count(DISTINCT ap.account_id), 0), 2) as cm2_per_user
FROM marketing.account_properties ap
JOIN marts.order_items oi ON ap.account_id = oi.account_id
JOIN silver.preliminary_cm2_by_order_item prlcm2 
     ON toDecimal64(oi.order_item_id, 0) = prlcm2.order_item_id
WHERE toDate(oi.order_date_issued, 'Asia/Tashkent') <= ap.fo_date_issued_uz + 90
  AND oi.order_item_status NOT IN ('CREATED', 'NOT_CREATED')
```

**Key points:**
- Use `IF(isNaN(...), 0, ...)` to handle NaN values
- Join on `toDecimal64(oi.order_item_id, 0) = prlcm2.order_item_id`
- Filter by `fo_date_issued_uz + 90` for 90-day LTV window

## Code Validation - MANDATORY

**NEVER present untested SQL:**

1. Write the query
2. Test via the `trino` or `clickhouse` MCP-коннектор
3. Verify results make logical sense
4. Fix any errors
5. Test again
6. Only then present to user

## SQL File Formatting

**CRITICAL:** Never start SQL files with comments:

```sql
SELECT * FROM marts.order_items
WHERE toDate(order_date_created, 'Asia/Tashkent') >= today() - 30
```

**NOT:**
```sql
-- This query analyzes order data  <- WRONG
SELECT * FROM marts.order_items
```

## Best Practices

- Use CTEs for readability
- Add `LIMIT` when exploring
- Always use partition keys in WHERE clause
- Marketing marts are rebuilt daily (data freshness D-1)
- Test queries via MCP before presenting

## Table/Column Disambiguation

When multiple tables could match:

```
"Нашёл несколько таблиц с данными по заказам:
- marts.order_items — транзакционные заказы
- marketing.orders_with_attribution — заказы с атрибуцией сессий
- marketing.performance_report — агрегаты по сессиям

Какую использовать?"
```

**Always ask** if uncertain about which table to use.

## Long-Running Commands

Commands estimated to take >15 minutes → run in **nohup** background:

```bash
nohup clickhouse-client --query "SELECT ... FROM large_table" > output.txt 2>&1 &
```

Then inform user:
```
"Запрос выполняется в фоне (~30 мин).
Скажи, когда закончится — разберу результаты."
```

**Why:**
- Prevents timeout issues
- Allows parallel work
- Maintains workflow efficiency
