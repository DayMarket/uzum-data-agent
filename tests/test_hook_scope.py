"""Граница «наша сессия / чужая» (lib/hook_scope.py).

Почему это вообще проверяется тестом: хуки Codex живут в общем на все
проекты `$CODEX_HOME/hooks.json`, поэтому наши скрипты запускаются и в чужих
сессиях аналитика. Раньше «не наша сессия» выражалась относительным путём в
команде хука — и живой запуск показал, что это не тишина, а
`UserPromptSubmit Blocked`, то есть промпт не доходит до модели вообще
(docs/codex-facts.md, раздел 11).
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import hook_scope

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"


def test_repo_root_is_the_clone_this_file_belongs_to():
    """Корень определяется по расположению самого модуля, а не по текущему
    каталогу и не по переменной окружения: в hooks.json прописан абсолютный
    путь к скриптам конкретного клона, и сравнивать надо с корнем того же
    клона."""
    assert hook_scope.repo_root() == str(REPO_ROOT.resolve())


def test_session_inside_the_clone_is_ours(tmp_path):
    root = tmp_path / "clone"
    (root / "work" / "OE-1").mkdir(parents=True)
    # Сам корень — наша сессия.
    assert hook_scope.session_is_ours(str(root), str(root)) is True
    # Подкаталог тоже: аналитик может запустить движок из work/<ключ>.
    assert hook_scope.session_is_ours(str(root / "work" / "OE-1"), str(root)) is True


def test_session_in_another_project_is_not_ours(tmp_path):
    root = tmp_path / "clone"
    root.mkdir()
    foreign = tmp_path / "some-other-project"
    foreign.mkdir()
    assert hook_scope.session_is_ours(str(foreign), str(root)) is False
    # Каталог, который лишь начинается так же, — не наш.
    sibling = tmp_path / "clone-old"
    sibling.mkdir()
    assert hook_scope.session_is_ours(str(sibling), str(root)) is False
    # Родитель нашего корня — тоже чужая сессия, а не наша.
    assert hook_scope.session_is_ours(str(tmp_path), str(root)) is False


def test_symlinked_paths_still_match(tmp_path):
    """На macOS /tmp — симлинк на /private/tmp. Без нормализации обеих
    сторон один и тот же каталог не совпал бы сам с собой, и хук молчал бы
    в нашем же репозитории."""
    root = tmp_path / "clone"
    root.mkdir()
    link = tmp_path / "link-to-clone"
    link.symlink_to(root)
    assert hook_scope.session_is_ours(str(link), str(root)) is True
    assert hook_scope.session_is_ours(str(root), str(link)) is True


# ── Обратная сторона: в своём клоне граница обязана пропускать ────────────
#
# Проверяем не функцию, а то, что запустит движок: скрипт целиком, как
# отдельный процесс. Зеркало этих тестов — tests/test_hooks_outside_the_repo.py,
# где те же скрипты запускаются в чужом каталоге и обязаны молчать.

def _run_hook(command, cwd, payload):
    return subprocess.run(command, cwd=str(cwd), input=payload,
                          capture_output=True, text=True, timeout=60)


def _fake_clone(tmp_path):
    """Клон-двойник: те же скрипты хуков и та же lib на своих местах внутри
    временного каталога. Проверять «внутри клона» на настоящем репозитории
    нельзя — SessionStart-хук делает `git pull`, то есть тест лез бы в сеть и
    в рабочее дерево разработчика."""
    root = tmp_path / "clone"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "lib").mkdir()
    for script in HOOKS_DIR.glob("*"):
        if script.is_file():
            shutil.copy2(script, root / ".claude" / "hooks" / script.name)
    for module in (REPO_ROOT / "lib").glob("*.py"):
        shutil.copy2(module, root / "lib" / module.name)
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True)
    return root


def test_hooks_still_work_inside_the_clone(tmp_path):
    """Обратная сторона: в своём клоне проверка обязана пропускать —
    иначе, починив Codex в чужих проектах, мы сломали бы его в своём."""
    root = _fake_clone(tmp_path)
    result = _run_hook(
        ["bash", str(root / ".claude" / "hooks" / "on_session_start.sh"), "--plain"],
        root, "{}")
    assert result.returncode == 0
    assert result.stdout.strip() != ""


def test_hooks_work_from_a_subdirectory_of_the_clone(tmp_path):
    """Запуск не из корня, а из рабочей папки задачи — та же сессия."""
    root = _fake_clone(tmp_path)
    subdir = root / "work" / "OE-1"
    subdir.mkdir(parents=True)
    result = _run_hook(
        ["bash", str(root / ".claude" / "hooks" / "on_session_start.sh"), "--plain"],
        subdir, "{}")
    assert result.returncode == 0
    assert result.stdout.strip() != ""


# ── Положительная сторона у python-хуков: строка ДОЛЖНА записаться ────────
#
# Находка повторного ревью: у on_session_start.sh было и «молчит в чужом», и
# «работает в своём», а у log_event.py/log_session.py — только первое. Мутант
# «граница режет всё» ронял 4 теста из 294 и ни одного в test_log_event.py,
# то есть телеметрию можно было выключить целиком, не уронив её тесты.
# Проверяем сквозь весь скрипт, с настоящей записью в очередь.

A_CLAUDE_TRANSCRIPT = "/Users/x/.claude/projects/slug/s.jsonl"
A_CODEX_TRANSCRIPT = "/x/sessions/2026/08/08/rollout-2026-08-08T00-00-00-s.jsonl"


def _payload(event, transcript, **extra):
    data = {"hook_event_name": event, "session_id": "s",
            "transcript_path": transcript}
    data.update(extra)
    return json.dumps(data)


def _run_hook_in_clone(root, script, payload, telemetry_queue, cwd=None):
    return subprocess.run(
        [sys.executable, str(root / ".claude" / "hooks" / script)],
        cwd=str(cwd or root), input=payload, env=telemetry_queue.env,
        capture_output=True, text=True, timeout=60)


def test_log_event_records_a_step_of_our_own_session(tmp_path, telemetry_queue):
    root = _fake_clone(tmp_path)
    result = _run_hook_in_clone(
        root, "log_event.py",
        _payload("PostToolUse", A_CODEX_TRANSCRIPT, tool_name="Bash"),
        telemetry_queue)

    assert result.returncode == 0, result.stderr
    tables = [table for table, _ in telemetry_queue.rows()]
    assert tables == ["ai_usage_events"], telemetry_queue.rows()
    row = telemetry_queue.rows()[0][1]
    assert row["tool_name"] == "Bash"
    assert row["engine"] == "codex"


def test_log_session_records_the_session_row_of_our_own_session(tmp_path, telemetry_queue):
    root = _fake_clone(tmp_path)
    transcript = tmp_path / "rollout-2026-08-08T00-00-00-s.jsonl"
    transcript.write_text("", encoding="utf-8")
    result = _run_hook_in_clone(
        root, "log_session.py",
        _payload("SessionEnd", str(transcript)), telemetry_queue)

    assert result.returncode == 0, result.stderr
    tables = [table for table, _ in telemetry_queue.rows()]
    assert tables == ["ai_usage_sessions"], telemetry_queue.rows()


def test_guard_lets_claude_code_through(tmp_path, telemetry_queue):
    """У Claude Code хуки регистрируются в .claude/settings.json своего
    проекта, поэтому рабочий каталог хука — всегда корень проекта (проверено
    живым запуском, docs/codex-facts.md, раздел 11: getcwd == payload["cwd"]
    == CLAUDE_PROJECT_DIR). Граница обязана быть для него безвредной — и это
    проверяется сквозным прогоном с payload'ом Claude Code, а не сравнением
    корня с самим собой."""
    root = _fake_clone(tmp_path)
    result = _run_hook_in_clone(
        root, "log_event.py",
        _payload("PostToolUse", A_CLAUDE_TRANSCRIPT, tool_name="Bash",
                 duration_ms=51),
        telemetry_queue, cwd=root / "lib")

    assert result.returncode == 0, result.stderr
    rows = telemetry_queue.rows()
    assert [table for table, _ in rows] == ["ai_usage_events"], rows
    assert rows[0][1]["engine"] == "claude"
    assert rows[0][1]["duration_ms"] == 51


# ── Находка повторного ревью: рабочего каталога может не быть на диске ────
#
# Аналитик сидит в work/OE-1234, каталог исчезает (переключение ветки,
# `git clean`, `rm -rf` в соседнем окне). os.getcwd() кидает FileNotFoundError,
# и до этой правки хук отвечал кодом 1 с трейсбеком — то есть тем же
# `UserPromptSubmit Blocked`, ради которого всё и делалось, только с другого
# входа. Обещание из заголовка обоих скриптов — «всегда завершается с кодом
# 0» — обязано держаться и здесь.

def _run_from_a_vanished_directory(command, tmp_path, payload):
    """Запустить команду с рабочим каталогом, которого уже нет.

    Каталог удаляется изнутри самого процесса, после того как он в нём
    оказался: снаружи так не сделать — subprocess требует существующий cwd на
    момент запуска.
    """
    doomed = tmp_path / "work" / "OE-1234"
    doomed.mkdir(parents=True)
    script = " ".join(['rm -rf "$1";', 'shift;', 'exec "$@"'])
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT / "lib"))
    return subprocess.run(
        ["bash", "-c", script, "_", str(doomed), *command],
        cwd=str(doomed), input=payload, env=env, capture_output=True,
        text=True, timeout=60)


def test_session_is_ours_answers_instead_of_raising_when_the_directory_is_gone(tmp_path):
    """Защита заявлена с ДВУХ сторон, значит и проверять надо обе.

    Три теста ниже держатся на `try/except` вокруг вызова и остаются зелёными,
    даже если сама функция снова начнёт бросать исключение (проверено
    мутацией). Здесь проверяется вторая сторона отдельно: функция, вызванная
    БЕЗ аргументов — то есть так, как её зовут хуки, — обязана вернуть False,
    а не уронить процесс. Ни моков, ни monkeypatch: настоящий процесс с
    настоящим исчезнувшим рабочим каталогом."""
    result = _run_from_a_vanished_directory(
        [sys.executable, "-c",
         "import hook_scope; print(repr(hook_scope.session_is_ours()))"],
        tmp_path, "")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "False", result.stdout + result.stderr
    assert result.stderr == ""


def test_log_event_survives_a_vanished_working_directory(tmp_path):
    result = _run_from_a_vanished_directory(
        [sys.executable, str(HOOKS_DIR / "log_event.py")], tmp_path,
        _payload("UserPromptSubmit", A_CODEX_TRANSCRIPT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_log_session_survives_a_vanished_working_directory(tmp_path):
    result = _run_from_a_vanished_directory(
        [sys.executable, str(HOOKS_DIR / "log_session.py")], tmp_path,
        _payload("SessionStart", A_CODEX_TRANSCRIPT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_session_start_survives_a_vanished_working_directory(tmp_path):
    """Bash-реализация той же границы обязана деградировать так же: код 0 и
    ни своей строчки в выводе.

    Единственное, что остаётся в stderr, — приветственное `shell-init: error
    retrieving current directory` самого bash: оно печатается при старте
    интерпретатора, до первой строки скрипта, и повлиять на него скрипт не
    может. Проверяем то, что в нашей власти: ни `pwd:`, ни вывода git."""
    result = _run_from_a_vanished_directory(
        ["bash", str(HOOKS_DIR / "on_session_start.sh"), "--plain"], tmp_path,
        _payload("SessionStart", A_CODEX_TRANSCRIPT))
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert "pwd:" not in result.stderr, result.stderr
    assert "git" not in result.stderr, result.stderr
