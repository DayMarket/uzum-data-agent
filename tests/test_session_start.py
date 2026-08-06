import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "on_session_start.sh"


def _run(cwd):
    return subprocess.run(
        ["bash", str(HOOK)],
        input='{"hook_event_name":"SessionStart","session_id":"s"}',
        capture_output=True, text=True, cwd=str(cwd), timeout=30,
    )


def test_exits_zero_outside_git_repo(tmp_path):
    assert _run(tmp_path).returncode == 0


def test_reports_conflict_instead_of_failing(tmp_path):
    # репозиторий без upstream — pull не сработает, но хук обязан выйти нулём
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=tmp_path, check=True)
    result = _run(tmp_path)
    assert result.returncode == 0


def test_outputs_valid_json_with_additional_context(tmp_path):
    result = _run(tmp_path)
    payload = json.loads(result.stdout)
    assert "hookSpecificOutput" in payload
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"


def test_settings_registers_all_hooks():
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    events = settings["hooks"].keys()
    for event in ("SessionStart", "SessionEnd", "UserPromptSubmit",
                  "PostToolUse", "PostToolUseFailure"):
        assert event in events
