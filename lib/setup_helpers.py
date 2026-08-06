"""Помощники установщика: запись секретов и списка включённых серверов.

Только стандартная библиотека — этот модуль подключается из setup.sh (мастер
установки), который должен работать до того, как в системе появится хоть один
внешний пакет.
"""
import json
import os


def write_env(path, values):
    """Дописать переменные в env-файл, заменяя существующие. Права 600."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    existing = {}
    order = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                if key not in existing:
                    order.append(key)
                existing[key] = value.strip()
    for key, value in values.items():
        if key not in existing:
            order.append(key)
        existing[key] = value
    with open(path, "w", encoding="utf-8") as f:
        for key in order:
            f.write("%s=%s\n" % (key, existing[key]))
    os.chmod(path, 0o600)


def write_enabled_servers(path, servers):
    """Записать список одобренных MCP-серверов, не трогая прочие настройки."""
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError:
            data = {}
    data["enabledMcpjsonServers"] = sorted(set(servers))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
