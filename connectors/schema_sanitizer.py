#!/usr/bin/env python3
"""Stdio MCP proxy that strips top-level oneOf/anyOf/allOf from tool inputSchemas.

The Anthropic API rejects any tool whose input_schema has oneOf/anyOf/allOf at the
top level ("input_schema does not support oneOf, allOf, or anyOf at the top level"),
and because the full tool list is sent on every request, one bad tool breaks the
whole session. Some MCP servers ship such schemas. This proxy spawns the real
server (argv) and rewrites only tools/list results, merging the union branches
into a flat object schema so Claude accepts them. Everything else is passed
through byte-for-byte.

Перенесено без изменения логики (только докстринг/сообщение об использовании) —
скрипт уже был полностью на стандартной библиотеке и не завязан на харнесс.
Использование: обёртка вокруг команды запуска другого MCP-сервера, например
["python3", "connectors/schema_sanitizer.py", "uvx", "mcp-some-server"].
"""
import sys
import json
import subprocess
import threading

_UNION_KEYWORDS = ("oneOf", "anyOf", "allOf")


def _flatten(schema):
    if not isinstance(schema, dict):
        return schema
    merged = {}
    hit = False
    for kw in _UNION_KEYWORDS:
        branches = schema.get(kw)
        if isinstance(branches, list):
            hit = True
            for branch in branches:
                if isinstance(branch, dict):
                    for key, val in (branch.get("properties") or {}).items():
                        merged.setdefault(key, val)
            schema.pop(kw, None)
    if hit:
        props = schema.setdefault("properties", {})
        for key, val in merged.items():
            props.setdefault(key, val)
        schema.pop("required", None)
    schema.setdefault("type", "object")
    return schema


def _rewrite(line):
    try:
        msg = json.loads(line)
    except Exception:
        return line
    result = msg.get("result")
    if isinstance(result, dict) and isinstance(result.get("tools"), list):
        for tool in result["tools"]:
            sch = tool.get("inputSchema")
            if isinstance(sch, dict):
                _flatten(sch)
        return json.dumps(msg) + "\n"
    return line


def _pump(child):
    for raw in child.stdout:
        try:
            sys.stdout.buffer.write(_rewrite(raw.decode("utf-8")).encode("utf-8"))
        except Exception:
            sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()


def main():
    cmd = sys.argv[1:]
    if not cmd:
        sys.stderr.write("использование: schema_sanitizer.py <команда> [аргументы...]\n")
        sys.exit(2)
    child = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    threading.Thread(target=_pump, args=(child,), daemon=True).start()
    try:
        for raw in sys.stdin.buffer:
            child.stdin.write(raw)
            child.stdin.flush()
    except Exception:
        pass
    child.terminate()
    sys.exit(child.wait())


if __name__ == "__main__":
    main()
