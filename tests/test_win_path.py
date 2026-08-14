"""Тесты на lib/win_path.sh — правило «команда есть И запускается здесь».

Правило появилось после живого отказа на машине аналитика: мастер прошёл до
конца и напечатал «Запускай: uzum», а `uzum --codex` умер с
`exec: node: not found`. Причина — в WSL переменная PATH содержит и
Windows-часть, npm на Windows кладёт рядом с codex.cmd ещё и `codex` без
расширения, на диске Windows он помечен исполняемым, и `command -v` отвечает
«есть». Установщик решил, что движок уже стоит, и пропустил установку.

Файл подключают все три точки входа (setup.sh, install-windows.sh,
bin/uzum), поэтому проверяется он один раз и напрямую — а не трижды через
поведение каждого скрипта.
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WIN_PATH_SH = REPO_ROOT / "lib" / "win_path.sh"


def _is_windows_path(path):
    """Ровно то решение, которое принимает правило, — без обвязки."""
    completed = subprocess.run(
        ["bash", "-c",
         '. "$1"; if is_windows_path "$2"; then echo yes; else echo no; fi',
         "_", str(WIN_PATH_SH), path],
        text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip() == "yes"


# Пути с машины аналитика — те самые, на которых всё и сломалось.
@pytest.mark.parametrize("path", [
    "/mnt/c/Users/Denis/AppData/Roaming/npm/codex",
    "/mnt/c/Users/Denis/AppData/Roaming/npm/npx",
    "/mnt/d/tools/claude",
])
def test_a_command_from_the_windows_disk_is_rejected(path):
    assert _is_windows_path(path), path


# Обратная граница, и она не формальность: /mnt/data — обычное место для
# программ на Linux. Правило по «любому /mnt/*» отвергало бы рабочую
# установку, поэтому в шаблоне стоит ровно буква диска.
@pytest.mark.parametrize("path", [
    "/usr/bin/codex",
    "/usr/local/bin/codex",
    "/home/denis/.local/bin/uzum",
    "/mnt/data/bin/codex",
    "/mnt/storage/tools/claude",
    "/opt/homebrew/bin/claude",
])
def test_a_normal_unix_path_is_accepted(path):
    assert not _is_windows_path(path), path


def test_every_entry_point_uses_the_shared_rule_instead_of_its_own_copy():
    """Раньше эта проверка была написана трижды — по копии в мастере,
    установщике и лаунчере. Разъехавшись, они начали бы расходиться в том,
    какой движок считается установленным, и увидеть это можно было бы
    только по симптому у аналитика."""
    for name in ("setup.sh", "install-windows.sh", "bin/uzum"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "lib/win_path.sh" in text, name
        assert "have_native()" not in text, (
            "%s снова завёл собственную копию правила" % name)
