"""Тесты на bin/uzum — находка ревью (задача Codex-6, второй раунд).

`bin/uzum` дёргает `python3 -c "... setup_helpers.select_engine(...) ..."`
для выбора движка (первый раунд ревью свёл эту логику к одной реализации —
см. lib/setup_helpers.py). Но сама эта правка ввела новую зависимость от
внешнего процесса ровно в развилке выбора движка: если помощник не
отработал (упал, вернул не то, интерпретатора вообще нет) — переменные
ENGINE/REASON пустые, ни одна ветка case не совпадает, и раньше скрипт
безусловно проваливался в `exec claude` с кодом успеха. Человек попросил
Codex — получил Claude Code, и ничто ему об этом не сказало. Тот же класс
дефекта, что уже дважды ловили на этом проекте (выглядит как рабочий
запуск, а на самом деле нет).

Копируем `bin/uzum` (и, где нужно для контроля, `lib/setup_helpers.py`) в
изолированную временную папку — подставной REPO_DIR, чтобы не трогать
настоящий репозиторий и не зависеть от PATH машины, где гоняются тесты.
Тот же приём, что уже используется для хуков (`tests/test_session_start.py`
— `subprocess.run(["bash", str(script)], ...)`), только для bin/uzum.
"""
import shutil
import stat
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_UZUM = REPO_ROOT / "bin" / "uzum"


