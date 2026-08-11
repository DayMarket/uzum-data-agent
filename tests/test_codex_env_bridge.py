"""Тесты на мостик окружения для Codex (connectors/codex_env_bridge.py).

Находка живой приёмки: под Codex шесть коннекторов из девяти стартовали без
переменных. `.mcp.json` переименовывает переменные сам
(`"JIRA_PERSONAL_TOKEN": "${JIRA_TOKEN}"`), а `.codex/config.toml` не может —
у Codex есть только `env_vars = ["ИМЯ"]`, «переслать под тем же именем»
(docs/codex-facts.md, раздел 4). Значит целевые имена обязано выставить то,
что запускает codex. Здесь проверяется, что оно это делает — и делает
ровно по реестру.

Каждый тест ниже писался так, чтобы он ПАДАЛ на сломанном коде. Проверяемые
мутации (все три прогонялись руками, см. отчёт задачи):
  1. мостик не применяется вовсе;
  2. `default` не подставляется;
  3. `source` и `target` перепутаны местами.

Поэтому тут нет ни одной заглушки, которая «отдаёт то, что от неё ждут»:
значения-сентинелы привязаны к именам ИСТОЧНИКОВ (`val:JIRA_TOKEN`), так
что попадание значения не под тем именем видно сразу; ожидания на дефолты
записаны точным словарём (лишнее имя — тоже провал); а сквозной тест
запускает мостик настоящим процессом и смотрит на окружение ребёнка, а не
на возврат функции.

Секретов в файле нет — только имена переменных и выдуманные значения.
"""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import render_configs  # noqa: E402
from connectors import codex_env_bridge  # noqa: E402
from connectors.registry import CONNECTORS, Connector, EnvVar, StaticEnv  # noqa: E402

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - тесты гоняются под 3.12
    tomllib = None

BRIDGE_PATH = REPO_ROOT / "connectors" / "codex_env_bridge.py"


def _codex_env_items(connector):
    """Переменные, которые реально нужны коннектору под Codex: своя ветка
    (`connector.codex`), если она есть, иначе общая с Claude Code.

    Намеренная копия развилки из реестра — тест обязан знать, ЧТО должно
    получиться, независимо от того, как это устроено внутри (тот же приём и
    та же причина, что у `_codex_spec` в tests/test_render_configs.py)."""
    env = connector.codex.env if connector.codex is not None else connector.env
    return [item for item in env if isinstance(item, EnvVar)]


def _every_source_filled():
    """Окружение, где у КАЖДОГО источника своё узнаваемое значение.

    Значение привязано к имени источника (`val:JIRA_TOKEN`), а не к имени
    цели — на этом ловится перепутанное направление: при обмене source и
    target значения либо исчезнут, либо приедут не под теми именами."""
    env = {}
    for connector in CONNECTORS:
        for item in _codex_env_items(connector):
            env[item.source] = "val:%s" % item.source
    return env


# ── переименование ────────────────────────────────────────────────────────

# Таблица из брифа, дословно: шесть коннекторов, которым под Codex не
# доезжали переменные. Записана здесь литералами, а не выведена из реестра:
# тест должен провалиться и в том случае, если кто-то «поправит» реестр так,
# что имя контракта чужого пакета изменится незамеченным.
BROKEN_ON_ACCEPTANCE = {
    "JIRA_PERSONAL_TOKEN": "JIRA_TOKEN",
    "CONFLUENCE_PERSONAL_TOKEN": "JIRA_TOKEN",
    "GOOGLE_SERVICE_ACCOUNT_FILE": "GOOGLE_SA_FILE",
    "GRAFANA_SERVICE_ACCOUNT_TOKEN": "GRAFANA_TOKEN",
    "OPENMETADATA_URI": "OMD_URL",
    "OPENMETADATA_JWT_TOKEN": "OMD_TOKEN",
    "GB_API_KEY": "GROWTHBOOK_TOKEN",
}


