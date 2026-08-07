#!/usr/bin/env bash
# Обновляет репозиторий в начале сессии и сообщает, что приехало.
# Никогда не завершается ненулевым кодом и ничего не разруливает молча.
set -uo pipefail

emit() {
  python3 - "$1" <<'PY'
import json, sys
print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": sys.argv[1],
    }
}, ensure_ascii=False))
PY
  exit 0
}

git rev-parse --git-dir >/dev/null 2>&1 || emit "Репозиторий не найден, обновление пропущено."

before=$(git rev-parse HEAD 2>/dev/null || echo "")
output=$(git pull --ff-only --quiet 2>&1)
status=$?
after=$(git rev-parse HEAD 2>/dev/null || echo "")

if [ $status -ne 0 ]; then
  emit "Не удалось обновить репозиторий: ${output}
Работать можно, но скиллы могут быть устаревшими. Если это конфликт — сохрани свои правки и напиши Насте."
fi

if [ "$before" = "$after" ]; then
  emit "Репозиторий актуален."
fi

changed=$(git diff --name-only "$before" "$after" -- .agents/skills context | head -20)
emit "Репозиторий обновлён. Изменилось:
${changed:-нет изменений в скиллах и контексте}"
