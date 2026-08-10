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
    -- Признак движка: 'claude' | 'codex'. DEFAULT 'claude' — не потому что
    -- Claude Code как-то привилегирован, а потому что все строки, вставленные
    -- до этой колонки, реально от него: Codex-порта тогда не было (см.
    -- docs/superpowers/specs/2026-08-07-codex-port-design.md, задача Codex-4).
    -- Значение пишет .claude/hooks/log_session.py по hook_payload.detect_engine() —
    -- по содержимому события, не по переменной окружения.
    engine       LowCardinality(String) DEFAULT 'claude',
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
    -- ok сохранён для обратной совместимости с уже существующими запросами:
    -- 0 означает ТОЛЬКО подтверждённый отказ (как и раньше — для Claude
    -- Code третьего состояния не бывает в принципе). Он больше не
    -- единственный источник правды про исход — см. колонку outcome.
    ok          UInt8,
    -- Ревью-находка 3 (задача Codex-4): исход вызова, три состояния, а не
    -- два. У Codex значительная доля вызовов заканчивается тем, что мы не
    -- смогли прочитать транскрипт вовремя (гонка хук/запись на диск — см.
    -- отчёт задачи) — раньше такая строка писалась как ok=1/error_text="",
    -- неотличимо от настоящего подтверждённого успеха, и "процент падений"
    -- Codex был смещён в оптимистичную сторону молча. DEFAULT 'ok' — не
    -- потому что неизвестность где-то реже встречается, а потому что все
    -- строки, вставленные до этой колонки, — от Claude Code, у которого
    -- исход всегда детерминирован (см. .claude/hooks/log_event.py).
    -- "Сколько упало": countIf(outcome = 'failed'). "Какая доля непроверяема
    -- (не путать с успехом)": countIf(outcome = 'unknown').
    outcome     LowCardinality(String) DEFAULT 'ok',
    error_text  String,
    -- Признак движка — то же назначение, что у sandbox.ai_usage_sessions.engine,
    -- см. комментарий там. Дополнительно к 'claude'/'codex' здесь возможно
    -- 'unknown' — lib/hook_payload.detect_engine() не опознал событие ни по
    -- одному из двух известных форматов транскрипта (ревью-находка 4,
    -- задача Codex-4): раньше такое событие молча помечалось 'claude', что
    -- было тем же классом ошибки, что и провал с permission_mode чуть выше
    -- по хронологии задачи, только на уровень ниже. DEFAULT остаётся
    -- 'claude', а не 'unknown', ровно по той же причине, что и у outcome:
    -- он про историю уже вставленных строк (до Codex-порта), а не про то,
    -- как код должен угадывать на новых, непонятных событиях.
    engine      LowCardinality(String) DEFAULT 'claude',
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


-- ── Что нужно применить на уже созданных таблицах ────────────────────────
-- Выполняет человек с правами записи (MCP-сервер uzum-wms-clickhouse-write
-- или клиент под учёткой с доступом на запись). Обе команды идемпотентны.
--
-- 1. ПРИМЕНЕНО 10.08.2026. Убрать колонку cost_usd: она всегда писалась
--    нулём, из схемы и из хука удалена (см. комментарий выше). Решение
--    подтверждено на живых данных: во всех строках обоих движков там было
--    0.0000. Считать стоимость честно — значит вести прайс по
--    идентификаторам моделей и переписывать историю при смене тарифа;
--    токены (in/out/cache) пишутся точно, и стоимость считается по ним
--    запросом, когда прайс появится.
--
--    ALTER TABLE sandbox.ai_usage_sessions ON CLUSTER default DROP COLUMN IF EXISTS cost_usd;
--
-- 2. ПРИМЕНЕНО 10.08.2026. Выкинуть отладочные строки, вставленные при
--    проверке схемы 06.08.2026: их оказалось по ДВЕ в каждой таблице —
--    'schema-check' и 'schema-check-utc' (вторая появилась при проверке
--    перехода на UTC и в этот список раньше не попала).
--
--    ALTER TABLE sandbox.ai_usage_events   ON CLUSTER default DELETE WHERE user IN ('schema-check', 'schema-check-utc');
--    ALTER TABLE sandbox.ai_usage_sessions ON CLUSTER default DELETE WHERE user IN ('schema-check', 'schema-check-utc');
--    ALTER TABLE sandbox.ai_usage_verdicts ON CLUSTER default DELETE WHERE user IN ('schema-check', 'schema-check-utc');
--
--    Проверено счётчиками до и после: sessions 8 → 6, events 57 → 55,
--    verdicts 2 → 0; строк с этими user'ами не осталось ни в одной таблице,
--    остальные строки не затронуты.
--
-- 3. Признак движка и исход вызова (задача Codex-4, порт под Codex):
--    добавить колонку engine в sessions и events, колонку outcome — в
--    events. DEFAULT закрывает и старые строки (ADD COLUMN ... DEFAULT в
--    MergeTree отдаёт значение по умолчанию при чтении для строк,
--    записанных до миграции, без переписи данных на диске), и новые
--    вставки от Claude Code, которые ещё не обновили хуки.
--
--    Перед миграцией проверен фактом риск "формат вставки строгий, лишнее
--    поле — ошибка" (ревью-находка 2): вставил в ЖИВУЮ таблицу sandbox.
--    ai_usage_events (ДО этой миграции, колонки engine ещё нет) строку с
--    лишним полем "engine" через тот же путь, что использует lib/telemetry.py
--    (INSERT ... FORMAT JSONEachRow). Ответ сервера — 200, строка вставлена,
--    лишнее поле молча отброшено. Причина — `input_format_skip_unknown_fields`
--    на этом кластере равен 1 (проверено: `SELECT value FROM system.settings
--    WHERE name = 'input_format_skip_unknown_fields'` → 1, changed=0, то
--    есть это действующее значение по умолчанию, а не разовая настройка
--    сессии). Риск был реальным по конструкции запроса (в lib/telemetry.py
--    это поведение нигде явно не задано), но не сработал на практике на
--    этом конкретном кластере — задокументировано здесь, чтобы в следующий
--    раз не проверять заново на другом кластере с других значением по
--    умолчанию.
--
--    ALTER TABLE sandbox.ai_usage_sessions ON CLUSTER default
--        ADD COLUMN IF NOT EXISTS engine LowCardinality(String) DEFAULT 'claude';
--    ALTER TABLE sandbox.ai_usage_events ON CLUSTER default
--        ADD COLUMN IF NOT EXISTS engine LowCardinality(String) DEFAULT 'claude';
--    ALTER TABLE sandbox.ai_usage_events ON CLUSTER default
--        ADD COLUMN IF NOT EXISTS outcome LowCardinality(String) DEFAULT 'ok';
--
--    Применено 07.08.2026, напрямую по HTTP с кредами
--    mcp_servers["uzum-wms-clickhouse-write"] из .mcp.json (write-MCP-сервер
--    в рабочей сессии не поднят, но креды к нему есть) — подтверждено
--    чтением DESCRIBE TABLE после миграции и вставкой+чтением тестовой
--    строки с обеими новыми колонками, см. отчёт задачи.