def _stub_engine(path):
    """Заглушка claude/codex — печатает, что её вызвали, и с чем, и
    завершается успешно. Если она хоть раз попадёт в вывод в тестах ниже —
    значит движок реально запустился, это и есть проверяемый факт."""
    path.write_text(
        "#!/usr/bin/env bash\necho \"STUB $(basename \"$0\") called args=[$*]\"\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _make_repo(tmp_path, broken_helper=False):
    """Минимальная копия репозитория, достаточная для запуска bin/uzum:
    сам bin/uzum и lib/setup_helpers.py (+ envfile.py, от которого
    setup_helpers.py зависит на уровне импорта)."""
    repo = tmp_path / "repo"
    (repo / "bin").mkdir(parents=True)
    (repo / "lib").mkdir(parents=True)
    (repo / "work").mkdir()
    shutil.copy(BIN_UZUM, repo / "bin" / "uzum")
    (repo / "bin" / "uzum").chmod(0o755)
    if broken_helper:
        # Синтаксически валидный python-модуль, который безусловно падает
        # при импорте — воспроизводит "вспомогательный процесс не
        # отработал" любой причиной (исключение), не только отсутствием
        # интерпретатора.
        (repo / "lib" / "setup_helpers.py").write_text(
            "raise RuntimeError('сломанный помощник — намеренно для теста')\n",
            encoding="utf-8",
        )
    else:
        shutil.copy(REPO_ROOT / "lib" / "setup_helpers.py", repo / "lib" / "setup_helpers.py")
        shutil.copy(REPO_ROOT / "lib" / "envfile.py", repo / "lib" / "envfile.py")
    return repo


def _isolated_home(tmp_path, engine_bin_dir):
    home = tmp_path / "home"
    (home / ".config" / "uzum-ai").mkdir(parents=True)
    (home / ".config" / "uzum-ai" / "secrets.env").write_text("CH_USER='t'\n", encoding="utf-8")
    engine_bin_dir.mkdir()
    _stub_engine(engine_bin_dir / "claude")
    _stub_engine(engine_bin_dir / "codex")
    return home


def _curated_path(extra_dirs, include_python):
    """PATH со стандартными утилитами (dirname, cat, sed, ...), собранный
    ЯВНЫМ куратором — символические ссылки на конкретные бинари по имени, а
    не подключение целых системных каталогов. Два свойства, которые важны
    для тестов ниже и которых не даёт просто "взять PATH процесса":

      1. `include_python=False` — python3 гарантированно НЕДОСТИЖИМ (не
         просто "не установлен где-то", а буквально нет ни одного пути в
         PATH, где его можно найти), а не просто "может, а может и не
         найдётся, в зависимости от машины, где гоняются тесты".
      2. Не протаскивает случайные claude/codex, если они реально стоят на
         машине, где гоняется тест (иначе сценарий "заглушку codex убрали —
         значит codex недоступен" был бы неверным: PATH нашёл бы настоящий
         codex дальше по списку каталогов)."""
    tools_dir = extra_dirs[0].parent / ("coreutils-with-python" if include_python else "coreutils-no-python")
    tools_dir.mkdir(exist_ok=True)
    needed = ["dirname", "basename", "readlink", "cat", "mkdir", "sed",
              "printf", "grep", "rm", "ls", "bash", "true", "false"]
    if include_python:
        needed.append("python3")
    for name in needed:
        real = shutil.which(name)
        target = tools_dir / name
        if real and not target.exists():
            target.symlink_to(real)
    parts = [str(d) for d in extra_dirs] + [str(tools_dir)]
    return ":".join(parts)


def _run_uzum(repo, home, path, args=(), input_text="\n"):
    env = {"HOME": str(home), "PATH": path, "USER": "test", "TERM": "xterm"}
    return subprocess.run(
        ["bash", str(repo / "bin" / "uzum"), *args],
        env=env, cwd=str(repo), input=input_text,
        capture_output=True, text=True, timeout=30,
    )


def test_broken_helper_fails_loudly_instead_of_defaulting_to_claude(tmp_path):
    """Critical, второй раунд ревью: вспомогательный python-процесс упал
    (здесь — импорт lib/setup_helpers.py бросает исключение) — bin/uzum
    обязан завершиться ненулевым кодом и явным сообщением, а не молча
    запустить Claude Code (та самая тихая заглушка, которую как раз
    искореняли всей этой задачей)."""
    repo = _make_repo(tmp_path, broken_helper=True)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode != 0, "сломанный помощник не должен давать код успеха"
    combined = result.stdout + result.stderr
    assert "STUB claude" not in combined, (
        "молча запустил Claude Code вместо честной ошибки:\n" + combined
    )
    assert "STUB codex" not in combined


def test_missing_python3_fails_loudly_with_a_specific_message(tmp_path):
    """Второй сценарий ревью, вполне реалистичный: python3 отсутствует в
    PATH целиком, оба движка при этом установлены. Раньше это давало одну
    строку "command not found" в stderr и тут же тихий запуск Claude Code
    с кодом успеха. Сообщение обязано называть python3 явно — открытие
    самого факта зависимости, не только про то, что что-то не найдено."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=False)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode != 0, "без python3 не должно быть кода успеха"
    combined = result.stdout + result.stderr
    assert "STUB claude" not in combined, (
        "молча запустил Claude Code вместо честной ошибки:\n" + combined
    )
    assert "STUB codex" not in combined
    assert "python3" in combined.lower(), (
        "сообщение обязано явно называть python3, а не просто \"не найдено\":\n" + combined
    )


def test_healthy_path_still_launches_the_requested_engine(tmp_path):
    """Бэкстоп: находки выше не должны были сломать штатный путь — с живым
    python3 и рабочим помощником --codex по-прежнему запускает codex."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=["--codex"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB codex" in result.stdout


def test_healthy_path_without_flag_launches_the_only_configured_engine(tmp_path):
    """Тот же бэкстоп для пути "единственный настроенный движок" — только
    claude в PATH, без флага."""
    repo = _make_repo(tmp_path, broken_helper=False)
    engine_bin = tmp_path / "engine-bin"
    home = _isolated_home(tmp_path, engine_bin)
    (engine_bin / "codex").unlink()
    path = _curated_path([engine_bin], include_python=True)

    result = _run_uzum(repo, home, path, args=[])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "STUB claude" in result.stdout