def test_renamed_variables_from_the_acceptance_table_reach_their_target_names():
    """Именно то, что не работало на приёмке: значение, лежащее у аналитика
    под нашим именем, обязано оказаться в окружении под именем, которого
    ждёт чужой пакет."""
    overlay = codex_env_bridge.codex_env_overlay(_every_source_filled())

    for target, source in BROKEN_ON_ACCEPTANCE.items():
        assert target in overlay, (
            "%s не выставлен — процесс коннектора не получит значение из %s"
            % (target, source)
        )
        assert overlay[target] == "val:%s" % source, (
            "%s получил %r, а должен значение источника %s"
            % (target, overlay[target], source)
        )


def test_every_variable_of_every_connector_is_bridged():
    """Тот же контракт, но по всему реестру, а не только по шести из брифа:
    ни одна переменная не должна потеряться при добавлении коннектора."""
    source_env = _every_source_filled()
    overlay = codex_env_bridge.codex_env_overlay(source_env)

    for connector in CONNECTORS:
        for item in _codex_env_items(connector):
            assert item.target in overlay, (
                "коннектор %s: %s не выставлен" % (connector.id, item.target)
            )
            assert overlay[item.target] == source_env[item.source], (
                "коннектор %s: %s = %r, ожидалось значение источника %s (%r)"
                % (connector.id, item.target, overlay[item.target],
                   item.source, source_env[item.source])
            )


def test_source_names_are_not_left_lying_around_under_their_own_names():
    """Обратная сторона того же: мостик не должен «на всякий случай»
    дублировать значение ещё и под именем источника. Имена источников уже
    есть в окружении (их кладёт bin/uzum из secrets.env), и `env_vars` в
    config.toml их не просит — лишнее имя в накладке означало бы, что
    направление переименования потеряно."""
    overlay = codex_env_bridge.codex_env_overlay(_every_source_filled())

    for target, source in BROKEN_ON_ACCEPTANCE.items():
        assert source not in overlay, (
            "мостик выставляет %s (имя источника) — переименование в %s "
            "потерялось или сделано в обе стороны" % (source, target)
        )


# ── дефолты ───────────────────────────────────────────────────────────────

# Всё, что аналитик имеет право не заполнять: адреса Jira/Confluence/Trino/
# Superset, каталог Trino, порт и признак https обоих кластеров ClickHouse.
# Точный словарь, а не «содержит»: лишнее имя здесь означало бы выдуманное
# значение там, где его быть не должно (в первую очередь — у секрета).
#
# SUPERSET_URL и GRAFANA_URL попали сюда позже остальных и по той же
# причине, что и JIRA_URL: адрес один на всю компанию, мастер подставляет его
# сам. Пока дефолта не было, connector_readiness числил переменную
# обязательной, и в итоге установки человек читал «grafana — нет GRAFANA_URL,
# …» про значение, которого у него никто не спрашивал.
DEFAULTS_ON_EMPTY_ENVIRONMENT = {
    "JIRA_URL": "https://jira.uzum.com",
    "GRAFANA_URL": "https://ops-grafana.prod.cluster.daymarket.uz",
    "CONFLUENCE_URL": "https://confluence.uzum.com",
    "TRINO_HOST": "trino.prod-data.internal.daymarket.uz",
    "TRINO_CATALOG": "dwh-iceberg",
    "CH_WMS_PORT": "8123",
    "CH_WMS_SECURE": "false",
    "CH_DWH_PORT": "8123",
    "CH_DWH_SECURE": "false",
    "SUPERSET_URL": "https://bi.uzum.uz",
}


def test_defaults_are_substituted_when_nothing_is_configured():
    assert codex_env_bridge.codex_env_overlay({}) == DEFAULTS_ON_EMPTY_ENVIRONMENT


def test_empty_value_is_treated_as_absent_and_falls_back_to_the_default():
    """`JIRA_URL=` в secrets.env — это «не заполнено», а не «адрес пустой».
    Без этого mcp-atlassian получил бы пустую строку и не поднялся."""
    overlay = codex_env_bridge.codex_env_overlay({"JIRA_URL": "", "TRINO_CATALOG": ""})

    assert overlay["JIRA_URL"] == "https://jira.uzum.com"
    assert overlay["TRINO_CATALOG"] == "dwh-iceberg"


def test_a_real_value_wins_over_the_default():
    overlay = codex_env_bridge.codex_env_overlay({"JIRA_URL": "https://jira.internal"})

    assert overlay["JIRA_URL"] == "https://jira.internal"


