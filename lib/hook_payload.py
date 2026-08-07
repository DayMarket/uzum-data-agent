# lib/hook_payload.py
"""Приведение события хука к общему виду — для обоих движков.

Источник — docs/codex-facts.md, раздел 2 "Содержимое события хука в обоих
движках" (таблица сравнения, снята живым запуском 07.08.2026: реальные
хуки, реальный `claude -p`/`codex exec`, реальные payload'ы). Ничего здесь
не угадано и не перенесено по аналогии с документацией — только то, что
подтверждено запуском. Где поля нет в принципе — это явно записано ниже, а
не подставлен 0/"" молча: колонка "0 мс" читается как "мгновенно", а не как
"это поле никто не прислал", это разные вещи.

Таблица соответствия (docs/codex-facts.md, раздел 2, "Таблица сравнения"):

| Итоговое поле    | Claude Code                              | Codex                                    |
|------------------|-------------------------------------------|-------------------------------------------|
| session_id       | payload["session_id"] (UUIDv4)            | payload["session_id"] (time-ordered id)   |
|                  | — имя поля совпадает дословно              | — имя поля совпадает дословно             |
| transcript_path  | payload["transcript_path"]                 | payload["transcript_path"]                |
|                  | ~/.claude/projects/<slug>/<session_id>.jsonl | $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl |
| tool_name        | payload["tool_name"]                       | payload["tool_name"]                      |
| duration_ms      | payload["duration_ms"] — есть на           | поля нет НИКОГДА: ни при успехе, ни при   |
|                  | PostToolUse при успехе; отсутствующее/     | ошибке (раздел 2, строка "Длительность"). |
|                  | нечисловое значение — устоявшееся,         | normalize() отдаёт None, а не 0 — 0 здесь |
|                  | покрытое тестами поведение log_event.py:   | означал бы "мгновенно", а это неправда:   |
|                  | деградирует к 0 (другая категория          | мы просто не знаем.                       |
|                  | "отсутствия" — поле в схеме есть, но       |                                            |
|                  | конкретное значение битое/не пришло)       |                                            |
| error_text       | Поля tool_error не существует. Текст       | Поля с текстом ошибки в hook payload нет  |
|                  | ошибки — в поле "error" события            | вообще ни при каком событии (раздел 3):   |
|                  | PostToolUseFailure (PostToolUse при        | tool_response при сбое — пустая строка,   |
|                  | сбое инструмента не вызывается вовсе,      | без exit_code/is_error/stderr. PostToolUse|
|                  | раздел 3) — normalize() читает "error",    | у Codex срабатывает и на успехе, и на     |
|                  | не "tool_error".                           | сбое — но без деталей. normalize() отдаёт |
|                  |                                             | "" и явно не пытается угадать: настоящий  |
|                  |                                             | текст даёт lib/transcript_codex.py, из    |
|                  |                                             | транскрипта, по transcript_path — это     |
|                  |                                             | отдельный шаг, не часть normalize().      |

engine определяется отдельно, см. detect_engine().
"""
import os

ENGINE_CLAUDE = "claude"
ENGINE_CODEX = "codex"
# Ревью-находка 4 (см. отчёт задачи): раньше «не смогли опознать» молча
# сливалось с ENGINE_CLAUDE — тот же класс ошибки, что и провал с
# permission_mode, только слоем ниже. Теперь это отдельное, видимое в
# данных состояние, а не тихая догадка.
ENGINE_UNKNOWN = "unknown"

# Структурный маркер пути транскрипта Claude Code (docs/codex-facts.md,
# раздел 2, строка «Путь к транскрипту»): ~/.claude/projects/<slug>/<id>.jsonl.
# Используется как ПОЗИТИВНЫЙ признак «это точно Claude Code», а не как
# запасной вариант по умолчанию — см. detect_engine().
_CLAUDE_PATH_MARKER = ".claude/projects/"

# Поле turn_id: наблюдалось только у Codex (docs/codex-facts.md, раздел 2)
# и не наблюдалось в живых Claude Code payload'ах (см. ниже, включая
# проверку при подготовке этой задачи). НЕ используем как единственный
# признак и держим отдельно от _CODEX_ONLY_KEYS, которого больше нет —
# см. предупреждение в docstring detect_engine() про то, почему "лишнее
# поле = Codex" оказалось ненадёжной идеей.
_CODEX_TURN_ID_KEY = "turn_id"


