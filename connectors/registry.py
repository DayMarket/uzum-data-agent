#!/usr/bin/env python3
"""Единственное описание MCP-коннекторов — источник для обоих движков.

До этого коннекторы были описаны только в `.mcp.json` (формат Claude Code).
Codex ждёт их в `[mcp_servers.<id>]` внутри `.codex/config.toml` — другой
формат, другой механизм секретов (см. docs/codex-facts.md, раздел 4).
Держать оба файла руками — гарантированное расхождение при первой же правке
(добавили переменную одному движку — забыли другому). Поэтому описание одно,
здесь, а `tools/render_configs.py` рисует из него оба формата.

Источник истины для содержания — .mcp.json на момент переноса (задача Codex-3
дизайн-спеки). Ничего не выдумано и не улучшено по дороге: девять коннекторов,
те же command/args/env, тот же набор секретных и структурных переменных, что
и раньше. Если у переноса всё же есть дефект — он не тут, а описан отдельно
(см. docs/codex-facts.md и отчёт задачи).

Ключевые понятия:

  ProjectScript — путь к локальному скрипту коннектора относительно корня
      репозитория (например "connectors/trino_proxy.py"). В .mcp.json это
      разворачивается макросом `${CLAUDE_PROJECT_DIR:-.}/<path>` — подстановку
      делает сам Claude Code. У Codex такого макроса нет, а `${VAR}` в
      config.toml не подставляется вообще (проверено запуском, см.
      docs/codex-facts.md, раздел 4) — там путь обязан быть абсолютным,
      посчитанным во время генерации, а не строкой-плейсхолдером.

  StaticEnv — литеральная константа окружения: нет источника в .env, значение
      одинаковое всегда и везде (например MCP_TRANSPORT=stdio). Не секрет —
      будь она секретом, её бы не записывали константой. Пишется как есть в
      обоих форматах.

  EnvVar — переменная, значение которой приходит от аналитика (.env,
      окружение). У неё есть `target` — имя, под которым переменную ждёт сам
      процесс коннектора (часть контракта стороннего пакета, мы его не
      выбираем), и `source` — имя переменной в .env/окружении, из которой
      значение берётся. Для большинства коннекторов source совпадает с
      target; там, где имя пакета отличается от нашего соглашения о том, как
      называть секрет (ClickHouse: CLICKHOUSE_HOST ← CH_WMS_HOST/CH_DWH_HOST;
      Jira/Confluence: JIRA_PERSONAL_TOKEN и CONFLUENCE_PERSONAL_TOKEN оба ←
      JIRA_TOKEN; Grafana: GRAFANA_SERVICE_ACCOUNT_TOKEN ← GRAFANA_TOKEN;
      OpenMetadata: OPENMETADATA_URI/OPENMETADATA_JWT_TOKEN ← OMD_URL/
      OMD_TOKEN; GrowthBook: GB_API_KEY ← GROWTHBOOK_TOKEN; Sheets:
      GOOGLE_SERVICE_ACCOUNT_FILE ← GOOGLE_SA_FILE) — они разные.

      `secret` разделяет переменную на секретную (пароль, токен) и
      структурную (адрес, порт, признак https). У секрета `default` обязан
      быть None: дефолт для секрета — это буквально зашитое в git значение,
      даже если формально это всё ещё подстановка переменной (тот же класс
      регрессии, что ловит tests/test_mcp_config.py для .mcp.json).

ВАЖНАЯ НЕДОКОНЦА (см. отчёт задачи Codex-3, раздел "находки"): у Codex нет
способа переслать переменную окружения ребёнку под ДРУГИМ именем — механизм
`env_vars` (проверено живым запуском, docs/codex-facts.md, раздел 4, плюс
собственная проверка: `env_vars` в config.toml — только плоский список имён,
TOML-таблица/маппинг для него не парсится, схема требует "sequence") умеет
только "переменная X из окружения, что запустило codex, попадёт в дочерний
процесс под тем же именем X". Это значит: там, где `target != source`
(ClickHouse, Jira/Confluence, Grafana, OpenMetadata, GrowthBook, Sheets),
`render_codex_toml` показывает `env_vars` по имени `target` (это то имя,
которое реально нужно дочернему процессу) — но чтобы это заработало, ту же
переменную нужно ЭКСПОРТИРОВАТЬ под именем `target` в окружение, из которого
запускается `codex` (это забота мастера установки, не этого модуля). Для двух
кластеров ClickHouse (`clickhouse-wms`, `clickhouse-dwh`) это в принципе не
решается экспортом: обоим нужен один и тот же `target`-имя `CLICKHOUSE_HOST`
(и т.д.) одновременно с разными значениями, а `codex` — один процесс с одним
общим окружением на оба дочерних MCP-сервера. Без обёртки-скрипта (по образцу
уже существующих connectors/trino_proxy.py, superset_mcp.py, sheets_mcp.py),
которая переименовывает переменные перед запуском настоящего пакета,
одновременная работа обоих кластеров ClickHouse под Codex не сходится. Это
архитектурный дефект, найденный при переносе, а не что-то, что этот модуль
предполагается чинить сам по себе.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple, Union


@dataclass(frozen=True)
class ProjectScript:
    """Путь к локальному скрипту коннектора относительно корня репозитория."""
    path: str


@dataclass(frozen=True)
class StaticEnv:
    """Литеральная константа окружения — без источника, одинакова везде."""
    name: str
    value: str


@dataclass(frozen=True)
class EnvVar:
    """Переменная окружения, значение которой приходит от аналитика."""
    target: str
    source: str
    secret: bool
    default: str = None  # разрешено только когда secret=False

    def __post_init__(self):
        if self.secret and self.default is not None:
            raise ValueError(
                f"{self.target}: у секретной переменной не может быть дефолта "
                f"({self.default!r}) — это зашитое в git значение"
            )


EnvItem = Union[EnvVar, StaticEnv]
ArgItem = Union[str, ProjectScript]


@dataclass(frozen=True)
class Connector:
    """Одно описание MCP-коннектора — источник для обоих форматов."""
    id: str
    command: str
    args: Tuple[ArgItem, ...]
    env: Tuple[EnvItem, ...] = field(default_factory=tuple)

    def env_vars(self) -> Tuple[EnvVar, ...]:
        return tuple(item for item in self.env if isinstance(item, EnvVar))

    def static_env(self) -> Tuple[StaticEnv, ...]:
        return tuple(item for item in self.env if isinstance(item, StaticEnv))


# Порядок — тот же, что в исходном .mcp.json: atlassian, clickhouse-wms,
# clickhouse-dwh, trino, superset, grafana, openmetadata, growthbook, sheets.
CONNECTORS: Tuple[Connector, ...] = (
    Connector(
        id="atlassian",
        command="uvx",
        args=("mcp-atlassian",),
        env=(
            EnvVar(target="JIRA_URL", source="JIRA_URL", secret=False,
                   default="https://jira.uzum.com"),
            EnvVar(target="JIRA_PERSONAL_TOKEN", source="JIRA_TOKEN", secret=True),
            EnvVar(target="CONFLUENCE_URL", source="CONFLUENCE_URL", secret=False,
                   default="https://confluence.uzum.com"),
            # Тот же JIRA_TOKEN — один PAT на Jira и Confluence разом, не два
            # независимых секрета (см. .mcp.json / connectors/ACCESS.md).
            EnvVar(target="CONFLUENCE_PERSONAL_TOKEN", source="JIRA_TOKEN", secret=True),
        ),
    ),
    Connector(
        id="clickhouse-wms",
        command="uvx",
        args=("--with", "pyarrow", "mcp-clickhouse"),
        env=(
            StaticEnv("MCP_TRANSPORT", "stdio"),
            EnvVar(target="CLICKHOUSE_HOST", source="CH_WMS_HOST", secret=False),
            EnvVar(target="CLICKHOUSE_PORT", source="CH_WMS_PORT", secret=False, default="8123"),
            EnvVar(target="CLICKHOUSE_USER", source="CH_WMS_USER", secret=True),
            EnvVar(target="CLICKHOUSE_PASSWORD", source="CH_WMS_PASSWORD", secret=True),
            EnvVar(target="CLICKHOUSE_SECURE", source="CH_WMS_SECURE", secret=False, default="false"),
            StaticEnv("CLICKHOUSE_VERIFY", "false"),
            StaticEnv("CHDB_ENABLED", "false"),
        ),
    ),
    Connector(
        id="clickhouse-dwh",
        command="uvx",
        args=("--with", "pyarrow", "mcp-clickhouse"),
        env=(
            StaticEnv("MCP_TRANSPORT", "stdio"),
            EnvVar(target="CLICKHOUSE_HOST", source="CH_DWH_HOST", secret=False),
            EnvVar(target="CLICKHOUSE_PORT", source="CH_DWH_PORT", secret=False, default="8123"),
            EnvVar(target="CLICKHOUSE_USER", source="CH_DWH_USER", secret=True),
            EnvVar(target="CLICKHOUSE_PASSWORD", source="CH_DWH_PASSWORD", secret=True),
            EnvVar(target="CLICKHOUSE_SECURE", source="CH_DWH_SECURE", secret=False, default="false"),
            StaticEnv("CLICKHOUSE_VERIFY", "false"),
            StaticEnv("CHDB_ENABLED", "false"),
        ),
    ),
    Connector(
        id="trino",
        command="uv",
        args=("run", ProjectScript("connectors/trino_proxy.py")),
        env=(
            EnvVar(target="TRINO_HOST", source="TRINO_HOST", secret=False,
                   default="trino.prod-data.internal.daymarket.uz"),
            EnvVar(target="TRINO_CATALOG", source="TRINO_CATALOG", secret=False,
                   default="dwh-iceberg"),
        ),
    ),
    Connector(
        id="superset",
        command="uv",
        args=("run", ProjectScript("connectors/superset_mcp.py")),
        env=(
            EnvVar(target="SUPERSET_URL", source="SUPERSET_URL", secret=False),
        ),
    ),
    Connector(
        id="grafana",
        command="uvx",
        args=("mcp-grafana",),
        env=(
            EnvVar(target="GRAFANA_URL", source="GRAFANA_URL", secret=False),
            EnvVar(target="GRAFANA_SERVICE_ACCOUNT_TOKEN", source="GRAFANA_TOKEN", secret=True),
        ),
    ),
    Connector(
        id="openmetadata",
        command="uvx",
        args=("--with", "fastmcp<2.4", "--with", "pydantic<2.9",
              "--from", "mcp-openmetadata", "python", "-m", "mcp_openmetadata.server"),
        env=(
            EnvVar(target="OPENMETADATA_URI", source="OMD_URL", secret=False),
            EnvVar(target="OPENMETADATA_JWT_TOKEN", source="OMD_TOKEN", secret=True),
        ),
    ),
    Connector(
        id="growthbook",
        command="npx",
        args=("-y", "@growthbook/mcp"),
        env=(
            EnvVar(target="GB_API_KEY", source="GROWTHBOOK_TOKEN", secret=True),
        ),
    ),
    Connector(
        id="sheets",
        command="uv",
        args=("run", ProjectScript("connectors/sheets_mcp.py")),
        env=(
            # Путь до файла сервисного аккаунта — не секрет сам по себе (см.
            # .env.example): указывает, ГДЕ лежит файл с приватным ключом, а
            # не содержит ключ в конфиге.
            EnvVar(target="GOOGLE_SERVICE_ACCOUNT_FILE", source="GOOGLE_SA_FILE", secret=False),
            EnvVar(target="GOOGLE_SHEETS_FOLDER_ID", source="GOOGLE_SHEETS_FOLDER_ID", secret=False),
        ),
    ),
)


CONNECTORS_BY_ID = {c.id: c for c in CONNECTORS}
assert len(CONNECTORS_BY_ID) == len(CONNECTORS), "дублирующийся id коннектора"