def test_no_secret_is_ever_invented():
    """Дефолт у секрета невозможен по построению (registry.EnvVar это
    проверяет) — мостик не должен становиться лазейкой, через которую
    секрет получает значение не от аналитика."""
    overlay = codex_env_bridge.codex_env_overlay({})

    secret_targets = {item.target
                      for connector in CONNECTORS
                      for item in _codex_env_items(connector) if item.secret}
    assert secret_targets, "в реестре не осталось секретов — тест потерял смысл"
    assert not (secret_targets & set(overlay)), (
        "секрет получил значение на пустом окружении: %s"
        % sorted(secret_targets & set(overlay))
    )


def test_missing_value_without_default_sets_nothing_at_all():
    """Пустой токен в окружении — это «доступ есть, но не работает»:
    коннектор поднимется и будет отвечать отказами вместо честного «не
    настроен». Имени не должно быть вовсе."""
    overlay = codex_env_bridge.codex_env_overlay({"GRAFANA_TOKEN": ""})

    assert "GRAFANA_SERVICE_ACCOUNT_TOKEN" not in overlay


# ── источник значений — только исходное окружение ─────────────────────────

def test_values_come_from_the_original_environment_not_from_the_result():
    """Цель одного коннектора может совпасть с источником другого. Если
    мостик читает уже собранный результат, значение уедет по цепочке, а
    порядок коннекторов в реестре станет частью контракта — молча.

    Синтетический реестр: первый коннектор пишет B из C, второй читает B.
    Правильный ответ — A = «то, что лежало в B ДО накладки»."""
    fake = (
        Connector(id="first", command="x", args=(),
                  env=(EnvVar(target="B", source="C", secret=False),)),
        Connector(id="second", command="x", args=(),
                  env=(EnvVar(target="A", source="B", secret=False),)),
    )

    overlay = codex_env_bridge.codex_env_overlay(
        {"B": "было-в-B", "C": "было-в-C"}, connectors=fake)

    assert overlay == {"B": "было-в-C", "A": "было-в-B"}


def test_the_overlay_does_not_mutate_the_environment_it_was_given():
    source_env = {"JIRA_TOKEN": "t"}
    codex_env_bridge.codex_env_overlay(source_env)

    assert source_env == {"JIRA_TOKEN": "t"}


# ── столкновение имён ─────────────────────────────────────────────────────

def test_two_connectors_asking_for_the_same_name_from_different_sources_is_an_error():
    """Окружение у процесса codex одно на все MCP-серверы: два разных
    значения под одним именем — это молча проигравший коннектор (ровно то,
    из-за чего clickhouse-wms/clickhouse-dwh получили отдельную ветку
    запуска). Такое должно падать при запуске, а не «как-нибудь
    разрешиться»."""
    fake = (
        Connector(id="one", command="x", args=(),
                  env=(EnvVar(target="TOKEN", source="A_TOKEN", secret=True),)),
        Connector(id="two", command="x", args=(),
                  env=(EnvVar(target="TOKEN", source="B_TOKEN", secret=True),)),
    )

    with pytest.raises(codex_env_bridge.ConflictingTargets) as exc:
        codex_env_bridge.codex_env_overlay({"A_TOKEN": "a", "B_TOKEN": "b"},
                                           connectors=fake)

    message = str(exc.value)
    assert "TOKEN" in message and "one" in message and "two" in message


def test_the_same_name_from_the_same_source_is_not_a_conflict():
    """Обратная сторона: страж не должен запрещать законное. Один и тот же
    PAT под одним именем у двух коннекторов — обычное дело (JIRA_TOKEN уже
    так и используется дважды внутри atlassian)."""
    fake = (
        Connector(id="one", command="x", args=(),
                  env=(EnvVar(target="URL", source="SHARED_URL", secret=False,
                              default="https://example"),)),
        Connector(id="two", command="x", args=(),
                  env=(EnvVar(target="URL", source="SHARED_URL", secret=False,
                              default="https://example"),)),
    )

    assert codex_env_bridge.codex_env_overlay({}, connectors=fake) == {
        "URL": "https://example"}


