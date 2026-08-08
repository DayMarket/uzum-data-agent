"""Хуки в ЧУЖОМ проекте: ноль, мгновенно, молча и без следов.

Почему это отдельный файл с тремя почти одинаковыми тестами. Хуки Codex
регистрируются в `$CODEX_HOME/hooks.json`, а этот файл — один на весь
`$CODEX_HOME`, то есть на ВСЕ проекты, которые аналитик когда-либо откроет в
Codex. Наши скрипты будут запускаться в каждой его сессии, и «это не наша
сессия» они обязаны решать сами.

Раньше эту роль пытался играть относительный путь в команде хука
(`python3 .claude/hooks/log_event.py`) с расчётом на то, что в чужом проекте
файла по такому пути не найдётся и хук «тихо не сработает». Живой запуск
опроверг (docs/codex-facts.md, раздел 11): отсутствие файла — это не тишина,
а ненулевой код возврата, и Codex читает его как отказ хука:

    hook: SessionStart Failed
    hook: UserPromptSubmit Blocked

`Blocked` означает, что промпт не доходит до модели вообще. Установка нашего
инструмента ломала аналитику Codex во всех остальных его проектах.

Проверяем не функцию, а то, что реально запускает движок: скрипт целиком,
отдельным процессом, с рабочим каталогом чужого проекта.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"

A_CODEX_PAYLOAD = (
    '{"hook_event_name":"%s","session_id":"s","cwd":"/elsewhere",'
    '"transcript_path":"/x/sessions/2026/08/08/rollout-2026-08-08T00-00-00-s.jsonl"}'
)


def _telemetry_env(state_dir):
    """Окружение, в котором телеметрия ВКЛЮЧЕНА, но уходит не в сеть, а в
    локальную очередь: 127.0.0.1:1 — гарантированный отказ соединения, после
    которого lib/telemetry.write() кладёт строку файлом в очередь. Так
    «хук ничего не записал» становится проверяемым фактом, а не догадкой по
    пустому stdout: если бы граница не работала, в очереди лежала бы строка
    чужой сессии."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(state_dir),
        "UZUM_STATE_DIR": str(state_dir),
        "TELEMETRY_CH_HOST": "127.0.0.1",
        "TELEMETRY_CH_PORT": "1",
    }


def _queued_rows(state_dir):
    queue = Path(state_dir) / "queue"
    if not queue.exists():
        return []
    return sorted(p.name for p in queue.iterdir())


def _run_in_foreign_dir(tmp_path, command, payload):
    foreign = tmp_path / "some-other-project"
    foreign.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    result = subprocess.run(command, cwd=str(foreign), input=payload,
                            env=_telemetry_env(state), capture_output=True,
                            text=True, timeout=60)
    return result, state, foreign


def test_log_event_writes_nothing_in_a_foreign_project(tmp_path):
    result, state, _ = _run_in_foreign_dir(
        tmp_path, [sys.executable, str(HOOKS_DIR / "log_event.py")],
        A_CODEX_PAYLOAD % "UserPromptSubmit")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert _queued_rows(state) == [], "чужая сессия попала в нашу телеметрию"


def test_log_session_writes_nothing_in_a_foreign_project(tmp_path):
    result, state, _ = _run_in_foreign_dir(
        tmp_path, [sys.executable, str(HOOKS_DIR / "log_session.py")],
        A_CODEX_PAYLOAD % "SessionEnd")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    assert _queued_rows(state) == [], "чужая сессия попала в нашу телеметрию"


def test_session_start_does_not_touch_a_foreign_repository(tmp_path):
    """У обновлятора ставка выше телеметрии: без границы он делал бы
    `git pull` в чужом репозитории аналитика и печатал бы в чужую сессию
    отчёт об этом."""
    foreign = tmp_path / "some-other-project"
    foreign.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(foreign), check=True)
    (foreign / "f.txt").write_text("x", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(HOOKS_DIR / "on_session_start.sh"), "--plain"],
        cwd=str(foreign), input='{"hook_event_name":"SessionStart"}',
        capture_output=True, text=True, timeout=60)

    assert result.returncode == 0
    assert result.stdout == "", "хук отчитался о работе в чужом репозитории"
    assert result.stderr == ""
