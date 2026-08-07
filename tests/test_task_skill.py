from pathlib import Path

SKILL = (Path(__file__).resolve().parent.parent
         / ".agents" / "skills" / "task" / "SKILL.md")


def test_skill_exists_with_description():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "description:" in text.split("---")[1]


def test_covers_all_eight_steps():
    text = SKILL.read_text(encoding="utf-8")
    for marker in ("work/", "data-sanity", "ai-draft", "task.md"):
        assert marker in text, marker


def test_requires_confirmation_before_posting():
    text = SKILL.read_text(encoding="utf-8").lower()
    assert "не публикуй" in text or "только после подтверждения" in text


def test_templates_file_has_both_templates():
    templates = (SKILL.parent / "references" / "comment-templates.md").read_text(
        encoding="utf-8")
    assert "[ai-draft]" in templates
    assert "[verdict]" in templates
