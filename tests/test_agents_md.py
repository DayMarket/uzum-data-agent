"""AGENTS.md (Codex) и CLAUDE.md (Claude Code) — один и тот же файл, не две
копии. Задача Codex-7: выбранный способ синхронизации — относительный
симлинк `AGENTS.md -> CLAUDE.md`, тот же приём, что уже используют скиллы
(.claude/skills/<имя> -> ../../.agents/skills/<имя>, tests/test_skills.py).
Здесь тот же класс проверки, но для одного файла в корне, а не для дерева
каталогов: копия вместо симлинка не сломает работу сразу (Codex прочитает
файл), но разъедётся с оригиналом при первой же правке CLAUDE.md, и заметить
это будет некому — ровно то, из-за чего предыдущий инструмент команды
провалился (людям было непонятно, какими правилами руководствоваться).

Живым запуском (оба движка, `claude -p` и `codex exec` в этом репозитории)
проверено отдельно при выполнении задачи Codex-7, что оба движка реально
цитируют одно и то же правило 1 из этого файла — тест ниже проверяет
файловую структуру, которая это гарантирует, не заменяет живую проверку, а
защищает её результат от регрессии."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_agents_md_is_a_relative_symlink_to_claude_md():
    agents_md = REPO_ROOT / "AGENTS.md"
    assert agents_md.is_symlink(), (
        agents_md, "AGENTS.md должен быть симлинком на CLAUDE.md, не копией "
        "— иначе правки CLAUDE.md не долетают до Codex")
    raw_target = agents_md.readlink()
    assert not raw_target.is_absolute(), (
        agents_md, raw_target, "абсолютный путь сломается у всех, кроме "
        "того, кто его создал — репозиторий клонируют в разные места")
    assert raw_target == Path("CLAUDE.md"), (agents_md, raw_target)


def test_agents_md_and_claude_md_content_is_identical():
    """Бэкстоп на случай, если симлинк когда-нибудь заменят файлом-копией
    вручную (например, редактор не умеет сохранять через симлинк и тихо
    подменяет его обычным файлом) — тест ловит расхождение по содержимому,
    а не только по типу записи в файловой системе."""
    agents_md = REPO_ROOT / "AGENTS.md"
    claude_md = REPO_ROOT / "CLAUDE.md"
    assert agents_md.read_text(encoding="utf-8") == claude_md.read_text(encoding="utf-8")


def test_claude_md_documents_the_agents_md_symlink():
    """Правило «правь только здесь» само по себе должно быть видно тому, кто
    открыл CLAUDE.md впервые — иначе способ синхронизации знает только тот,
    кто его придумал."""
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "AGENTS.md" in text
    assert "симлинк" in text
