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
      делает сам Claude Code. У Codex такого макроса нет, но это и не нужно:
      относительный путь в `args` разрешается относительно рабочего каталога,
      из которого запущен сам `codex` (проверено живым MCP-рукопожатием при
      ревью задачи Codex-3 — `uv run connectors/trino_proxy.py` в config.toml
      с относительным путём вернул реальный список инструментов trino, когда
      `codex exec` запущен из корня репозитория; предыдущая версия этого
      модуля ошибочно считала, что раз ${VAR}-подстановка в config.toml не
      работает (это факт про переменные окружения — docs/codex-facts.md,
      раздел 4), то и путь в args обязан быть абсолютным — это была
      непроверенная догадка по аналогии, а не факт, и она оказалась неверной).
      Поэтому `render_codex_toml` тоже пишет путь как есть, относительным —
      как и .mcp.json.

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

РЕШЁННАЯ НАХОДКА — два кластера ClickHouse под Codex (см. отчёт задачи
Codex-3, раздел "находки"). У Codex нет способа переслать переменную
окружения ребёнку под ДРУГИМ именем — механизм `env_vars` (проверено живым
запуском, docs/codex-facts.md, раздел 4, плюс собственная проверка:
`env_vars` в config.toml — только плоский список имён, TOML-таблица/маппинг
для него не парсится, схема требует "sequence") умеет только "переменная X
из окружения, что запустило codex, попадёт в дочерний процесс под тем же
именем X". Пакет mcp-clickhouse при этом ждёт фиксированные имена
(CLICKHOUSE_HOST и т.д.) — одинаковые что для WMS, что для DWH. Если оба
коннектора попросят переслать одну и ту же CLICKHOUSE_HOST, оба получат одно
и то же значение из общего окружения codex — второй кластер станет
недоступен молча (132 витрины из реестра, которые живут только на DWH).

Решение — поле `codex` у `Connector`: отдельный сценарий запуска
специально для Codex, только у `clickhouse-wms`/`clickhouse-dwh` (у
остальных семи `codex=None`, рендерятся из общего command/args/env, как и
раньше). Вместо прямого `uvx --with pyarrow mcp-clickhouse` Codex запускает
`connectors/clickhouse_proxy.py <wms|dwh>` — обёртку (тот же приём, что уже
применяют trino_proxy.py/superset_mcp.py/sheets_mcp.py), которая читает
CH_WMS_*/CH_DWH_* (эти имена уже не пересекаются — коллизии в `env_vars`
нет) и раскладывает их в CLICKHOUSE_* ВНУТРИ своего процесса, до запуска
настоящего пакета (os.execvpe) — то есть переименование происходит не в
config.toml (где было бы негде, см. выше), а в отдельном процессе на
каждый кластер.

