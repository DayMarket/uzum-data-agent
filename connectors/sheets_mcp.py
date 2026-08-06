#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "google-auth>=2.28.0",
#     "requests>=2.31.0",
# ]
# ///
"""MCP-сервер для записи в Google Sheets под сервисным аккаунтом.

Пишет только в таблицы внутри разрешённой папки: иначе агент получает право
писать в любую таблицу, к которой у сервисного аккаунта есть доступ (см.
check_perimeter).

Аутентификация — через google-auth (google.oauth2.service_account), которая
сама подписывает JWT сервисного аккаунта (RS256, через pure-python пакет rsa —
никакого openssl/subprocess) и обменивает его на access token. Раньше это
делалось вручную через subprocess+openssl, потому что остальные коннекторы
репозитория держались на стандартной библиотеке; после задачи 7 это правило
смягчили — коннекторы (в отличие от хуков) можно ставить зависимостями, если
они запускаются через `uv run` (см. PEP 723 заголовок выше и ACCESS.md). Тот же
приём, что и в connectors/trino_proxy.py и connectors/superset_mcp.py.

Переменные окружения:
  GOOGLE_SERVICE_ACCOUNT_FILE  путь к JSON-ключу сервисного аккаунта (не в git)
  GOOGLE_SHEETS_FOLDER_ID      ID папки Google Drive, внутри которой разрешена
                                запись; если не задана — запись запрещена
"""
from __future__ import annotations

import json
import os
import sys

import requests
from google.auth.transport.requests import Request
from google.oauth2 import service_account

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


class OutsidePerimeter(Exception):
    """Попытка записи вне разрешённой папки."""


_credentials = None


def _access_token(sa_path):
    """Получить access token сервисного аккаунта через google-auth.

    Объект Credentials кэшируется на модуль: google-auth сам знает, истёк ли
    токен (credentials.valid), и обновляет его только когда нужно. Это не
    просто оптимизация — без кэша один вызов append_rows дважды ходил на
    oauth2.googleapis.com/token (один раз в handle(), второй раз внутри
    check_perimeter → _parent_folder).
    """
    global _credentials
    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(
            sa_path, scopes=SCOPES
        )
    if not _credentials.valid:
        _credentials.refresh(Request())
    return _credentials.token


def _api(token, url, method="GET", payload=None):
    resp = requests.request(
        method,
        url,
        json=payload,
        headers={"Authorization": "Bearer " + token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _parent_folder(spreadsheet_id):
    token = _access_token(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    meta = _api(
        token,
        "https://www.googleapis.com/drive/v3/files/%s?fields=parents"
        % spreadsheet_id,
    )
    parents = meta.get("parents") or []
    return parents[0] if parents else ""


def check_perimeter(spreadsheet_id, allowed_folder):
    """Разрешить запись только внутри заданной папки."""
    if not allowed_folder:
        raise OutsidePerimeter(
            "Папка для записи не настроена: задай GOOGLE_SHEETS_FOLDER_ID"
        )
    if _parent_folder(spreadsheet_id) != allowed_folder:
        raise OutsidePerimeter(
            "Таблица лежит вне разрешённой папки — запись запрещена"
        )
    return True


def rows_to_values(rows):
    """Привести строки к строковым значениям, None → пустая строка."""
    return [["" if cell is None else str(cell) for cell in row] for row in rows]


def _try_trash(token, file_id):
    """Убрать таблицу, которая после переноса оказалась вне периметра.

    Лучшее из возможного, не гарантия: если сервисному аккаунту не хватит
    прав на удаление, глотаем ошибку — молчаливая неудача здесь безопаснее,
    чем маскировка исходного OutsidePerimeter исключением из cleanup-кода.
    """
    try:
        _api(
            token,
            "https://www.googleapis.com/drive/v3/files/%s" % file_id,
            "PATCH",
            {"trashed": True},
        )
    except Exception:
        pass


def handle(method, params):
    folder = os.environ.get("GOOGLE_SHEETS_FOLDER_ID", "")
    # Периметр проверяем до любого обращения к API — включая запрос токена:
    # пустая/незаданная папка обязана останавливать создание таблицы, а не
    # проходить как «папка не указана, но и не запрещена».
    if method == "create_sheet" and not folder:
        raise OutsidePerimeter(
            "Папка для записи не настроена: задай GOOGLE_SHEETS_FOLDER_ID"
        )
    token = _access_token(os.environ["GOOGLE_SERVICE_ACCOUNT_FILE"])
    if method == "create_sheet":
        created = _api(
            token,
            "https://sheets.googleapis.com/v4/spreadsheets",
            "POST",
            {"properties": {"title": params["title"]}},
        )
        sid = created["spreadsheetId"]
        _api(
            token,
            "https://www.googleapis.com/drive/v3/files/%s?addParents=%s"
            % (sid, folder),
            "PATCH",
            {},
        )
        # Не надеемся, что перенос сработал — перепроверяем фактического
        # родителя тем же способом, что и append_rows. Строки пишем только
        # после подтверждения; если таблица всё же не в папке — не пишем
        # ничего и убираем её, а не оставляем висеть вне периметра.
        try:
            check_perimeter(sid, folder)
        except OutsidePerimeter:
            _try_trash(token, sid)
            raise
        if params.get("rows"):
            _api(
                token,
                "https://sheets.googleapis.com/v4/spreadsheets/%s/values/A1:append"
                "?valueInputOption=RAW" % sid,
                "POST",
                {"values": rows_to_values(params["rows"])},
            )
        return {
            "spreadsheet_id": sid,
            "url": "https://docs.google.com/spreadsheets/d/%s" % sid,
        }
    if method == "append_rows":
        sid = params["spreadsheet_id"]
        check_perimeter(sid, folder)
        _api(
            token,
            "https://sheets.googleapis.com/v4/spreadsheets/%s/values/A1:append"
            "?valueInputOption=RAW" % sid,
            "POST",
            {"values": rows_to_values(params["rows"])},
        )
        return {"ok": True}
    raise ValueError("неизвестный метод: %s" % method)


TOOLS = [
    {
        "name": "create_sheet",
        "description": "Создать таблицу в рабочей папке и записать строки",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "rows": {"type": "array"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "append_rows",
        "description": "Дописать строки в существующую таблицу рабочей папки",
        "inputSchema": {
            "type": "object",
            "properties": {
                "spreadsheet_id": {"type": "string"},
                "rows": {"type": "array"},
            },
            "required": ["spreadsheet_id", "rows"],
        },
    },
]


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method = req.get("id"), req.get("method")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "sheets", "version": "1.0.0"},
                }
            elif method == "notifications/initialized":
                continue
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                out = handle(req["params"]["name"], req["params"].get("arguments", {}))
                result = {
                    "content": [
                        {"type": "text", "text": json.dumps(out, ensure_ascii=False)}
                    ]
                }
            else:
                result = {}
            response = {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32000, "message": str(exc)},
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
