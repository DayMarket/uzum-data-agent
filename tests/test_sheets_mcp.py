import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "sheets_mcp", REPO_ROOT / "connectors" / "sheets_mcp.py"
)
sheets_mcp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sheets_mcp)


def test_rejects_write_outside_allowed_folder(monkeypatch):
    monkeypatch.setattr(sheets_mcp, "_parent_folder", lambda sid: "other-folder")
    with pytest.raises(sheets_mcp.OutsidePerimeter):
        sheets_mcp.check_perimeter("sheet-1", allowed_folder="our-folder")


def test_allows_write_inside_allowed_folder(monkeypatch):
    monkeypatch.setattr(sheets_mcp, "_parent_folder", lambda sid: "our-folder")
    assert sheets_mcp.check_perimeter("sheet-1", allowed_folder="our-folder") is True


def test_refuses_when_folder_not_configured():
    with pytest.raises(sheets_mcp.OutsidePerimeter):
        sheets_mcp.check_perimeter("sheet-1", allowed_folder="")


def test_rows_to_values_handles_none():
    assert sheets_mcp.rows_to_values([["a", None, 1]]) == [["a", "", "1"]]


def test_create_sheet_refuses_when_folder_not_configured(monkeypatch):
    """Регрессия: create_sheet раньше игнорировал периметр целиком.

    Пустая/незаданная GOOGLE_SHEETS_FOLDER_ID должна останавливать всё до
    первого обращения к API — включая запрос токена. Если защиту убрать,
    handle() дойдёт до _api, тот кинет AssertionError вместо ожидаемого
    OutsidePerimeter, и pytest.raises ниже не совпадёт — тест упадёт.
    """
    monkeypatch.delenv("GOOGLE_SHEETS_FOLDER_ID", raising=False)
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/dev/null")

    calls = []

    def _fake_access_token(sa_path):
        calls.append("access_token")
        return "fake-token"

    def _fake_api(token, url, method="GET", payload=None):
        calls.append((method, url))
        raise AssertionError(
            "создание/запись таблицы не должно вызываться без настроенной папки"
        )

    monkeypatch.setattr(sheets_mcp, "_access_token", _fake_access_token)
    monkeypatch.setattr(sheets_mcp, "_api", _fake_api)

    with pytest.raises(sheets_mcp.OutsidePerimeter):
        sheets_mcp.handle(
            "create_sheet", {"title": "Тестовая таблица", "rows": [["a", "b"]]}
        )

    assert calls == [], "не должно быть ни запроса токена, ни обращений к API"


def test_create_sheet_does_not_write_rows_when_move_verification_fails(monkeypatch):
    """Регрессия: перенос в папку не должен считаться успешным «на слово».

    Папка настроена, создание и addParents проходят, но проверка фактического
    родителя (тем же _parent_folder, что и у append_rows) говорит, что
    таблица осталась вне периметра. Строки не должны попасть в API вообще —
    если проверку после переноса убрать, вызов values/A1:append произойдёт и
    упадёт с AssertionError вместо OutsidePerimeter, тест не пройдёт.
    """
    monkeypatch.setenv("GOOGLE_SHEETS_FOLDER_ID", "our-folder")
    monkeypatch.setenv("GOOGLE_SERVICE_ACCOUNT_FILE", "/dev/null")
    monkeypatch.setattr(sheets_mcp, "_access_token", lambda sa_path: "fake-token")
    monkeypatch.setattr(sheets_mcp, "_parent_folder", lambda sid: "other-folder")

    api_calls = []

    def _fake_api(token, url, method="GET", payload=None):
        api_calls.append((method, url))
        if method == "POST" and url == "https://sheets.googleapis.com/v4/spreadsheets":
            return {"spreadsheetId": "sheet-1"}
        if method == "PATCH" and "addParents" in url:
            return {}
        if "values/A1:append" in url:
            raise AssertionError(
                "строки не должны писаться без подтверждённого периметра"
            )
        if method == "PATCH" and url.endswith("/files/sheet-1"):
            return {}  # уборка (trash) созданной вне периметра таблицы
        raise AssertionError("неожиданный вызов API: %s %s" % (method, url))

    monkeypatch.setattr(sheets_mcp, "_api", _fake_api)

    with pytest.raises(sheets_mcp.OutsidePerimeter):
        sheets_mcp.handle("create_sheet", {"title": "Т", "rows": [["a"]]})

    assert not any("values/A1:append" in url for _, url in api_calls)


def test_access_token_reuses_cached_credentials(monkeypatch):
    """append_rows раньше запрашивал токен дважды за один вызов (handle() +
    check_perimeter → _parent_folder). Credentials должны кэшироваться и
    обновляться только когда истекли."""
    monkeypatch.setattr(sheets_mcp, "_credentials", None)

    calls = {"create": 0, "refresh": 0}

    class FakeCredentials:
        def __init__(self):
            self.token = None

        @property
        def valid(self):
            return self.token is not None

        def refresh(self, request):
            calls["refresh"] += 1
            self.token = "token-%d" % calls["refresh"]

    fake = FakeCredentials()

    def _from_file(path, scopes=None):
        calls["create"] += 1
        return fake

    monkeypatch.setattr(
        sheets_mcp.service_account.Credentials,
        "from_service_account_file",
        staticmethod(_from_file),
    )

    first = sheets_mcp._access_token("dummy.json")
    second = sheets_mcp._access_token("dummy.json")

    assert first == second == "token-1"
    assert calls == {"create": 1, "refresh": 1}