def test_the_real_registry_has_no_conflicts():
    """Тот же страж на живом реестре — чтобы правка registry.py, вводящая
    столкновение, падала здесь, а не у аналитика в сессии."""
    codex_env_bridge.codex_env_overlay(_every_source_filled())


# ── граница с clickhouse_proxy.py ─────────────────────────────────────────

def test_clickhouse_is_bridged_through_its_own_wrapper_names_not_clickhouse_star():
    """`clickhouse_proxy.py` переименовывает CH_WMS_*/CH_DWH_* в
    CLICKHOUSE_* внутри своего процесса — по одному процессу на кластер.
    Если бы CLICKHOUSE_* появился в общем окружении codex, оба кластера
    получили бы одно значение и второй стал бы недоступен молча. Мостик
    обязан это не дублировать: он читает Codex-ветку реестра, где
    target == source."""
    overlay = codex_env_bridge.codex_env_overlay(_every_source_filled())

    leaked = [name for name in overlay if name.startswith("CLICKHOUSE_")]
    assert not leaked, (
        "мостик выставил %s в общем окружении codex — это ровно то "
        "столкновение двух кластеров, ради которого сделана обёртка" % leaked
    )
    assert overlay["CH_WMS_HOST"] == "val:CH_WMS_HOST"
    assert overlay["CH_DWH_HOST"] == "val:CH_DWH_HOST"


# ── согласие с тем, что реально просит config.toml ────────────────────────

def test_every_name_codex_config_asks_for_is_a_name_the_bridge_sets():
    """Настоящая проводка: `env_vars` в config.toml — это список имён,
    которые Codex ищет В СВОЁМ ОКРУЖЕНИИ. Если конфиг просит одно, а мостик
    кладёт другое, всё выглядит настроенным и не работает — тот самый
    дефект. Здесь два независимо построенных набора имён сверяются между
    собой на полном окружении."""
    assert tomllib is not None, "тест требует Python 3.11+ — гоняй через uv run --python 3.12"
    config = tomllib.loads(render_configs.render_codex_toml(CONNECTORS))
    overlay = codex_env_bridge.codex_env_overlay(_every_source_filled())

    asked = set()
    for server in config["mcp_servers"].values():
        asked.update(server.get("env_vars", []))
    assert asked, "в конфиге Codex не оказалось ни одного env_vars — тест потерял смысл"

    missing = sorted(asked - set(overlay))
    assert not missing, (
        "config.toml просит имена, которых мостик не выставляет: %s" % missing
    )


def test_static_env_stays_in_the_config_and_out_of_the_bridge():
    """StaticEnv (MCP_TRANSPORT=stdio, CLICKHOUSE_VERIFY=false и т.п.) —
    литералы, их пишет сам config.toml в `[mcp_servers.<id>.env]` каждому
    процессу отдельно. В общем окружении процесса codex им делать нечего:
    CLICKHOUSE_VERIFY, попавший туда, — это переменная одного коннектора,
    видимая всем остальным."""
    overlay = codex_env_bridge.codex_env_overlay(_every_source_filled())

    static_names = {item.name
                    for connector in CONNECTORS
                    for branch in (connector.env,
                                   connector.codex.env if connector.codex else ())
                    for item in branch
                    if isinstance(item, StaticEnv)}
    assert static_names, "в реестре не осталось StaticEnv — тест потерял смысл"
    assert not (static_names & set(overlay))


# ── сквозной запуск: настоящий процесс, настоящее окружение ───────────────

