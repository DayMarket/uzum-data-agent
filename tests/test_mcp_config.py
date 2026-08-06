import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
EXPECTED = {"atlassian", "clickhouse", "trino", "superset", "grafana",
            "openmetadata", "growthbook", "sheets"}


def test_all_eight_servers_present():
    assert set(CONFIG["mcpServers"]) == EXPECTED


def test_no_literal_secrets_in_config():
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for pattern in (r"sk-ant-", r"ghp_", r"Bearer [A-Za-z0-9]{12,}", r"password\"\s*:\s*\"[^$]"):
        assert not re.search(pattern, raw), pattern


def test_every_credential_uses_variable():
    raw = (REPO_ROOT / ".mcp.json").read_text(encoding="utf-8")
    for var in ("CH_HOST", "CH_USER", "CH_PASSWORD", "JIRA_URL", "JIRA_TOKEN"):
        assert "${%s}" % var in raw


def test_local_scripts_exist():
    # sheets_mcp.py делается в задаче 8, следующей за этой — не проверяем его здесь.
    for name in ("trino_proxy.py", "superset_mcp.py"):
        assert (REPO_ROOT / "connectors" / name).exists()
