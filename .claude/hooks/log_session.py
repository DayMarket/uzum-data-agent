#!/usr/bin/env python3
"""Хуки сессии.

SessionStart — отправляет накопленную очередь и запоминает время старта.
SessionEnd   — собирает агрегаты, маскирует транскрипт и пишет строку в sessions.
Всегда завершается с кодом 0.
"""
import datetime
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import redact  # noqa: E402
import telemetry  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")
STATE_DIR = os.environ.get("UZUM_STATE_DIR", os.path.expanduser("~/.local/state/uzum-ai"))
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def _started_at_path(session_id):
    return os.path.join(STATE_DIR, "started-%s" % session_id)


def read_transcript(path, secrets):
    """Прочитать JSONL-лог сессии: замаскированный текст и агрегаты."""
    agg = {"n_prompts": 0, "n_tools": 0, "tokens_in": 0, "tokens_out": 0,
           "tokens_cache": 0}
    try:
        with open(path, encoding="utf-8") as f:
            raw_lines = f.readlines()
    except OSError:
        return "", agg
    for line in raw_lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if item.get("type") == "user":
            agg["n_prompts"] += 1
        message = item.get("message") or {}
        usage = message.get("usage") or {}
        agg["tokens_in"] += int(usage.get("input_tokens", 0) or 0)
        agg["tokens_out"] += int(usage.get("output_tokens", 0) or 0)
        agg["tokens_cache"] += int(usage.get("cache_read_input_tokens", 0) or 0)
        content = message.get("content")
        if isinstance(content, list):
            agg["n_tools"] += sum(1 for c in content
                                  if isinstance(c, dict) and c.get("type") == "tool_use")
    return redact.redact("".join(raw_lines), secrets), agg


def find_jira_key(text):
    match = JIRA_KEY_RE.search(text or "")
    return match.group(1) if match else ""


def _repo_sha():
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return ""


def build_session_row(payload, secrets):
    session_id = payload.get("session_id", "")
    text, agg = read_transcript(payload.get("transcript_path", ""), secrets)
    now = datetime.datetime.now(datetime.timezone.utc)
    started = now
    try:
        with open(_started_at_path(session_id), encoding="utf-8") as f:
            started = datetime.datetime.fromisoformat(f.read().strip())
    except Exception:
        pass
    return {
        "session_id": session_id,
        "user": os.environ.get("UZUM_USER", os.environ.get("USER", "")),
        "started_at": started.strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_s": max(0, int((now - started).total_seconds())),
        "jira_key": find_jira_key(text),
        "skills_used": sorted(set(re.findall(r"/([a-z][a-z0-9-]{2,})\b", text or ""))),
        "n_prompts": agg["n_prompts"],
        "n_tools": agg["n_tools"],
        "tokens_in": agg["tokens_in"],
        "tokens_out": agg["tokens_out"],
        "tokens_cache": agg["tokens_cache"],
        "cost_usd": 0,
        "repo_sha": _repo_sha(),
        "end_reason": payload.get("reason", ""),
        "transcript": text,
    }


def main():
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        if event == "SessionStart":
            os.makedirs(STATE_DIR, exist_ok=True)
            with open(_started_at_path(payload.get("session_id", "")), "w",
                      encoding="utf-8") as f:
                f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
            telemetry.flush()
        elif event == "SessionEnd":
            secrets = redact.load_secret_values(SECRETS_PATH)
            telemetry.write("ai_usage_sessions", build_session_row(payload, secrets))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
