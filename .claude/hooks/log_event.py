#!/usr/bin/env python3
"""Хук шагов: пишет вызовы инструментов и промпты в sandbox.ai_usage_events.

Всегда завершается с кодом 0 — телеметрия не имеет права мешать работе.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import redact  # noqa: E402
import telemetry  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")
TRACKED = ("UserPromptSubmit", "PostToolUse", "PostToolUseFailure")


def _mcp_server(tool_name):
    """mcp__clickhouse__run_query → clickhouse; нативный тул → пустая строка."""
    if not tool_name.startswith("mcp__"):
        return ""
    parts = tool_name.split("__")
    return parts[1] if len(parts) > 2 else ""


def build_row(payload, secrets):
    event = payload.get("hook_event_name", "")
    if event not in TRACKED:
        return None
    tool_name = payload.get("tool_name", "") or ""
    error_text = payload.get("tool_error", "") or ""
    return {
        "ts": telemetry.utc_now_str(milliseconds=True),
        "session_id": payload.get("session_id", ""),
        "user": os.environ.get("UZUM_USER", os.environ.get("USER", "")),
        "event_type": event,
        "tool_name": tool_name,
        "mcp_server": _mcp_server(tool_name),
        "duration_ms": int(payload.get("duration_ms", 0) or 0),
        "ok": 0 if event == "PostToolUseFailure" else 1,
        "error_text": redact.redact(error_text, secrets)[:2000],
    }


def main():
    try:
        payload = json.load(sys.stdin)
        row = build_row(payload, redact.load_secret_values(SECRETS_PATH))
        if row:
            telemetry.write("ai_usage_events", row)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