def detect_engine(payload):
    """Движок — по содержимому события, а не по переменной окружения (её
    легко забыть выставить при запуске, и тогда телеметрия молча пишет не
    тот движок всем строкам подряд).

    ПРЕДУПРЕЖДЕНИЕ (живая находка сверх docs/codex-facts.md, при подготовке
    этой задачи, 07.08.2026, версия Claude Code та же — 2.1.224, что и в
    разведке): факты утверждали, что permission_mode/model у Claude Code
    "отсутствуют как отдельные поля в этом виде". Живой прогон настоящего
    `claude -p` с реальными хуками, подключёнными к настоящему
    .claude/hooks/log_event.py, показал ОБРАТНОЕ: поле "permission_mode"
    (значение "bypassPermissions") реально пришло в UserPromptSubmit,
    PostToolUse И PostToolUseFailure — то есть проверка "если есть
    permission_mode — это Codex" ошибочно помечала бы Claude Code как Codex
    и путала бы ok/duration_ms/error_text местами (это баг, пойманный именно
    так — не гипотетически). "model" в тех же живых Claude Code payload'ах
    не встретилось ни разу — но после промаха с permission_mode доверять
    этому наблюдению как единственному признаку тоже неправильно: у полей
    hook payload нет версионированной, документированной схемы, на которую
    можно опереться железно.

    Поэтому единственный признак, который реально используется —
    имя файла транскрипта (docs/codex-facts.md, раздел 2, строка "Путь к
    транскрипту"): у Codex раскладка
    $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<id>.jsonl — файл ВСЕГДА
    называется с префиксом "rollout-". У Claude Code файл — плоский
    <session_id>.jsonl без этого префикса, в другой раскладке каталогов
    (~/.claude/projects/<slug>/). Это не поле hook-события, а факт о том,
    какой движок физически писал файл на диск — устойчивее, чем присутствие
    очередного поля в JSON, которое конкретная версия клиента может как
    добавить, так и убрать. transcript_path присутствует в КАЖДОМ
    проверенном типе события обоих движков (SessionStart, UserPromptSubmit,
    PostToolUse, SessionEnd, PostToolUseFailure).

    turn_id как ДОПОЛНИТЕЛЬНАЯ подсказка используется только когда
    transcript_path вообще отсутствует (пустая строка) — на живых данных
    обоих движков такого не встречалось, это защита на случай битого
    события, а не основной путь.

    РЕВЬЮ-НАХОДКА 4 (см. отчёт задачи): раньше «ничего не сработало» молча
    возвращало ENGINE_CLAUDE — тот же класс ошибки, из-за которого
    переписывалось определение движка чуть выше (permission_mode), только
    слоем ниже: там ошибочно СЧИТАЛИ Codex-ом то, что было Claude Code,
    здесь — ошибочно СЧИТАЛИ Claude Code тем, что не опознано вовсе. ENGINE
    CLAUDE возвращается ТОЛЬКО при позитивном совпадении с известным путём
    Claude Code (`.claude/projects/` в transcript_path — см.
    _CLAUDE_PATH_MARKER). Если ни один из известных форматов не совпал —
    ENGINE_UNKNOWN, отдельное видимое в данных состояние, а не тихая
    подстановка. Существующие строки, вставленные до этой колонки, всё
    равно помечаются 'claude' в схеме (DEFAULT 'claude', sql/schema.sql) —
    это факт про историю данных (движка Codex тогда не было), а не про то,
    как detect_engine() должен угадывать на будущих, непонятных событиях.
    """
    transcript_path = payload.get("transcript_path") or ""
    if transcript_path:
        if os.path.basename(transcript_path).startswith("rollout-"):
            return ENGINE_CODEX
        if _CLAUDE_PATH_MARKER in transcript_path:
            return ENGINE_CLAUDE
        # Путь есть, но не похож ни на один из двух известных форматов —
        # не гадаем.
        return ENGINE_UNKNOWN
    if _CODEX_TURN_ID_KEY in payload:
        return ENGINE_CODEX
    return ENGINE_UNKNOWN


def _claude_duration_ms(payload):
    """Число миллисекунд или 0 — нечисловое/отсутствующее значение не должно
    ронять всё событие, деградируем только это поле. Устоявшееся, покрытое
    тестами поведение (.claude/hooks/log_event.py, tests/test_log_event.py):
    у Claude Code поле duration_ms в схеме СУЩЕСТВУЕТ (в отличие от Codex,
    где его нет в принципе) — просто конкретное значение может не прийти
    или быть битым. Это другая категория "отсутствия", чем у Codex, поэтому
    два движка обрабатываются по-разному, а не одним общим "если нет — 0"."""
    try:
        return int(payload.get("duration_ms", 0) or 0)
    except (TypeError, ValueError):
        return 0


def normalize(payload):
    """Привести сырое событие хука к общему виду.

    Возвращает dict с полями: session_id, transcript_path, tool_name,
    error_text, duration_ms, engine. Для Codex duration_ms — всегда None
    (поля нет в принципе, см. таблицу соответствия в докстринге модуля) и
    error_text — всегда "" (текст ошибки не приходит в hook payload Codex
    ни при каком событии; его достаёт отдельно lib/transcript_codex.py по
    transcript_path — вызывающий код делает это сам, когда он ему нужен,
    normalize() не читает файлы с диска). Для ENGINE_UNKNOWN — та же
    консервативная пара (None/"") : раз не опознали, по какой из двух схем
    разбирать событие, применять ни ту, ни другую нельзя — это было бы той
    же угадайкой, что и раньше молчаливое сведение к Claude Code."""
    engine = detect_engine(payload)
    tool_name = payload.get("tool_name", "") or ""
    session_id = payload.get("session_id", "") or ""
    transcript_path = payload.get("transcript_path", "") or ""

    if engine == ENGINE_CLAUDE:
        duration_ms = _claude_duration_ms(payload)
        # Поле называется "error", не "tool_error" — схема события
        # PostToolUseFailure зашита в самом Claude Code (см. комментарий в
        # .claude/hooks/log_event.py). Поля tool_error не существует.
        error_text = payload.get("error", "") or ""
    else:
        # ENGINE_CODEX или ENGINE_UNKNOWN — см. докстринг выше.
        duration_ms = None
        error_text = ""

    return {
        "session_id": session_id,
        "transcript_path": transcript_path,
        "tool_name": tool_name,
        "error_text": error_text,
        "duration_ms": duration_ms,
        "engine": engine,
    }
