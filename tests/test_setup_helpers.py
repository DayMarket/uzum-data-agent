import json

import setup_helpers


def test_writes_env_file_with_permissions(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis-platon"})
    assert path.read_text(encoding="utf-8").strip() == "CH_USER=denis-platon"
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_merges_without_losing_existing_keys(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "denis"})
    setup_helpers.write_env(str(path), {"JIRA_TOKEN": "test-token-xxx"})
    content = path.read_text(encoding="utf-8")
    assert "CH_USER=denis" in content
    assert "JIRA_TOKEN=test-token-xxx" in content


def test_overwrites_existing_key(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_USER": "old"})
    setup_helpers.write_env(str(path), {"CH_USER": "new"})
    assert "CH_USER=new" in path.read_text(encoding="utf-8")
    assert "CH_USER=old" not in path.read_text(encoding="utf-8")


def test_enables_only_configured_servers(tmp_path):
    path = tmp_path / "settings.local.json"
    setup_helpers.write_enabled_servers(str(path), ["clickhouse", "atlassian"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["enabledMcpjsonServers"] == ["atlassian", "clickhouse"]


def test_keeps_other_local_settings(tmp_path):
    path = tmp_path / "settings.local.json"
    path.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]}}), encoding="utf-8")
    setup_helpers.write_enabled_servers(str(path), ["trino"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["permissions"]["allow"] == ["Bash(ls)"]
    assert data["enabledMcpjsonServers"] == ["trino"]
