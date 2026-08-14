"""Тесты на установщик окружения Windows — `install-windows.sh`, запущенный
целиком.

Проверяется то, ради чего он и появился: шаг с `%UserProfile%\\.wslconfig`
перестал быть пунктом инструкции, который пропускают. Файл лежит на диске
Windows и виден из WSL, поэтому установщик его читает и, если нужно,
создаёт сам — а дальше решает, можно ли запускать мастер прямо сейчас.

Windows тут никакой нет и не требуется: установщик обращается к
Windows-стороне ровно двумя командами, `cmd.exe` и `wslpath`, и обе
подставляются стабами — ровно так же, как `curl` и `uv` в
tests/test_setup_wizard.py. Стабы ничего не решают за проверяемый код: они
отвечают тем же, чем ответила бы настоящая Windows, а все утверждения
тестов — про файл, который установщик написал, и про то, дошёл ли он до
мастера.

Секретов в файле нет — только выдуманные значения.
"""
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_SKIP_DIRS = {".git", ".pytest_cache", "__pycache__", "work", "tests", ".codex"}

# Мастер подменяется маркером: запускать настоящий setup.sh здесь незачем
# (он проверяется своим набором), а вот ответ на вопрос «дошло до мастера
# или нет» — ровно то, что этот файл и проверяет. Подменить его через PATH
# нельзя: установщик зовёт его абсолютным путём, поэтому подменяем в копии
# репозитория.
MASTER_MARKER = "МАСТЕР ЗАПУЩЕН"


def _script(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_repo_copy(tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(
        REPO_ROOT, repo,
        ignore=lambda src, names: [n for n in names if n in _SKIP_DIRS],
    )
    _script(repo / "setup.sh",
            "#!/usr/bin/env bash\necho '%s'\n" % MASTER_MARKER)
    return repo


def _run_installer(repo, tmp_path, win_home_exists=True):
    """Запускает установщик так, как он выполнялся бы в WSL на машине, где
    всё уже стоит: движок, uv, Node. Так тест остаётся про сеть и про
    решение «запускать мастер или нет», а не про apt."""
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir(exist_ok=True)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True, exist_ok=True)
    # Codex уже авторизован — иначе установщик пошёл бы в `codex login`,
    # который тут проверять нечего.
    (home / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

    win_home = tmp_path / "winhome"
    if win_home_exists:
        win_home.mkdir(exist_ok=True)

    # Ядро сообщает Linux: без этого установщик отказался бы работать на
    # macOS, где тесты обычно и запускаются.
    _script(stub_dir / "uname", "#!/bin/sh\necho Linux\n")
    # Настоящий cmd.exe отвечает Windows-путём и переводом строки в формате
    # Windows — CRLF здесь не декорация, установщик его срезает.
    _script(stub_dir / "cmd.exe", "#!/bin/sh\nprintf 'C:\\\\Users\\\\Denis\\r\\n'\n")
    _script(stub_dir / "wslpath", "#!/bin/sh\necho %s\n" % win_home)
    # Всё, что уже стоит на машине: установщик их только находит.
    for name in ("uv", "uvx", "npx", "codex"):
        _script(stub_dir / name, "#!/usr/bin/env bash\nexit 0\n")
    # Страховка: если проверка «уже стоит» когда-нибудь разъедется, тест
    # должен упасть, а не молча поставить пакеты на машине разработчика.
    for name in ("sudo", "apt-get", "npm"):
        _script(stub_dir / name,
                "#!/usr/bin/env bash\necho 'СТАВИТ ПАКЕТЫ: %s' >&2\nexit 1\n" % name)

    env = {
        "HOME": str(home),
        "PATH": "%s:/usr/bin:/bin:/usr/sbin:/sbin" % stub_dir,
        "USER": "test",
        "TERM": "dumb",
        "WSL_DISTRO_NAME": "Ubuntu",
    }
    completed = subprocess.run(
        ["bash", str(repo / "install-windows.sh")],
        env=env, cwd=str(repo), text=True, capture_output=True, timeout=120,
    )
    return completed, win_home / ".wslconfig"


def test_the_network_setting_is_written_instead_of_being_explained(tmp_path):
    """Раньше это был пункт инструкции: создай %UserProfile%\\.wslconfig
    блокнотом. Пункт пропускали, а расплата приходила через десять минут —
    ClickHouse не отвечал, и это читалось как неверный пароль."""
    repo = _make_repo_copy(tmp_path)

    result, wslconfig = _run_installer(repo, tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert wslconfig.exists(), result.stdout
    written = wslconfig.read_text(encoding="utf-8")
    assert "networkingMode=mirrored" in written, written
    assert "[wsl2]" in written, written


def test_the_master_is_not_started_before_wsl_is_restarted(tmp_path):
    """Настройка сети применяется только после `wsl --shutdown`. Запустить
    мастер сразу — значит показать красный смоук-тест ClickHouse, который
    означает не «нет доступа», а «ещё не перезагрузились»."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_installer(repo, tmp_path)

    assert MASTER_MARKER not in result.stdout, result.stdout
    assert "wsl --shutdown" in result.stdout, result.stdout
    # И сказано, чем продолжить, — иначе человек остаётся с настроенной
    # машиной и без единой подсказки, что делать дальше.
    assert "./setup.sh" in result.stdout, result.stdout


def test_an_existing_setting_lets_the_master_start_right_away(tmp_path):
    """Обратная сторона: на машине, где зеркальный режим уже включён,
    лишнего шага быть не должно — установка идёт до конца за один заход."""
    repo = _make_repo_copy(tmp_path)
    win_home = tmp_path / "winhome"
    win_home.mkdir()
    (win_home / ".wslconfig").write_text(
        "[wsl2]\nnetworkingMode=mirrored\n", encoding="utf-8")

    result, _ = _run_installer(repo, tmp_path)

    assert MASTER_MARKER in result.stdout, result.stdout
    assert "wsl --shutdown" not in result.stdout, result.stdout


def test_a_foreign_wslconfig_is_not_rewritten(tmp_path):
    """В .wslconfig может лежать чужая настройка — лимит памяти, своё ядро.
    Разбирать INI шеллом, чтобы вложиться в нужную секцию, — способ
    испортить рабочий файл, поэтому установщик говорит словами."""
    repo = _make_repo_copy(tmp_path)
    win_home = tmp_path / "winhome"
    win_home.mkdir()
    original = "[wsl2]\nmemory=8GB\nprocessors=4\n"
    (win_home / ".wslconfig").write_text(original, encoding="utf-8")

    result, wslconfig = _run_installer(repo, tmp_path)

    assert wslconfig.read_text(encoding="utf-8") == original, wslconfig.read_text()
    assert "не трогаю чужой файл" in result.stdout, result.stdout
    assert "networkingMode=mirrored" in result.stdout, result.stdout
    assert MASTER_MARKER not in result.stdout, result.stdout


def test_an_unreachable_windows_side_is_said_out_loud(tmp_path):
    """Дотянуться до Windows-стороны получается не всегда. Молча пройти
    мимо нельзя: сеть — единственное, без чего установка выглядит удачной и
    не работает."""
    repo = _make_repo_copy(tmp_path)

    result, _ = _run_installer(repo, tmp_path, win_home_exists=False)

    assert "не нашёл домашнюю папку Windows" in result.stdout, result.stdout
    assert "networkingMode=mirrored" in result.stdout, result.stdout