def _env_dump_stub(path, dump_to):
    """Подставной «codex»: печатать ничего не нужно — он записывает СВОЁ
    окружение и СВОЙ argv в файл. Проверяем потом по файлу: это факт о
    запущенном процессе, а не то, что заглушка согласилась вернуть."""
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump({'env': dict(os.environ), 'argv': sys.argv},\n"
        "          open(%r, 'w', encoding='utf-8'))\n" % str(dump_to),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_bridge_execs_the_command_with_the_renamed_environment(tmp_path):
    """Сквозь настоящий процесс: мостик запускает то, что ему передали, и
    ребёнок видит целевые имена. Заодно — что остальное окружение (PATH и
    прочее) доехало: без него codex просто не стартует."""
    dump = tmp_path / "dump.json"
    stub = tmp_path / "fake-codex"
    _env_dump_stub(stub, dump)

    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
           "JIRA_TOKEN": "секрет с пробелом и $знаком",
           "OMD_URL": "https://omd.internal", "OMD_TOKEN": "omd-tok"}
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "--", str(stub), "-p", "uzum"],
        env=env, capture_output=True, text=True, timeout=60,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    child = json.loads(dump.read_text(encoding="utf-8"))
    child_env = child["env"]

    assert child_env["JIRA_PERSONAL_TOKEN"] == "секрет с пробелом и $знаком"
    assert child_env["CONFLUENCE_PERSONAL_TOKEN"] == "секрет с пробелом и $знаком"
    assert child_env["JIRA_URL"] == "https://jira.uzum.com"
    assert child_env["OPENMETADATA_URI"] == "https://omd.internal"
    assert child_env["OPENMETADATA_JWT_TOKEN"] == "omd-tok"
    assert child_env["PATH"] == env["PATH"], "мостик потерял PATH"
    assert child["argv"][1:] == ["-p", "uzum"], (
        "аргументы запуска изменились: %r" % (child["argv"],))


def test_bridge_never_puts_values_into_the_command_line(tmp_path):
    """Секрет в argv виден любому `ps` того же пользователя — это уже
    учтено в curl_check мастера установки и не должно вернуться здесь.
    Значения уходят через envp, argv остаётся тем, что передали."""
    dump = tmp_path / "dump.json"
    stub = tmp_path / "fake-codex"
    _env_dump_stub(stub, dump)

    token = "argv-canary-9d3f1"
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "JIRA_TOKEN": token}
    subprocess.run([sys.executable, str(BRIDGE_PATH), "--", str(stub)],
                   env=env, capture_output=True, text=True, timeout=60)

    child = json.loads(dump.read_text(encoding="utf-8"))
    assert child["env"]["JIRA_PERSONAL_TOKEN"] == token, "мостик не сработал вовсе"
    assert not any(token in arg for arg in child["argv"]), (
        "значение попало в командную строку: %r" % (child["argv"],))


def test_bridge_reports_a_command_it_cannot_start(tmp_path):
    """Если codex не нашёлся — честная ошибка и ненулевой код, а не тишина
    (тот же принцип, что и во всех развилках bin/uzum)."""
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH), "--",
         str(tmp_path / "no-such-binary-4f2a")],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "no-such-binary-4f2a" in result.stderr


def test_bridge_without_a_command_explains_itself(tmp_path):
    result = subprocess.run(
        [sys.executable, str(BRIDGE_PATH)],
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )

    assert result.returncode != 0
    assert "Использование" in result.stderr


def test_the_registry_is_read_at_call_time_not_frozen_at_import(monkeypatch):
    """Сторож против формы, которая уже стоила одного круга правок.

    `def codex_env_overlay(source_env, connectors=CONNECTORS)` выглядит
    настраиваемым, но умолчание-выражение вычисляется ОДИН раз при импорте:
    подмена `codex_env_bridge.CONNECTORS` после этого ни на что не влияет.
    В `log_session.clickhouse_users` ровно такая сигнатура прятала дефект
    полгода — тест подменял путь к secrets.env, подмена молча не
    действовала, читался личный файл владельца, и утверждение теста не
    проверялось никогда.

    Здесь дефекта не было: реестр берётся из исходников репозитория, а не с
    машины. Но форма та же, и наступить в неё можно завтра. Тест падает,
    если умолчание вернут в сигнатуру."""
    fake = (
        Connector(id="только-в-подменённом-реестре", command="x", args=(),
                  env=(EnvVar(target="ЦЕЛЬ", source="ИСТОЧНИК", secret=False),)),
    )
    monkeypatch.setattr(codex_env_bridge, "CONNECTORS", fake)

    overlay = codex_env_bridge.codex_env_overlay({"ИСТОЧНИК": "значение"})

    assert overlay == {"ЦЕЛЬ": "значение"}, (
        "мостик взял реестр, зафиксированный при импорте, а не текущий")
