"""Граница «наша сессия / чужая» (lib/hook_scope.py).

Почему это вообще проверяется тестом: хуки Codex живут в общем на все
проекты `$CODEX_HOME/hooks.json`, поэтому наши скрипты запускаются и в чужих
сессиях аналитика. Раньше «не наша сессия» выражалась относительным путём в
команде хука — и живой запуск показал, что это не тишина, а
`UserPromptSubmit Blocked`, то есть промпт не доходит до модели вообще
(docs/codex-facts.md, раздел 11).
"""
import os
import shutil
import subprocess
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
    """Клон-двойник: тот же скрипт хука на своём месте внутри временного
    каталога. Проверять «внутри клона» на настоящем репозитории нельзя —
    хук делает `git pull`, то есть тест лез бы в сеть и в рабочее дерево
    разработчика."""
    root = tmp_path / "clone"
    (root / ".claude" / "hooks").mkdir(parents=True)
    shutil.copy2(HOOKS_DIR / "on_session_start.sh",
                 root / ".claude" / "hooks" / "on_session_start.sh")
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


def test_guard_is_a_noop_for_claude_code():
    """У Claude Code хуки регистрируются в .claude/settings.json своего
    проекта, поэтому рабочий каталог хука — всегда корень проекта (проверено
    живым запуском, docs/codex-facts.md, раздел 11: getcwd == payload["cwd"]
    == CLAUDE_PROJECT_DIR). Проверка обязана быть для него безвредной.
    """
    assert hook_scope.session_is_ours(str(REPO_ROOT), str(REPO_ROOT)) is True
    assert hook_scope.session_is_ours(os.path.join(str(REPO_ROOT), "lib"),
                                      str(REPO_ROOT)) is True