Claude Code эту обёртку не использует — его `codex=None` тут неприменим
(поле относится только к Codex-ветке), а сам по себе Claude Code развязки
не требует: .mcp.json подставляет ${CH_WMS_HOST} в значение ключа
CLICKHOUSE_HOST ВНУТРИ env-словаря ОДНОГО процесса — clickhouse-wms и
clickhouse-dwh это два независимых процесса с двумя независимыми
env-словарями, делить им нечего, коллизии никогда не было. Решение
осознанное, не молчаливая асимметрия: единообразие "оба движка всегда через
обёртку" стоило бы лишнего процесса там, где Claude Code и так работает
корректно (см. отчёт задачи, раздел про выбор между "обёртка для обоих" и
"обёртка только для Codex").
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
class CodexLaunch:
    """Альтернативный сценарий запуска — только для Codex, только когда
    общий (Claude Code) сценарий не подходит из-за плоского `env_vars` (см.
    докстринг модуля, раздел "РЕШЁННАЯ НАХОДКА"). У большинства коннекторов
    этого поля нет вообще — `Connector.codex is None` значит "рендерить
    Codex из тех же command/args/env, что и Claude Code"."""
    command: str
    args: Tuple[ArgItem, ...]
    env: Tuple[EnvItem, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Connector:
    """Одно описание MCP-коннектора — источник для обоих форматов."""
    id: str
    command: str
    args: Tuple[ArgItem, ...]
    env: Tuple[EnvItem, ...] = field(default_factory=tuple)
    codex: "CodexLaunch" = None

    def env_vars(self) -> Tuple[EnvVar, ...]:
        return tuple(item for item in self.env if isinstance(item, EnvVar))

    def static_env(self) -> Tuple[StaticEnv, ...]:
        return tuple(item for item in self.env if isinstance(item, StaticEnv))

    def required_sources(self) -> Tuple[str, ...]:
        """Имена переменных в secrets.env, без которых коннектор бесполезен.

        Правило одно на все девять и берётся из самого описания: обязательна
        та переменная, у которой НЕТ дефолта. У секрета дефолт невозможен по
        построению, у структурной он либо есть (порт, адрес Jira), либо её
        неоткуда взять (адрес Grafana, путь к ключу Google) — и тогда она
        такая же обязательная, как токен.

        Совпадение с чужим прочтением, а не только с нашим: `claude mcp list`
        сам считает по `.mcp.json` «Missing environment variables» и выдаёт
        ровно эти же наборы (проверено живым запуском на клоне без кредов —
        JIRA_TOKEN у atlassian, GRAFANA_URL+GRAFANA_TOKEN у grafana и т.д.,
        и ни одной у trino).

        Считаем по обеим веткам запуска (общей и Codex): у clickhouse-* они
        просят одни и те же CH_*_*, но полагаться на это совпадение как на
        вечное не стоит.
        """
        names = []
        for branch in (self.env, self.codex.env if self.codex is not None else ()):
            for item in branch:
                if isinstance(item, EnvVar) and item.default is None:
                    if item.source not in names:
                        names.append(item.source)
        return tuple(names)

    def codex_spec(self):
        """(command, args, env) — то, что реально запускается под Codex:
        своя ветка (`self.codex`), если она задана, иначе общая с Claude
        Code. Живёт здесь, а не в генераторе конфига, потому что читателей
        у этого решения два: `tools/render_configs.py` (что писать в
        `env_vars` конфига Codex) и `connectors/codex_env_bridge.py` (какие
        переменные выставить в окружении перед запуском Codex). Две копии
        этой развилки разошлись бы при первой же правке `codex=...` у
        любого коннектора — и разошлись бы молча: конфиг просил бы одни
        имена, мостик выставлял бы другие."""
        if self.codex is not None:
            return self.codex.command, self.codex.args, self.codex.env
        return self.command, self.args, self.env


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
        # Codex: через обёртку — см. докстринг модуля, "РЕШЁННАЯ НАХОДКА".
        # target == source везде: обёртка сама переименовывает в CLICKHOUSE_*
        # уже внутри своего процесса, Codex-у достаточно переслать CH_WMS_*
        # как есть (тут коллизии с DWH нет — имена разные).
        codex=CodexLaunch(
            command="uv",
            args=("run", ProjectScript("connectors/clickhouse_proxy.py"), "wms"),
            env=(
                EnvVar(target="CH_WMS_HOST", source="CH_WMS_HOST", secret=False),
                EnvVar(target="CH_WMS_PORT", source="CH_WMS_PORT", secret=False, default="8123"),
                EnvVar(target="CH_WMS_USER", source="CH_WMS_USER", secret=True),
                EnvVar(target="CH_WMS_PASSWORD", source="CH_WMS_PASSWORD", secret=True),
                EnvVar(target="CH_WMS_SECURE", source="CH_WMS_SECURE", secret=False, default="false"),
            ),
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
        codex=CodexLaunch(
            command="uv",
            args=("run", ProjectScript("connectors/clickhouse_proxy.py"), "dwh"),
            env=(
                EnvVar(target="CH_DWH_HOST", source="CH_DWH_HOST", secret=False),
                EnvVar(target="CH_DWH_PORT", source="CH_DWH_PORT", secret=False, default="8123"),
                EnvVar(target="CH_DWH_USER", source="CH_DWH_USER", secret=True),
                EnvVar(target="CH_DWH_PASSWORD", source="CH_DWH_PASSWORD", secret=True),
                EnvVar(target="CH_DWH_SECURE", source="CH_DWH_SECURE", secret=False, default="false"),
            ),
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
            # Вход в Superset проходит сам коннектор: superset_mcp.py::_login
            # отправляет форму Keycloak (логин + пароль) и держит cookie —
            # браузер в этом не участвует. Раньше этих двух переменных тут не
            # было, а мастер писал «кредов не нужно, вход через SSO в
            # браузере»: коннектор числился включённым, поднимался и падал на
            # первом же запросе, потому что взять логин с паролем было
            # неоткуда (найдено на живой приёмке, под Claude Code).
            # Логин — корп-учётка, тот же класс, что CLICKHOUSE_USER:
            # secret=True, в закоммиченный конфиг попадает только имя
            # переменной.
            EnvVar(target="SUPERSET_USERNAME", source="SUPERSET_USERNAME", secret=True),
            EnvVar(target="SUPERSET_PASSWORD", source="SUPERSET_PASSWORD", secret=True),
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
