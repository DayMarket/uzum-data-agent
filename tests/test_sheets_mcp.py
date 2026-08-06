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
