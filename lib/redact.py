"""Маскирование секретов перед записью логов.

Два источника: известные значения из secrets.env и регулярки под токены
распространённых форматов. Значения короче 8 символов не трогаем — иначе
маскирование побьёт осмысленный текст.
"""
import re

MIN_SECRET_LEN = 7

TOKEN_PATTERNS = [
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "TOKEN"),
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "TOKEN"),
    (re.compile(r"ghp_[A-Za-z0-9]{8,}"), "TOKEN"),
    (re.compile(r"gho_[A-Za-z0-9]{8,}"), "TOKEN"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9\-]{8,}"), "TOKEN"),
    (re.compile(r"AKIA[0-9A-Z]{12,}"), "TOKEN"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"), "TOKEN"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                re.DOTALL), "PRIVATE_KEY"),
]


def redact(text, secrets=None):
    """Заменить секреты в тексте на метки."""
    if not text:
        return text
    out = text
    for value, label in sorted((secrets or {}).items(), key=lambda kv: -len(kv[0])):
        if value and len(value) >= MIN_SECRET_LEN:
            out = out.replace(value, "[СКРЫТО:%s]" % label)
    for pattern, label in TOKEN_PATTERNS:
        out = pattern.sub("[СКРЫТО:%s]" % label, out)
    return out


def load_secret_values(path):
    """Прочитать env-файл и вернуть {значение: имя_переменной}."""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        if value:
            values[value] = key.strip()
    return values
