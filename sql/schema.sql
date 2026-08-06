-- Таблицы телеметрии живут в существующей базе sandbox (прав на CREATE DATABASE
-- нет и не будет), с префиксом ai_usage_, чтобы не теряться среди остальных
-- 318+ таблиц sandbox. Движок ReplicatedMergeTree — как у всех соседних таблиц
-- в sandbox (кластер реплицированный).
--
-- ON CLUSTER default обязателен: кластер за балансировщиком wms-clickhouse
-- состоит из двух хостов (system.clusters, cluster='default'), и CREATE TABLE
-- без ON CLUSTER выполняется только на том хосте, который принял конкретный
-- HTTP-запрос — проверено эмпирически (см. task-3-report.md, раунд 4):
-- без ON CLUSTER таблица была видна в system.tables через раз, в зависимости
-- от того, какой хост ответил на запрос балансировщика.
--
-- Все колонки времени — DateTime('UTC') / DateTime64(3, 'UTC') явно.
-- Причина: ноутбуки аналитиков не обязаны стоять в Asia/Tashkent (часовой
-- пояс сервера ClickHouse), а наивная DateTime-строка без указания зоны
-- интерпретируется ClickHouse в часовом поясе КОЛОНКИ, а не отправителя —
-- при разных поясах машины и сервера события расходятся во времени молча,
-- без единой ошибки на вставке или чтении (см. task-3-report.md, раунд 5).
-- lib/telemetry.py.utc_now_str() формирует строки в UTC без суффикса зоны —
-- ровно то, что здесь и ожидается. При чтении для дашбордов/отчётов в
-- Ташкентском времени приводить явно: toTimeZone(col, 'Asia/Tashkent').

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_sessions ON CLUSTER default
(
    session_id   String,
    user         LowCardinality(String),
    started_at   DateTime('UTC'),
    ended_at     DateTime('UTC'),
    duration_s   UInt32,
    jira_key     String,
    skills_used  Array(LowCardinality(String)),
    n_prompts    UInt16,
    n_tools      UInt16,
    tokens_in    UInt64,
    tokens_out   UInt64,
    tokens_cache UInt64,
    -- Колонки cost_usd здесь нет намеренно. Она была, но всегда писалась
    -- нулём: считать стоимость честно значит вести прайс по идентификаторам
    -- моделей из транскрипта (claude-opus-4-8, claude-fable-5, <synthetic>),
    -- держать его в актуальном состоянии и обновлять историю при смене
    -- тарифа. Пустая колонка «стоимость» в отчётности хуже отсутствующей:
    -- по ней строят выводы. Токены (in/out/cache) пишутся точно — стоимость
    -- считается по ним запросом, когда прайс понадобится.
    repo_sha     String,
    end_reason   LowCardinality(String),
    transcript   String CODEC(ZSTD(3)),
    inserted_at  DateTime('UTC') DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_sessions', '{replica}')
PARTITION BY toYYYYMM(started_at)
ORDER BY (user, started_at, session_id)
TTL started_at + INTERVAL 12 MONTH;

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_events ON CLUSTER default
(
    ts          DateTime64(3, 'UTC'),
    session_id  String,
    user        LowCardinality(String),
    event_type  LowCardinality(String),
    tool_name   LowCardinality(String),
    mcp_server  LowCardinality(String),
    duration_ms UInt32,
    ok          UInt8,
    error_text  String,
    inserted_at DateTime('UTC') DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_events', '{replica}')
PARTITION BY toYYYYMM(ts)
ORDER BY (user, ts)
TTL toDateTime(ts) + INTERVAL 12 MONTH;

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_verdicts ON CLUSTER default
(
    jira_key    String,
    session_id  String,
    user        LowCardinality(String),
    job         LowCardinality(String),
    drafted_at  DateTime('UTC'),
    verdict_at  DateTime('UTC'),
    verdict     LowCardinality(String),
    reason      String,
    inserted_at DateTime('UTC') DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_verdicts', '{replica}')
ORDER BY (jira_key, drafted_at);
