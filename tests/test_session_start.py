import json
import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / ".claude" / "hooks" / "on_session_start.sh"


def _run(cwd, *args):
    """Запустить хук так, как его запускает движок: скрипт на своём месте
    внутри клона, рабочий каталог — каталог сессии.

    Копия скрипта кладётся в `<cwd>/.claude/hooks/` не для удобства: хук
    сравнивает каталог сессии с корнем ТОГО клона, которому принадлежит сам
    (lib/hook_scope.py — в общем на все проекты $CODEX_HOME/hooks.json он
    зарегистрирован на каждую сессию Codex, и в чужой обязан молчать).
    Запуск скрипта настоящего репозитория с рабочим каталогом во временной
    папке — это ровно "чужая сессия", и хук честно ничего бы не сделал.
    """
    cwd = Path(cwd)
    hook = cwd / ".claude" / "hooks" / "on_session_start.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(HOOK, hook)
    return subprocess.run(
        ["bash", str(hook), *args],
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


def test_plain_mode_prints_text_not_claude_json(tmp_path):
    """Режим для Codex: тот же скрипт, тот же git pull, но вывод — обычный
    текст. hookSpecificOutput у Codex не описан и живым запуском не
    подтверждён; обычный текст — подтверждён (docs/codex-facts.md, раздел 9:
    Codex отдал его модели дословно)."""
    result = _run(tmp_path, "--plain")
    assert result.returncode == 0
    assert result.stdout.strip()
    assert "hookSpecificOutput" not in result.stdout
    assert not result.stdout.lstrip().startswith("{")


def test_plain_mode_pulls_the_repository_the_same_way(tmp_path):
    """Главное, ради чего хук вообще нужен: обновление не должно зависеть от
    формата вывода. Клон отстаёт от апстрима на один коммит — после хука
    должен догнать."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=upstream, check=True)
    (upstream / "f.txt").write_text("one")
    subprocess.run([*git, "add", "."], cwd=upstream, check=True)
    subprocess.run([*git, "commit", "-qm", "one"], cwd=upstream, check=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(upstream), str(clone)], check=True)
    before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone,
                            capture_output=True, text=True).stdout.strip()

    (upstream / "f.txt").write_text("two")
    subprocess.run([*git, "commit", "-qam", "two"], cwd=upstream, check=True)

    assert _run(clone, "--plain").returncode == 0
    after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone,
                           capture_output=True, text=True).stdout.strip()
    assert after != before, "хук не подтянул коммит из апстрима"
    assert (clone / "f.txt").read_text() == "two"


def test_settings_registers_all_hooks():
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text())
    events = settings["hooks"].keys()
    for event in ("SessionStart", "SessionEnd", "UserPromptSubmit",
                  "PostToolUse", "PostToolUseFailure"):
        assert event in events
