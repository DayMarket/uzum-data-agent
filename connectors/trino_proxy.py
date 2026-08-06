#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "mcp>=1.0.0,<2",
#     "trino>=0.333.0",
#     "pandas>=2.0.0",
# ]
# ///
"""MCP-сервер для Trino: SQL-запросы к dwh-iceberg через каталог Trino.

Перенесено из харнесса opencode-lite-bundle (shared/mcp_local/trino-mcp.py).
В харнессе это был тонкий клиент, который ходил на localhost:8080 к отдельному
процессу trino_proxy_server.py — тот держал OAuth2-токен и проксировал HTTP.
Мы держим один процесс: подключаемся к прод-Trino напрямую и сами владеем
OAuth2Authentication (пакет trino сам открывает браузер для SSO при первом
запросе и обновляет токен по истечении — см. ACCESS.md). Убрали также файл
~/.config/opencode/trino_auth_url.txt, которым headless-контейнер харнесса
передавал ссылку на вход агенту: у нас процесс работает на ноутбуке аналитика,
браузер открывается тем же процессом напрямую.

Только стандартная библиотека Python в остальных коннекторах репозитория, но
у этого файла есть PEP 723 заголовок выше (`# /// script`) с тремя пакетами
(mcp, trino, pandas) — Claude Code запускает его через `uv run`, который сам
разворачивает изолированное окружение под эти зависимости, без ручного
`pip install` (тот же принцип, что и `uvx` для остальных пяти MCP-серверов
в .mcp.json).

Переменные окружения:
  TRINO_HOST     хост Trino (по умолчанию прод: trino.prod-data.internal.daymarket.uz)
  TRINO_PORT     порт (по умолчанию 443, HTTPS)
  TRINO_CATALOG  каталог (по умолчанию dwh-iceberg)
  TRINO_SCHEMA   схема (по умолчанию default)
  TRINO_USER     твой корп. email; если не задан в окружении — берём из
                 ~/.config/uzum-ai/secrets.env (ключ TRINO_USER)
"""
from __future__ import annotations

import asyncio
import json
import os

import pandas as pd
from mcp import types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from trino.auth import OAuth2Authentication
from trino.dbapi import connect

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")

# Глобальное подключение
_global_connection = None


def _read_secrets_env(path):
    """Прочитать простой KEY=VALUE файл секретов. Отсутствие файла — не ошибка."""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def _trino_user():
    """TRINO_USER берём из окружения, а если там пусто — из secrets.env."""
    user = os.getenv("TRINO_USER", "").strip()
    if user:
        return user
    user = _read_secrets_env(SECRETS_PATH).get("TRINO_USER", "").strip()
    if not user:
        raise RuntimeError(
            "TRINO_USER не задан: добавь строку TRINO_USER=твой.email@uzum.com "
            f"в {SECRETS_PATH}"
        )
    return user


def get_trino_connection():
    """Создаёт или возвращает существующее подключение к Trino."""
    global _global_connection

    if _global_connection is not None:
        try:
            cursor = _global_connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return _global_connection
        except Exception:
            _global_connection = None

    # request_timeout ограничивает КАЖДЫЙ HTTP-запрос к Trino (в т.ч. health-probe
    # SELECT 1 выше): без него запрос на протухший keep-alive сокет висит
    # бесконечно. Это таймаут на один HTTP-обмен, а не на весь запрос —
    # long-running query опрашивается многими быстрыми поллами, поэтому 120s не
    # режет тяжёлые запросы.
    config = {
        "host": os.getenv("TRINO_HOST", "trino.prod-data.internal.daymarket.uz"),
        "port": int(os.getenv("TRINO_PORT", "443")),
        "user": _trino_user(),
        "catalog": os.getenv("TRINO_CATALOG", "dwh-iceberg"),
        "schema": os.getenv("TRINO_SCHEMA", "default"),
        "http_scheme": "https",
        "auth": OAuth2Authentication(),
        "verify": False,
        "request_timeout": float(os.getenv("TRINO_REQUEST_TIMEOUT", "120")),
    }

    _global_connection = connect(**config)
    return _global_connection


AUTH_ERROR_MARKERS = ("401", "unauthorized", "authentication", "oauth", "token")
CONN_ERROR_MARKERS = ("timeout", "timed out", "connection", "eof occurred",
                      "broken pipe", "reset by peer", "closed", "refused")


def is_auth_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(marker in lowered for marker in AUTH_ERROR_MARKERS)


def is_conn_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(marker in lowered for marker in CONN_ERROR_MARKERS)


