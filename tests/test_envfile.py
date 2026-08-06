"""Формат файла секретов: запись, чтение и — главное — то, что из него
получает `source` в bin/uzum.

Находка 4 финального ревью: значения писались как KEY=VALUE без кавычек, а
bin/uzum делает `set -a; source secrets.env`. Воспроизведено до правки:
пароль `p@ss w0rd` давал "w0rd: command not found" и переменной не появлялось
вовсе; `abc$HOME def` подставлял домашний каталог внутрь токена; a`id`b
выполнял команду. А раз в .mcp.json секреты подставляются голым ${VAR} без
дефолта, отсутствие переменной валит запись MCP-сервера целиком.
"""
import subprocess

import envfile
import redact
import setup_helpers

# Значения, на которых ломался старый формат, плюс соседние случаи.
NASTY = {
    "SPACE": "p@ss w0rd",
    "DOLLAR": "abc$HOME def",
    "QUOTE": "a'b'c",
    "DQUOTE": 'a"b"c',
    "BACKTICK": "a`id`b",
    "BACKSLASH": "a\\b\\'c",
    "NEWLINE": "line1\nline2",
    "SEMICOLON": "a; rm -rf /tmp/nope",
    "PLAIN": "denis-platon",
    "EQUALS": "key=value=more",
    "TRAILING_SPACE": "secret ",
}


def test_quote_roundtrip():
    for value in NASTY.values():
        assert envfile.unquote(envfile.quote(value)) == value


def test_write_and_read_back_every_nasty_value(tmp_path):
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), NASTY)
    assert envfile.read(str(path)) == NASTY


def test_bash_source_gets_exactly_the_written_values(tmp_path):
    """Главная проверка: значение должно доехать до окружения дочернего
    процесса ровно таким, каким его ввёл человек в мастере."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), NASTY)

    # печатаем каждое значение с нулевым разделителем — так не потеряем
    # перевод строки внутри значения
    script = 'set -a; . "$1"; set +a; ' + "".join(
        'printf "%%s\\0" "${%s-<НЕТ ПЕРЕМЕННОЙ>}"; ' % key for key in NASTY
    )
    out = subprocess.run(
        ["bash", "-c", script, "bash", str(path)],
        capture_output=True, text=True, check=True,
    )
    got = out.stdout.split("\0")[:-1]
    assert got == list(NASTY.values())
    assert out.stderr == ""


def test_backtick_value_does_not_execute_a_command(tmp_path):
    """Регрессия: `a`id`b` раньше исполнялся шеллом при source."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_PASSWORD": "a`id`b"})
    out = subprocess.run(
        ["bash", "-c", 'set -a; . "$1"; set +a; printf "%s" "$CH_PASSWORD"',
         "bash", str(path)],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout == "a`id`b"
    assert "uid=" not in out.stdout


def test_redact_masks_a_password_with_spaces_from_quoted_file(tmp_path):
    """Наивное снятие кавычек .strip("'") вернуло бы не тот текст — и
    настоящий пароль уехал бы в транскрипт незамаскированным."""
    path = tmp_path / "secrets.env"
    setup_helpers.write_env(str(path), {"CH_PASSWORD": "p@ss w0rd 'x'"})
    secrets = redact.load_secret_values(str(path))
    assert secrets == {"p@ss w0rd 'x'": "CH_PASSWORD"}
    assert "p@ss" not in redact.redact("упал логин с паролем p@ss w0rd 'x'", secrets)


def test_reads_old_unquoted_file(tmp_path):
    """Файлы, написанные прошлой версией мастера, читаются как раньше:
    значение — весь остаток строки, хвостовые пробелы не в счёт."""
    path = tmp_path / "secrets.env"
    path.write_text(
        "# комментарий\n"
        "CH_USER=denis-platon\n"
        'JIRA_TOKEN="test-token-xxx"  \n'
        "CH_PASSWORD=p@ss w0rd   \n"
        "EMPTY=\n",
        encoding="utf-8",
    )
    assert envfile.read(str(path)) == {
        "CH_USER": "denis-platon",
        "JIRA_TOKEN": "test-token-xxx",
        "CH_PASSWORD": "p@ss w0rd",
        "EMPTY": "",
    }


def test_rewrite_upgrades_old_file_to_quoted_format(tmp_path):
    """Мастер, дописывая один ключ, чинит формат и остальных: иначе пароль,
    записанный старой версией, продолжал бы ронять source."""
    path = tmp_path / "secrets.env"
    path.write_text("CH_PASSWORD=p@ss w0rd\n", encoding="utf-8")
    setup_helpers.write_env(str(path), {"CH_USER": "denis"})
    assert path.read_text(encoding="utf-8") == "CH_PASSWORD='p@ss w0rd'\nCH_USER='denis'\n"


def test_missing_file_is_not_an_error():
    assert envfile.read("/nope/secrets.env") == {}
