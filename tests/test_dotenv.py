"""Заполнение доступов файлом `.env` вместо ответов на вопросы мастера.

Разбор файла — тот же envfile.py, что и для secrets.env (см. test_envfile.py):
второго парсера здесь нет, `read_dotenv` только проверяет права и зовёт
`envfile.read`. Эти тесты — про то, что специфично для .env-файла: права 600
на входе и приоритет "значение из файла важнее вопроса".
"""
import os
import stat

import setup_helpers

# Те же коварные значения, что и в test_envfile.py — .env заполняют вручную,
# без кавычек, и именно тут раньше ломался пароль с пробелом/$/бэктиком.
# QUOTE — литеральный апостроф в значении: формат разбирается по тем же
# правилам, что и `source` в шелле (см. lib/envfile.py), поэтому голый
# апостроф внутри неэкранированного текста открывает кавычку, а не значит
# сам себя — ровно как в bash. Человек, который вписывает пароль с
# апострофом от руки, обязан обернуть его в двойные кавычки — это и
# проверяем, а не выдумываем несуществующий синтаксис.
NASTY = {
    "SPACE": "p@ss w0rd",
    "DOLLAR": "abc$HOME def",
    "BACKTICK": "a`id`b",
    "QUOTE": "a'b'c",
}
# Как выглядит .env-строка для каждого ключа: большинство — без кавычек
# (как обычно пишут руками), QUOTE — в двойных кавычках, потому что голый
# апостроф внутри неэкранированного текста в этом формате открывает кавычку
# (см. пояснение выше).
NASTY_LINES = {
    "SPACE": "SPACE=p@ss w0rd\n",
    "DOLLAR": "DOLLAR=abc$HOME def\n",
    "BACKTICK": "BACKTICK=a`id`b\n",
    "QUOTE": 'QUOTE="a\'b\'c"\n',
}


def test_reads_plain_dotenv_values(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CH_USER=denis-platon\nCH_PASSWORD=p@ss w0rd\n", encoding="utf-8")
    os.chmod(path, 0o600)
    values, tightened = setup_helpers.read_dotenv(str(path))
    assert values == {"CH_USER": "denis-platon", "CH_PASSWORD": "p@ss w0rd"}
    assert tightened is False


def test_nasty_values_survive_the_round_trip(tmp_path):
    """То самое, что уже однажды ломалось (Находка 4, test_envfile.py):
    пробел/`$`/бэктик/кавычка в значении, вписанном вручную в .env."""
    path = tmp_path / ".env"
    path.write_text("".join(NASTY_LINES[k] for k in NASTY), encoding="utf-8")
    os.chmod(path, 0o600)
    values, _ = setup_helpers.read_dotenv(str(path))
    assert values == NASTY


def test_blank_and_missing_values_are_not_returned_as_present(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CH_USER=denis\nCH_PASSWORD=\n", encoding="utf-8")
    os.chmod(path, 0o600)
    values, _ = setup_helpers.read_dotenv(str(path))
    # Пустое значение по разбору присутствует, но для приоритета это
    # "не заполнено" — эту границу проверяет missing_keys ниже.
    assert values.get("CH_USER") == "denis"
    assert values.get("CH_PASSWORD") == ""


def test_missing_file_returns_empty_without_error(tmp_path):
    values, tightened = setup_helpers.read_dotenv(str(tmp_path / "nope.env"))
    assert values == {}
    assert tightened is False


def test_loose_permissions_are_tightened_to_600(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CH_USER=denis\n", encoding="utf-8")
    os.chmod(path, 0o644)
    values, tightened = setup_helpers.read_dotenv(str(path))
    assert values == {"CH_USER": "denis"}
    assert tightened is True
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_permissions_already_600_are_left_alone_and_not_reported(tmp_path):
    path = tmp_path / ".env"
    path.write_text("CH_USER=denis\n", encoding="utf-8")
    os.chmod(path, 0o600)
    _, tightened = setup_helpers.read_dotenv(str(path))
    assert tightened is False


def test_group_readable_file_is_also_tightened(tmp_path):
    """Не только 'мир', но и группа — секрет не должен читаться никем, кроме
    владельца."""
    path = tmp_path / ".env"
    path.write_text("CH_USER=denis\n", encoding="utf-8")
    os.chmod(path, 0o640)
    _, tightened = setup_helpers.read_dotenv(str(path))
    assert tightened is True
    assert oct(path.stat().st_mode)[-3:] == "600"


# ── приоритет: значение из файла важнее вопроса ─────────────────────────

def test_missing_keys_flags_absent_and_empty_values():
    values = {"CH_USER": "denis", "CH_PASSWORD": ""}
    assert setup_helpers.missing_keys(values, ["CH_USER", "CH_PASSWORD", "JIRA_TOKEN"]) == [
        "CH_PASSWORD",
        "JIRA_TOKEN",
    ]


def test_missing_keys_is_empty_when_everything_is_filled():
    values = {"CH_USER": "denis", "CH_PASSWORD": "secret"}
    assert setup_helpers.missing_keys(values, ["CH_USER", "CH_PASSWORD"]) == []


def test_missing_keys_preserves_the_order_of_required_list():
    values = {}
    assert setup_helpers.missing_keys(values, ["B", "A"]) == ["B", "A"]