def auth_required_result(error_text: str) -> dict:
    instruction = (
        "Trino требует входа. OAuth2Authentication уже должен был открыть окно "
        "браузера для SSO — попроси пользователя завершить вход там, дождись "
        "подтверждения и повтори запрос один раз. Если браузер не открылся сам, "
        "предложи перезапустить сессию Claude Code."
    )
    return {
        "success": False,
        "error": f"TRINO_AUTH_REQUIRED: {error_text}",
        "action_required": instruction,
        "data": [],
        "columns": [],
        "row_count": 0,
    }


def execute_query(query: str) -> dict:
    """Выполняет запрос к Trino"""
    global _global_connection
    try:
        conn = get_trino_connection()
        df = pd.read_sql_query(query, conn)

        # Конвертируем datetime в строки
        for col in df.columns:
            if "datetime" in str(df[col].dtype):
                df[col] = df[col].astype(str)

        return {
            "success": True,
            "data": df.to_dict(orient="records"),
            "columns": df.columns.tolist(),
            "row_count": len(df),
        }
    except Exception as e:
        error_text = str(e)
        if is_auth_error(error_text):
            _global_connection = None
            return auth_required_result(error_text)
        # drop the cached connection on transport-level failures (dead socket,
        # timeout, EOF) so the next call reconnects instead of reusing a broken one
        if is_conn_error(error_text):
            _global_connection = None
        return {
            "success": False,
            "error": error_text,
            "data": [],
            "columns": [],
            "row_count": 0,
        }


# Создаём MCP сервер
server = Server("trino-mcp")


@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Список доступных инструментов"""
    return [
        types.Tool(
            name="execute_query",
            description=(
                "Execute SQL query on Trino. If the result contains "
                "TRINO_AUTH_REQUIRED, do NOT retry blindly: follow action_required — "
                "stop, tell the user to finish the browser sign-in, wait for their "
                "confirmation, then retry once."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "SQL query to execute",
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="list_catalogs",
            description="List all available Trino catalogs",
            inputSchema={"type": "object", "properties": {}},
        ),
        types.Tool(
            name="list_schemas",
            description="List schemas in a catalog",
            inputSchema={
                "type": "object",
                "properties": {
                    "catalog": {
                        "type": "string",
                        "description": "Catalog name",
                        "default": "dwh-iceberg",
                    }
                },
            },
        ),
        types.Tool(
            name="list_tables",
            description="List tables in a schema",
            inputSchema={
                "type": "object",
                "properties": {
                    "catalog": {"type": "string", "description": "Catalog name"},
                    "schema": {"type": "string", "description": "Schema name"},
                },
                "required": ["catalog", "schema"],
            },
        ),
        types.Tool(
            name="describe_table",
            description="Describe table structure",
            inputSchema={
                "type": "object",
                "properties": {
                    "catalog": {"type": "string", "description": "Catalog name"},
                    "schema": {"type": "string", "description": "Schema name"},
                    "table": {"type": "string", "description": "Table name"},
                },
                "required": ["catalog", "schema", "table"],
            },
        ),
    ]


@server.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    """Обработка вызовов инструментов"""

    if arguments is None:
        arguments = {}

    if name == "execute_query":
        sql = arguments.get("query")
    elif name == "list_catalogs":
        sql = "SHOW CATALOGS"
    elif name == "list_schemas":
        sql = f"SHOW SCHEMAS FROM {arguments.get('catalog', 'dwh-iceberg')}"
    elif name == "list_tables":
        sql = f"SHOW TABLES FROM {arguments.get('catalog')}.{arguments.get('schema')}"
    elif name == "describe_table":
        sql = (f"DESCRIBE {arguments.get('catalog')}."
               f"{arguments.get('schema')}.{arguments.get('table')}")
    else:
        sql = None
        result = {"success": False, "error": f"Unknown tool: {name}"}

    # execute_query is blocking (sync trino client + pandas); run it off the event
    # loop so a slow/stuck query can NEVER freeze the whole MCP server (one hung
    # call used to block asyncio and turn every subsequent call into -32001)
    if sql is not None:
        result = await asyncio.to_thread(execute_query, sql)

    # default=str: Trino DATE/TIME/DECIMAL/UUID land in pandas as dtype=object, so the
    # dtype sweep in execute_query misses them and json.dumps dies on the first such row
    # ("Object of type date is not JSON serializable"), losing an otherwise good result.
    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, indent=2, ensure_ascii=False, default=str),
        )
    ]


async def main():
    """Запуск MCP сервера"""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="trino-mcp",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
