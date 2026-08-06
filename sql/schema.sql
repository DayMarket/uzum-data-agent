-- Таблицы телеметрии живут в существующей базе sandbox (прав на CREATE DATABASE
-- нет и не будет), с префиксом ai_usage_, чтобы не теряться среди остальных
-- 318+ таблиц sandbox. Движок ReplicatedMergeTree — как у всех соседних таблиц
-- в sandbox (кластер реплицированный).

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_sessions
(
    session_id   String,
    user         LowCardinality(String),
    started_at   DateTime,
    ended_at     DateTime,
    duration_s   UInt32,
    jira_key     String,
    skills_used  Array(LowCardinality(String)),
    n_prompts    UInt16,
    n_tools      UInt16,
    tokens_in    UInt64,
    tokens_out   UInt64,
    tokens_cache UInt64,
    cost_usd     Decimal(10, 4),
    repo_sha     String,
    end_reason   LowCardinality(String),
    transcript   String CODEC(ZSTD(3)),
    inserted_at  DateTime DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_sessions', '{replica}')
PARTITION BY toYYYYMM(started_at)
ORDER BY (user, started_at, session_id)
TTL started_at + INTERVAL 12 MONTH;

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_events
(
    ts          DateTime64(3),
    session_id  String,
    user        LowCardinality(String),
    event_type  LowCardinality(String),
    tool_name   LowCardinality(String),
    mcp_server  LowCardinality(String),
    duration_ms UInt32,
    ok          UInt8,
    error_text  String,
    inserted_at DateTime DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_events', '{replica}')
PARTITION BY toYYYYMM(ts)
ORDER BY (user, ts)
TTL toDateTime(ts) + INTERVAL 12 MONTH;

CREATE TABLE IF NOT EXISTS sandbox.ai_usage_verdicts
(
    jira_key    String,
    session_id  String,
    user        LowCardinality(String),
    job         LowCardinality(String),
    drafted_at  DateTime,
    verdict_at  DateTime,
    verdict     LowCardinality(String),
    reason      String,
    inserted_at DateTime DEFAULT now()
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/{shard}/sandbox/ai_usage_verdicts', '{replica}')
ORDER BY (jira_key, drafted_at);
