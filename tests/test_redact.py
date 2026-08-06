from redact import redact, load_secret_values


def test_masks_known_secret_value():
    text = "подключаюсь с паролем hunter2 к базе"
    assert redact(text, {"hunter2": "CH_PASSWORD"}) == (
        "подключаюсь с паролем [СКРЫТО:CH_PASSWORD] к базе"
    )


def test_masks_anthropic_token_without_known_values():
    text = "ключ sk-ant-api03-AAAABBBBCCCCDDDD в конфиге"
    assert "sk-ant-api03" not in redact(text)
    assert "[СКРЫТО:TOKEN]" in redact(text)


def test_masks_bearer_header():
    text = 'Authorization: Bearer abcdef123456ghijkl'
    out = redact(text)
    assert "abcdef123456ghijkl" not in out


def test_ignores_short_values():
    # короткие значения не маскируем — иначе побьём осмысленный текст
    text = "статус ok"
    assert redact(text, {"ok": "SOME_KEY"}) == "статус ok"


def test_load_secret_values_parses_env_file(tmp_path):
    p = tmp_path / "secrets.env"
    p.write_text(
        "# комментарий\n"
        "CH_USER=denis-platon\n"
        'JIRA_TOKEN="test-token-xxx"\n'
        "EMPTY=\n",
        encoding="utf-8",
    )
    assert load_secret_values(str(p)) == {
        "denis-platon": "CH_USER",
        "test-token-xxx": "JIRA_TOKEN",
    }


def test_returns_empty_for_missing_file():
    assert load_secret_values("/nope/secrets.env") == {}
