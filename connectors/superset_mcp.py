#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "httpx>=0.27",
# ]
# ///
"""Superset MCP Server — локальный прокси к Superset REST API со входом через Keycloak SSO.

Перенесено из харнесса opencode-lite-bundle (shared/mcp_local/superset-mcp.py).
Логика REST-запросов к Superset и хендрождённый JSON-RPC цикл по stdio не
менялись — только загрузка учётных данных: харнесс читал их из macOS Keychain /
Windows Credential Manager, мы читаем из ~/.config/uzum-ai/secrets.env (ключи
SUPERSET_USERNAME/SUPERSET_PASSWORD), как и остальные секреты в этом репозитории.

Единственная внешняя зависимость — httpx (для авторизации у Superset нужен
persistent cookie jar с follow_redirects, которого нет в stdlib). Claude Code
запускает файл через `uv run` (см. PEP 723 заголовок выше) — uv разворачивает
изолированное окружение под httpx сам, без ручного pip install.

Задача Codex-5 (разрешения): у 13 читающих инструментов (тот же набор, что
уже одобрен для Claude Code в .claude/settings.json — refresh_token,
list_dashboards, get_dashboard, list_charts, list_dashboard_charts,
get_chart_params_summary, get_chart_data, get_dashboard_layout_summary,
get_dataset, get_dataset_summary, list_datasets, get_chart_screenshot,
get_dashboard_screenshot) в get_tools() добавлен ключ
`"annotations": {"readOnlyHint": True}`. Это единственное, что Codex смотрит
при решении — спрашивать ли подтверждение на вызов MCP-инструмента
(проверено живым запуском, см. tests/test_codex_permissions.py и отчёт
задачи Codex-5): без этого поля любой вызов, включая безобидное
перечисление, требует подтверждения. У sql_query и у всех пишущих
инструментов (create_*/update_*/delete/patch_dashboard_position/
normalize_dashboard_metrics) этого поля нет и не должно быть — правило 1
(любой SQL и любое изменение требует подтверждения человека) держится
именно на его отсутствии.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from typing import Any
from html.parser import HTMLParser

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib"))

import envfile  # noqa: E402

SECRETS_PATH = os.path.expanduser("~/.config/uzum-ai/secrets.env")


def _read_secrets_env(path):
    """Прочитать файл секретов. Отсутствие файла — не ошибка.

    Формат значений един для всего репозитория (одинарные кавычки с
    экранированием, см. lib/envfile.py) — разбор берём оттуда, чтобы у
    коннектора не оказалось своего мнения о том, где кончается значение.
    """
    return envfile.read(path)


async def read_message():
    line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
    if not line:
        return None
    return json.loads(line)


async def write_message(message: dict):
    await asyncio.get_event_loop().run_in_executor(
        None, lambda: print(json.dumps(message), flush=True)
    )


def _make_nullable(sub: dict) -> dict:
    if not isinstance(sub, dict):
        return sub
    t = sub.get("type")
    if isinstance(t, list):
        if "null" not in t:
            sub = {**sub, "type": [*t, "null"]}
    elif isinstance(t, str) and t != "null":
        sub = {**sub, "type": [t, "null"]}
    elif t is None and isinstance(sub.get("anyOf"), list):
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in sub["anyOf"]):
            sub = {**sub, "anyOf": [*sub["anyOf"], {"type": "null"}]}
    return sub


def _strictify_schema(schema: dict) -> dict:
    """Make a JSON Schema OpenAI strict-compatible.

    The cliproxy codex/gpt-5.5 path forces Structured Outputs (strict=true) on the
    Responses API, which rejects any object lacking additionalProperties:false or
    whose `required` omits a property (HTTP 400 invalid_function_parameters) — so
    gpt-5.5 could not call ANY superset tool. We close every object and mark all
    keys required; originally-optional keys become nullable so the model can pass
    null, and tools/call strips null so the Python defaults still apply. Anthropic
    (opus/gemini) accepts this same shape, so both providers keep working.
    """
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    for combiner in ("anyOf", "oneOf", "allOf"):
        if isinstance(s.get(combiner), list):
            s[combiner] = [_strictify_schema(sub) for sub in s[combiner]]
    if s.get("type") == "object" or "properties" in s:
        props = s.get("properties", {}) or {}
        orig_required = set(s.get("required", []) or [])
        s["properties"] = {
            k: (_strictify_schema(v) if k in orig_required else _make_nullable(_strictify_schema(v)))
            for k, v in props.items()
        }
        s["required"] = list(props.keys())
        s["additionalProperties"] = False
    if s.get("type") == "array" and isinstance(s.get("items"), dict):
        s["items"] = _strictify_schema(s["items"])
    return s


class _FormActionParser(HTMLParser):
    """Extract the first <form action="..."> from Keycloak HTML."""
    def __init__(self):
        super().__init__()
        self.action: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "form" and self.action is None:
            for name, value in attrs:
                if name == "action":
                    self.action = value
                    break


class SupersetMCPServer:
    def __init__(self):
        self.base_url = os.getenv("SUPERSET_URL", "").rstrip("/")
        if not self.base_url:
            raise ValueError("SUPERSET_URL environment variable is required")
        # Superset 6 no longer 302s /login/ straight to the IdP; the OAuth flow starts at the
        # provider-specific /login/<provider> endpoint (default keycloak here).
        self.oauth_provider = os.getenv("SUPERSET_OAUTH_PROVIDER", "keycloak")

        self.username, self.password = self._load_credentials()

        self.cookie_file = Path(
            os.getenv("SUPERSET_COOKIE_FILE", os.path.expanduser("~/.superset_cookies"))
        )
        self.api = f"{self.base_url}/api/v1"
        self._client: httpx.AsyncClient | None = None

    def _load_credentials(self) -> tuple[str, str]:
        """Username/password из окружения, а если там пусто — из secrets.env.

        В харнессе эти же значения читались из macOS Keychain / Windows Credential
        Manager (security find-generic-password / PowerShell CredRead). У нас один
        секретный файл для всех коннекторов — ~/.config/uzum-ai/secrets.env, — так
        что читаем оттуда те же ключи SUPERSET_USERNAME/SUPERSET_PASSWORD.
        """
        user = os.getenv("SUPERSET_USERNAME", "").strip()
        pwd = os.getenv("SUPERSET_PASSWORD", "")
        if pwd:
            return user, pwd
        secrets = _read_secrets_env(SECRETS_PATH)
        return (user or secrets.get("SUPERSET_USERNAME", "").strip()), (pwd or secrets.get("SUPERSET_PASSWORD", ""))

    async def _get_client(self) -> httpx.AsyncClient:
        """Return a persistent client with cookies."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=False,
                timeout=120,
                follow_redirects=True,
                cookies=self._load_cookies(),
            )
        return self._client

    def _load_cookies(self) -> httpx.Cookies:
        cookies = httpx.Cookies()
        if self.cookie_file.exists():
            try:
                data = json.loads(self.cookie_file.read_text())
                for name, value in data.items():
                    cookies.set(name, value, domain=self._domain())
            except Exception:
                pass
        return cookies

    def _save_cookies(self, client: httpx.AsyncClient):
        cookie_dict = {}
        for cookie in client.cookies.jar:
            cookie_dict[cookie.name] = cookie.value
        self.cookie_file.write_text(json.dumps(cookie_dict))

    def _domain(self) -> str:
        from urllib.parse import urlparse
        return urlparse(self.base_url).hostname or ""

    async def _login(self) -> bool:
        """Full OAuth2 Authorization Code flow through Keycloak."""
        client = await self._get_client()

        # Step 1: GET /login/<provider> to trigger the OAuth2 redirect to Keycloak
        r = await client.get(f"{self.base_url}/login/{self.oauth_provider}")
        keycloak_html = r.text
        final_url = str(r.url)

        if "keycloak" not in final_url and "login-actions" not in final_url:
            if r.status_code == 200 and "/welcome" in final_url:
                self._save_cookies(client)
                return True

        # Step 2: Extract form action from Keycloak HTML
        parser = _FormActionParser()
        parser.feed(keycloak_html)
        form_action = parser.action
        if not form_action:
            raise ValueError("Could not find Keycloak login form action")
        form_action = form_action.replace("&amp;", "&")

        # Step 3: Submit credentials (don't auto-follow the POST redirect)
        login_client = httpx.AsyncClient(
            verify=False, timeout=30, follow_redirects=False,
            cookies=client.cookies,
        )
        try:
            r2 = await login_client.post(
                form_action,
                data={"username": self.username, "password": self.password},
            )

            if r2.status_code not in (301, 302, 303):
                if "Invalid username or password" in r2.text:
                    raise ValueError("Keycloak: Invalid username or password")
                raise ValueError(f"Keycloak login unexpected status {r2.status_code}")

            redirect_url = r2.headers.get("location", "")
            for cookie in login_client.cookies.jar:
                client.cookies.set(cookie.name, cookie.value, domain=cookie.domain)
        finally:
            await login_client.aclose()

        # Step 4: Follow the redirect back to Superset /authorize with GET
        r3 = await client.get(redirect_url)
        if r3.status_code == 200:
            self._save_cookies(client)
            return True

        raise ValueError(f"OAuth callback failed: {r3.status_code} at {r3.url}")

    async def _ensure_session(self):
        """Ensure we have a valid session, login if needed."""
        client = await self._get_client()
        try:
            # Superset 6 returns 403 on /api/v1/me/ for SSO sessions, so probe a normal
            # authenticated endpoint instead to decide whether the session is still valid.
            r = await client.get(f"{self.api}/dashboard/?q=(page_size:1)")
            if r.status_code == 200:
                return
        except Exception:
            pass
        await self._login()

    def _headers(self) -> dict:
        return {"Accept": "application/json"}

    @staticmethod
    def _compact_chart_params(params: Any) -> dict[str, Any]:
        """Return high-signal chart params without dumping full form_data."""
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                return {"params_chars": len(params)}
        if not isinstance(params, dict):
            return {}

        keys = (
            "datasource",
            "viz_type",
            "metric",
            "metrics",
            "groupby",
            "groupbyRows",
            "groupbyColumns",
            "columns",
            "all_columns",
            "time_range",
            "row_limit",
            "server_page_length",
            "y_axis_format",
            "valueFormat",
        )
        summary = {k: params[k] for k in keys if k in params}
        filters = params.get("adhoc_filters")
        if isinstance(filters, list):
            summary["adhoc_filters_count"] = len(filters)
        summary["param_keys"] = len(params)
        return summary

    @staticmethod
    def _loads_json_maybe(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return default
        return value

    @staticmethod
    def _chart_id_from_component(component: dict[str, Any]) -> int | None:
        meta = component.get("meta", {}) if isinstance(component, dict) else {}
        chart_id = meta.get("chartId")
        return chart_id if isinstance(chart_id, int) else None

    def _layout_invariants(
        self,
        position: dict[str, Any],
        component_ids: set[str] | None = None,
        chart_ids: set[int] | None = None,
    ) -> list[str]:
        """Compact layout readback for affected components only."""
        parent: dict[str, str] = {}
        for cid, component in position.items():
            if not isinstance(component, dict):
                continue
            for child in component.get("children", []) or []:
                parent[child] = cid

        target_components = set(component_ids or set())
        if chart_ids:
            for cid, component in position.items():
                if not isinstance(component, dict):
                    continue
                if component.get("type") == "CHART" and self._chart_id_from_component(component) in chart_ids:
                    target_components.add(cid)
                    col_id = parent.get(cid)
                    row_id = parent.get(col_id or "")
                    if col_id:
                        target_components.add(col_id)
                    if row_id:
                        target_components.add(row_id)

        rows: set[str] = set()
        for cid in target_components:
            component = position.get(cid)
            if not isinstance(component, dict):
                continue
            ctype = component.get("type")
            if ctype == "ROW":
                rows.add(cid)
            elif ctype == "COLUMN":
                row_id = parent.get(cid)
                if row_id:
                    rows.add(row_id)
            elif ctype == "CHART":
                col_id = parent.get(cid)
                row_id = parent.get(col_id or "")
                if row_id:
                    rows.add(row_id)
            elif ctype == "TAB":
                rows.update(
                    child for child in component.get("children", []) or []
                    if isinstance(position.get(child), dict) and position[child].get("type") == "ROW"
                )

        lines: list[str] = []
        for row_id in sorted(rows):
            row = position.get(row_id, {})
            children = row.get("children", []) if isinstance(row, dict) else []
            width_sum = 0.0
            child_bits = []
            for child in children:
                col = position.get(child, {})
                if not isinstance(col, dict):
                    continue
                width = col.get("meta", {}).get("width", 0)
                if isinstance(width, (int, float)):
                    width_sum += float(width)
                chart_bits = []
                for chart_comp_id in col.get("children", []) or []:
                    chart = position.get(chart_comp_id, {})
                    if not isinstance(chart, dict) or chart.get("type") != "CHART":
                        continue
                    meta = chart.get("meta", {})
                    chart_bits.append(
                        f"{meta.get('chartId')}:{meta.get('height')}h/{meta.get('width')}w"
                    )
                child_bits.append(f"{child}:{width}w[{','.join(chart_bits)}]")
            lines.append(f"{row_id}: children={children}; width_sum={round(width_sum, 4)}; {', '.join(child_bits)}")
        return lines

    # ── REST helpers ──────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> dict:
        await self._ensure_session()
        client = await self._get_client()
        r = await client.get(f"{self.api}{path}", headers=self._headers(), params=params)
        if r.status_code == 401:
            await self._login()
            r = await client.get(f"{self.api}{path}", headers=self._headers(), params=params)
        r.raise_for_status()
        return r.json()

    async def _post(self, path: str, payload: dict | None = None) -> dict:
        await self._ensure_session()
        client = await self._get_client()
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        csrf_r = await client.get(f"{self.api}/security/csrf_token/")
        if csrf_r.status_code == 200:
            csrf_token = csrf_r.json().get("result", "")
            if csrf_token:
                headers["X-CSRFToken"] = csrf_token
                headers["Referer"] = self.base_url

        r = await client.post(f"{self.api}{path}", headers=headers, json=payload or {})
        if r.status_code == 401:
            await self._login()
            r = await client.post(f"{self.api}{path}", headers=headers, json=payload or {})
        r.raise_for_status()
        return r.json()

    async def _put(self, path: str, payload: dict | None = None) -> dict:
        await self._ensure_session()
        client = await self._get_client()
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        csrf_r = await client.get(f"{self.api}/security/csrf_token/")
        if csrf_r.status_code == 200:
            csrf_token = csrf_r.json().get("result", "")
            if csrf_token:
                headers["X-CSRFToken"] = csrf_token
                headers["Referer"] = self.base_url

        r = await client.put(f"{self.api}{path}", headers=headers, json=payload or {})
        if r.status_code == 401:
            await self._login()
            r = await client.put(f"{self.api}{path}", headers=headers, json=payload or {})
        r.raise_for_status()
        return r.json()

    async def _delete(self, path: str) -> dict:
        await self._ensure_session()
        client = await self._get_client()
        headers = self._headers()
        headers["Content-Type"] = "application/json"

        csrf_r = await client.get(f"{self.api}/security/csrf_token/")
        if csrf_r.status_code == 200:
            csrf_token = csrf_r.json().get("result", "")
            if csrf_token:
                headers["X-CSRFToken"] = csrf_token
                headers["Referer"] = self.base_url

        r = await client.delete(f"{self.api}{path}", headers=headers)
        if r.status_code == 401:
            await self._login()
            r = await client.delete(f"{self.api}{path}", headers=headers)
        r.raise_for_status()
        return r.json() if r.text else {}

    # ── Tool implementations ──────────────────────────────────────────

    async def refresh_token(self) -> str:
        """Force re-login via Keycloak SSO and return status."""
        try:
            self._client = None
            await self._login()
            client = await self._get_client()
            r = await client.get(f"{self.api}/dashboard/?q=(page_size:1)")
            if r.status_code == 200:
                return "Login successful (session verified via the dashboard API)."
            return f"Login completed but session check returned {r.status_code}"
        except Exception as e:
            return f"Login failed: {e}"

    async def list_dashboards(self, search: str | None = None, limit: int = 25) -> str:
        """List dashboards filtered by search query.

        Uses RISON q= param with filters to avoid 502 on heavy unfiltered list.
        A search term is required; unfiltered full list causes server timeout.
        """
        if not search:
            return (
                "Error: search term is required for list_dashboards — "
                "the unfiltered dashboard list causes server timeout (502). "
                "Provide a search keyword, or use superset_get_dashboard with a known ID."
            )
        safe = search.replace("'", "\\'")
        filters_rison = f",filters:!((col:dashboard_title,opr:ct,value:'{safe}'))"

        q = f"(page_size:{limit},page:0,order_column:changed_on_delta_humanized,order_direction:desc{filters_rison})"
        data = await self._get("/dashboard/", {"q": q})
        dashboards = data.get("result", [])
        total = data.get("count", len(dashboards))

        if not dashboards:
            suffix = f' matching "{search}"' if search else ""
            return f"No dashboards found{suffix}"

        suffix = f' matching "{search}"' if search else ""
        lines = [f"Found {total} dashboards{suffix} (showing {len(dashboards)}):\n"]
        for d in dashboards:
            did = d.get("id", "?")
            title = d.get("dashboard_title", "Untitled")
            slug = d.get("slug", "")
            changed = d.get("changed_on_delta_humanized", "")
            owners = ", ".join(
                f"{o.get('first_name', '')} {o.get('last_name', '')}".strip()
                for o in d.get("owners", [])
            )
            published = "Published" if d.get("published") else "Draft"
            url = f"{self.base_url}/superset/dashboard/{did}/"

            line = f"- **{title}** (id={did})"
            if slug:
                line += f" [slug: {slug}]"
            line += f" — {published}"
            if changed:
                line += f", {changed}"
            if owners:
                line += f"\n  Owners: {owners}"
            line += f"\n  [Open]({url})"
            lines.append(line)
        return "\n".join(lines)

    async def get_dashboard(self, id_or_slug: str) -> str:
        """Get dashboard details including its charts."""
        data = await self._get(f"/dashboard/{id_or_slug}")
        d = data.get("result", {})
        did = d.get("id", "?")
        title = d.get("dashboard_title", "Untitled")
        desc = d.get("description") or ""
        slug = d.get("slug", "")
        published = "Published" if d.get("published") else "Draft"
        css = d.get("css") or ""
        owners = ", ".join(
            f"{o.get('first_name', '')} {o.get('last_name', '')}".strip()
            for o in d.get("owners", [])
        )
        roles = ", ".join(r.get("name", "") for r in d.get("roles", []))
        url = f"{self.base_url}/superset/dashboard/{did}/"

        lines = [
            f"## Dashboard: {title}",
            f"**ID**: {did}",
        ]
        if slug:
            lines.append(f"**Slug**: {slug}")
        lines.append(f"**Status**: {published}")
        if desc:
            lines.append(f"**Description**: {desc}")
        if owners:
            lines.append(f"**Owners**: {owners}")
        if roles:
            lines.append(f"**Roles**: {roles}")
        lines.append(f"[Open in Superset]({url})")

        # Charts in this dashboard
        charts_data = await self._get(f"/dashboard/{did}/charts")
        charts = charts_data.get("result", [])
        if charts:
            lines.append(f"\n**Charts** ({len(charts)}):")
            for c in charts:
                cid = c.get("id", "?")
                cname = c.get("slice_name", c.get("chart_name", "Untitled"))
                viz = c.get("viz_type", "?")
                lines.append(f"- `{cname}` (id={cid}, type={viz})")

        return "\n".join(lines)

    async def list_charts(
        self, search: str | None = None, dashboard_id: int | None = None, limit: int = 25
    ) -> str:
        """List charts, optionally filtered by search or dashboard."""
        params: dict[str, Any] = {
            "page_size": limit,
            "page": 0,
            "order_column": "changed_on_delta_humanized",
            "order_direction": "desc",
        }
        filters = []
        if search:
            filters.append({"col": "slice_name", "opr": "ct", "value": search})
        if dashboard_id is not None:
            filters.append({"col": "dashboards", "opr": "rel_m_m", "value": dashboard_id})
        if filters:
            params["filters"] = json.dumps(filters)

        data = await self._get("/chart/", params)
        charts = data.get("result", [])
        total = data.get("count", len(charts))

        if not charts:
            suffix = f' matching "{search}"' if search else ""
            return f"No charts found{suffix}"

        lines = [f"Found {total} charts (showing {len(charts)}):\n"]
        for c in charts:
            cid = c.get("id", "?")
            name = c.get("slice_name", "Untitled")
            viz = c.get("viz_type", "?")
            ds = c.get("datasource_name_text", "")
            changed = c.get("changed_on_delta_humanized", "")
            owners = ", ".join(
                f"{o.get('first_name', '')} {o.get('last_name', '')}".strip()
                for o in c.get("owners", [])
            )

            line = f"- **{name}** (id={cid}, type={viz})"
            if ds:
                line += f"\n  Datasource: {ds}"
            if changed:
                line += f" | {changed}"
            if owners:
                line += f"\n  Owners: {owners}"
            lines.append(line)
        return "\n".join(lines)

    async def list_dashboard_charts(self, dashboard_id: int) -> str:
        """List charts from the dashboard-specific endpoint."""
        data = await self._get(f"/dashboard/{dashboard_id}/charts")
        charts = data.get("result", [])
        lines = [f"Dashboard {dashboard_id} charts: {len(charts)}"]
        for c in charts:
            cid = c.get("id", "?")
            name = c.get("slice_name", c.get("chart_name", "Untitled"))
            viz = c.get("viz_type", "?")
            ds_id = c.get("datasource_id", "")
            lines.append(f"- {cid} | {name} | viz={viz} | datasource={ds_id}")
        return "\n".join(lines)

    async def get_chart_params_summary(self, chart_id: int) -> str:
        """Return compact chart config without full params/query_context."""
        chart_info = await self._get(f"/chart/{chart_id}")
        chart = chart_info.get("result", {})
        params = self._loads_json_maybe(chart.get("params"), {})
        summary = {
            "id": chart_id,
            "name": chart.get("slice_name"),
            "viz_type": chart.get("viz_type"),
            "datasource_id": chart.get("datasource_id"),
            "datasource_type": chart.get("datasource_type"),
            "has_query_context": bool(chart.get("query_context")),
            "params": self._compact_chart_params(params),
        }
        return json.dumps(summary, ensure_ascii=False, indent=2, default=str)

    async def get_chart_data(self, chart_id: int) -> str:
        """Get the underlying data for a chart."""
        chart_info = await self._get(f"/chart/{chart_id}")
        chart = chart_info.get("result", {})
        name = chart.get("slice_name", "Untitled")
        viz = chart.get("viz_type", "?")
        query_context = chart.get("query_context")

        if not query_context:
            params = chart.get("params")
            if params and isinstance(params, str):
                try:
                    params = json.loads(params)
                except json.JSONDecodeError:
                    pass
            ds_id = chart.get("datasource_id")
            ds_type = chart.get("datasource_type", "table")
            return (
                f"## Chart: {name} (id={chart_id}, type={viz})\n"
                f"Datasource: {ds_type} id={ds_id}\n\n"
                f"No query_context available. Params summary:\n"
                f"```json\n{json.dumps(self._compact_chart_params(params), indent=2, ensure_ascii=False, default=str)}\n```"
            )

        if isinstance(query_context, str):
            query_context = json.loads(query_context)

        data = await self._post("/chart/data", query_context)
        results = data.get("result", [])

        lines = [f"## Chart Data: {name} (id={chart_id}, type={viz})\n"]
        for i, qr in enumerate(results):
            colnames = qr.get("colnames", [])
            row_data = qr.get("data", [])
            total_rows = qr.get("rowcount", len(row_data))

            lines.append(f"**Query {i+1}**: {total_rows} rows, {len(colnames)} columns")
            if colnames:
                lines.append(f"Columns: {', '.join(colnames)}")

            preview = row_data[:20]
            if preview:
                lines.append("\nFirst rows:")
                for row in preview:
                    vals = " | ".join(str(row.get(c, ""))[:30] for c in colnames[:10])
                    lines.append(f"  {vals}")
                if total_rows > 20:
                    lines.append(f"  ... ({total_rows - 20} more rows)")
        return "\n".join(lines)

    async def get_dashboard_layout_summary(
        self,
        dashboard_id: int,
        chart_ids: list[int] | None = None,
        include_tabs: bool = True,
    ) -> str:
        """Return compact position_json summary."""
        data = await self._get(f"/dashboard/{dashboard_id}")
        d = data.get("result", {})
        position = self._loads_json_maybe(d.get("position_json"), {})
        if not isinstance(position, dict):
            return f"Dashboard {dashboard_id}: position_json unavailable or invalid"

        lines = [f"Dashboard {dashboard_id} layout"]
        if include_tabs:
            for cid, component in position.items():
                if isinstance(component, dict) and component.get("type") == "TAB":
                    lines.append(f"TAB {cid}: text={component.get('meta', {}).get('text')}; children={component.get('children', [])}")

        target_chart_ids = set(chart_ids or [])
        if target_chart_ids:
            lines.extend(self._layout_invariants(position, chart_ids=target_chart_ids))
        else:
            for cid, component in position.items():
                if isinstance(component, dict) and component.get("type") == "ROW":
                    lines.extend(self._layout_invariants(position, component_ids={cid}))
        return "\n".join(lines)

    async def patch_dashboard_position(
        self,
        dashboard_id: int,
        operations: list[dict],
        readback_component_ids: list[str] | None = None,
        readback_chart_ids: list[int] | None = None,
    ) -> str:
        """Patch dashboard position_json with small operations."""
        data = await self._get(f"/dashboard/{dashboard_id}")
        d = data.get("result", {})
        position = self._loads_json_maybe(d.get("position_json"), {})
        if not isinstance(position, dict):
            return f"Dashboard {dashboard_id}: position_json unavailable or invalid"

        touched: set[str] = set(readback_component_ids or [])
        for op in operations:
            action = op.get("op")
            cid = op.get("id")
            if action in {"set_meta", "set_children", "upsert_component", "delete_component"} and cid:
                touched.add(str(cid))

            if action == "set_meta":
                if cid not in position or not isinstance(position.get(cid), dict):
                    raise ValueError(f"set_meta target not found: {cid}")
                values = op.get("values") or {}
                if not isinstance(values, dict):
                    raise ValueError("set_meta values must be an object")
                position[cid].setdefault("meta", {}).update(values)
            elif action == "set_children":
                if cid not in position or not isinstance(position.get(cid), dict):
                    raise ValueError(f"set_children target not found: {cid}")
                children = op.get("children")
                if not isinstance(children, list):
                    raise ValueError("set_children children must be an array")
                position[cid]["children"] = children
            elif action == "remove_child":
                parent_id = op.get("parent_id")
                child_id = op.get("child_id")
                if parent_id not in position or not isinstance(position.get(parent_id), dict):
                    raise ValueError(f"remove_child parent not found: {parent_id}")
                children = position[parent_id].get("children", []) or []
                position[parent_id]["children"] = [child for child in children if child != child_id]
                touched.add(str(parent_id))
            elif action == "upsert_component":
                component = op.get("component")
                if not isinstance(component, dict) or "id" not in component or "type" not in component:
                    raise ValueError("upsert_component requires component with id and type")
                position[component["id"]] = component
                touched.add(str(component["id"]))
            elif action == "delete_component":
                position.pop(cid, None)
            else:
                raise ValueError(f"Unknown layout op: {action}")

        await self._put(f"/dashboard/{dashboard_id}", {"position_json": json.dumps(position, ensure_ascii=False)})
        readback = self._layout_invariants(
            position,
            component_ids=touched,
            chart_ids=set(readback_chart_ids or []),
        )
        lines = [
            f"Dashboard {dashboard_id} position patched. operations={len(operations)}; touched={sorted(touched)}"
        ]
        if readback:
            lines.append("Readback:")
            lines.extend(readback)
        return "\n".join(lines)

    async def sql_query(self, sql: str, database_id: int = 1, schema: str = "") -> str:
        """Execute SQL query via SQL Lab."""
        payload = {
            "database_id": database_id,
            "sql": sql,
            "runAsync": False,
            "queryLimit": 1000,
        }
        # ClickHouse/Trino treat "schema" as the database name, so a hardcoded "public"
        # 500s ("Database public does not exist"). Send schema only when explicitly given.
        if schema:
            payload["schema"] = schema
        data = await self._post("/sqllab/execute/", payload)

        columns = data.get("columns", [])
        rows = data.get("data", [])
        query_id = data.get("query_id", "?")
        status = data.get("status", "?")

        col_names = [c.get("column_name", c.get("name", "?")) if isinstance(c, dict) else str(c) for c in columns]

        lines = [
            f"**SQL Lab Result** (query_id={query_id}, status={status})",
            f"Rows: {len(rows)}, Columns: {len(col_names)}",
        ]
        if col_names:
            lines.append(f"Columns: {', '.join(col_names)}")

        preview = rows[:30]
        if preview:
            lines.append("")
            for row in preview:
                if isinstance(row, dict):
                    vals = " | ".join(str(row.get(c, ""))[:40] for c in col_names[:12])
                else:
                    vals = str(row)[:200]
                lines.append(f"  {vals}")
            if len(rows) > 30:
                lines.append(f"  ... ({len(rows) - 30} more rows)")
        return "\n".join(lines)

    async def create_chart(
        self,
        slice_name: str,
        viz_type: str,
        datasource_id: int,
        datasource_type: str = "table",
        params: str = "{}",
        dashboards: list[int] | None = None,
    ) -> str:
        """Create a new chart."""
        payload: dict[str, Any] = {
            "slice_name": slice_name,
            "viz_type": viz_type,
            "datasource_id": datasource_id,
            "datasource_type": datasource_type,
            "params": params,
        }
        if dashboards:
            payload["dashboards"] = dashboards

        data = await self._post("/chart/", payload)
        result = data.get("result", data)
        chart_id = data.get("id", result.get("id", "?"))
        return (
            f"Chart created. id={chart_id}; name={slice_name}; viz_type={viz_type}; "
            f"datasource={datasource_id}__{datasource_type}; dashboards={dashboards or []}"
        )

    async def update_dashboard(
        self,
        dashboard_id: int,
        json_metadata: str | None = None,
        position_json: str | None = None,
    ) -> str:
        """Update dashboard properties."""
        payload: dict[str, Any] = {}
        if json_metadata is not None:
            payload["json_metadata"] = json_metadata
        if position_json is not None:
            payload["position_json"] = position_json

        if not payload:
            return "No properties to update."

        data = await self._put(f"/dashboard/{dashboard_id}", payload)
        changed = ", ".join(payload.keys())
        result = data.get("result", {}) if isinstance(data, dict) else {}
        last_modified = data.get("last_modified_time") if isinstance(data, dict) else None
        sizes = []
        if position_json is not None:
            sizes.append(f"position_json_chars={len(position_json)}")
        if json_metadata is not None:
            sizes.append(f"json_metadata_chars={len(json_metadata)}")
        suffix = f"; {'; '.join(sizes)}" if sizes else ""
        if last_modified is not None:
            suffix += f"; last_modified_time={last_modified}"
        elif result:
            suffix += f"; result_keys={list(result.keys())}"
        return f"Dashboard {dashboard_id} updated. fields=[{changed}]{suffix}"

    async def update_chart(
        self,
        chart_id: int,
        slice_name: str | None = None,
        viz_type: str | None = None,
        datasource_id: int | None = None,
        datasource_type: str | None = None,
        params: str | dict | None = None,
        query_context: str | dict | None = None,
        dashboards: list[int] | None = None,
        clear_query_context: bool = False,
    ) -> str:
        """Update an existing chart properties and/or query payload."""
        payload: dict[str, Any] = {}

        if slice_name is not None:
            payload["slice_name"] = slice_name
        if viz_type is not None:
            payload["viz_type"] = viz_type
        if datasource_id is not None:
            payload["datasource_id"] = datasource_id
        if datasource_type is not None:
            payload["datasource_type"] = datasource_type
        if dashboards is not None:
            payload["dashboards"] = dashboards

        if params is not None:
            if isinstance(params, (dict, list)):
                payload["params"] = json.dumps(params, ensure_ascii=False)
            else:
                payload["params"] = params

        if clear_query_context:
            payload["query_context"] = None
        elif query_context is not None:
            if isinstance(query_context, (dict, list)):
                payload["query_context"] = json.dumps(query_context, ensure_ascii=False)
            else:
                payload["query_context"] = query_context

        if not payload:
            return "No properties to update."

        data = await self._put(f"/chart/{chart_id}", payload)
        result = data.get("result", {}) if isinstance(data, dict) else {}
        changed = ", ".join(payload.keys())
        parts = [f"Chart {chart_id} updated", f"fields=[{changed}]"]
        if "slice_name" in result:
            parts.append(f"name={result['slice_name']}")
        elif slice_name is not None:
            parts.append(f"name={slice_name}")
        if "viz_type" in result:
            parts.append(f"viz_type={result['viz_type']}")
        elif viz_type is not None:
            parts.append(f"viz_type={viz_type}")
        if datasource_id is not None:
            parts.append(f"datasource={datasource_id}__{datasource_type or 'table'}")
        if params is not None:
            parts.append(f"params={json.dumps(self._compact_chart_params(params), ensure_ascii=False, default=str)}")
        if clear_query_context:
            parts.append("query_context=cleared")
        return "; ".join(parts)

    @staticmethod
    def _canon_sql(expr: str) -> str:
        """Normalize SQL expression for stable matching."""
        return re.sub(r"\s+", "", str(expr).lower())

    async def normalize_dashboard_metrics(
        self,
        dashboard_id: int,
        dataset_id: int | None = None,
        dry_run: bool = True,
        clear_query_context: bool = True,
    ) -> str:
        """
        Replace adhoc SQL metrics in chart params with saved dataset metric names.

        Scope:
        - All charts on a dashboard.
        - If dataset_id is provided, only charts with params.datasource == "<dataset_id>__table" are processed.
        - Exact expression match only (normalized whitespace/case).
        """
        charts_data = await self._get(f"/dashboard/{dashboard_id}/charts")
        charts = charts_data.get("result", [])
        if not charts:
            return f"No charts found on dashboard {dashboard_id}."

        dataset_expr_map: dict[int, dict[str, str]] = {}

        async def get_expr_map(dsid: int) -> dict[str, str]:
            if dsid in dataset_expr_map:
                return dataset_expr_map[dsid]
            ds = await self._get(f"/dataset/{dsid}")
            metrics = ds.get("result", {}).get("metrics", [])
            expr_map: dict[str, str] = {}
            for m in metrics:
                expr = m.get("expression")
                name = m.get("metric_name")
                if expr and name:
                    expr_map[self._canon_sql(expr)] = name
            dataset_expr_map[dsid] = expr_map
            return expr_map

        updated: list[tuple[int, str, int]] = []
        skipped: list[tuple[int, str, str]] = []
        unresolved: list[tuple[int, str, int]] = []

        for c in charts:
            cid = c.get("id")
            cname = c.get("slice_name", "Untitled")
            if cid is None:
                continue

            chart_info = await self._get(f"/chart/{cid}")
            chart = chart_info.get("result", {})
            params_raw = chart.get("params")

            try:
                params = json.loads(params_raw) if isinstance(params_raw, str) else (params_raw or {})
            except Exception:
                skipped.append((cid, cname, "invalid params json"))
                continue

            dsref = str(params.get("datasource", "") or "")
            m = re.match(r"^(\d+)__table$", dsref)
            if not m:
                skipped.append((cid, cname, "params.datasource is not <id>__table"))
                continue

            chart_dataset_id = int(m.group(1))
            if dataset_id is not None and chart_dataset_id != dataset_id:
                skipped.append((cid, cname, f"dataset mismatch ({chart_dataset_id} != {dataset_id})"))
                continue

            expr_map = await get_expr_map(chart_dataset_id)
            changed = 0
            unresolved_count = 0

            def convert_metric_entry(item: Any) -> Any:
                nonlocal changed, unresolved_count
                if not isinstance(item, dict):
                    return item
                sql = item.get("sqlExpression")
                if not sql:
                    return item
                metric_name = expr_map.get(self._canon_sql(sql))
                if metric_name:
                    changed += 1
                    return metric_name
                unresolved_count += 1
                return item

            for bucket in ("metrics", "percent_metrics"):
                arr = params.get(bucket)
                if isinstance(arr, list):
                    params[bucket] = [convert_metric_entry(x) for x in arr]

            metric = params.get("metric")
            if isinstance(metric, dict):
                params["metric"] = convert_metric_entry(metric)

            if changed == 0 and unresolved_count == 0:
                skipped.append((cid, cname, "no adhoc SQL metrics found"))
                continue

            if changed == 0 and unresolved_count > 0:
                unresolved.append((cid, cname, unresolved_count))
                continue

            if dry_run:
                updated.append((cid, cname, changed))
            else:
                payload: dict[str, Any] = {
                    "params": json.dumps(params, ensure_ascii=False),
                }
                if clear_query_context:
                    payload["query_context"] = None
                await self._put(f"/chart/{cid}", payload)
                updated.append((cid, cname, changed))
                if unresolved_count > 0:
                    unresolved.append((cid, cname, unresolved_count))

        lines = [
            f"Dashboard {dashboard_id} metric normalization",
            f"mode={'dry-run' if dry_run else 'apply'}",
            f"charts_total={len(charts)}",
            f"updated={len(updated)}",
            f"skipped={len(skipped)}",
            f"with_unresolved={len(unresolved)}",
        ]

        if updated:
            lines.append("\nUpdated charts:")
            for cid, cname, cnt in updated:
                lines.append(f"- {cid} | {cname} | replaced={cnt}")

        if unresolved:
            lines.append("\nUnresolved adhoc SQL metrics:")
            for cid, cname, cnt in unresolved:
                lines.append(f"- {cid} | {cname} | unresolved={cnt}")

        if skipped:
            lines.append("\nSkipped charts:")
            for cid, cname, reason in skipped[:20]:
                lines.append(f"- {cid} | {cname} | {reason}")
            if len(skipped) > 20:
                lines.append(f"- ... ({len(skipped) - 20} more)")

        return "\n".join(lines)

    async def get_dataset(self, dataset_id: int) -> str:
        """Get dataset details."""
        data = await self._get(f"/dataset/{dataset_id}")
        ds = data.get("result", {})

        table_name = ds.get("table_name", "?")
        schema = ds.get("schema", "?")
        database = ds.get("database", {})
        db_name = database.get("database_name", "?") if isinstance(database, dict) else "?"
        sql_text = ds.get("sql") or ""
        description = ds.get("description") or ""

        columns = ds.get("columns", [])
        metrics = ds.get("metrics", [])

        lines = [
            f"## Dataset: {table_name} (id={dataset_id})",
            f"**Schema**: {schema}",
            f"**Database**: {db_name}",
        ]
        if description:
            lines.append(f"**Description**: {description}")
        if sql_text:
            lines.append(f"\n**SQL**:\n```sql\n{sql_text[:3000]}\n```")

        if columns:
            lines.append(f"\n**Columns** ({len(columns)}):")
            for col in columns:
                cname = col.get("column_name", "?")
                ctype = col.get("type", "?")
                cdesc = col.get("description", "")
                filterable = col.get("filterable", False)
                groupby = col.get("groupby", False)
                suffix = ""
                if filterable:
                    suffix += " [filterable]"
                if groupby:
                    suffix += " [groupby]"
                line = f"  - `{cname}` ({ctype}){suffix}"
                if cdesc:
                    line += f" — {cdesc}"
                lines.append(line)

        if metrics:
            lines.append(f"\n**Metrics** ({len(metrics)}):")
            for m in metrics:
                mname = m.get("metric_name", "?")
                expr = m.get("expression", "?")
                lines.append(f"  - `{mname}`: {expr}")

        return "\n".join(lines)

    async def get_dataset_summary(
        self,
        dataset_id: int,
        include_columns: bool = True,
        include_metrics: bool = True,
        include_sql_preview: bool = False,
        sql_chars: int = 500,
    ) -> str:
        """Compact dataset metadata; full SQL stays opt-in."""
        data = await self._get(f"/dataset/{dataset_id}")
        ds = data.get("result", {})
        database = ds.get("database", {})
        db_name = database.get("database_name", "?") if isinstance(database, dict) else "?"
        columns = ds.get("columns", []) or []
        metrics = ds.get("metrics", []) or []

        summary: dict[str, Any] = {
            "id": dataset_id,
            "name": ds.get("table_name"),
            "schema": ds.get("schema"),
            "database": db_name,
            "kind": ds.get("kind"),
            "main_dttm_col": ds.get("main_dttm_col"),
            "columns_count": len(columns),
            "metrics_count": len(metrics),
            "has_sql": bool(ds.get("sql")),
        }
        if include_columns:
            summary["columns"] = [
                {
                    "name": c.get("column_name"),
                    "type": c.get("type"),
                    "groupby": c.get("groupby"),
                    "filterable": c.get("filterable"),
                    "is_dttm": c.get("is_dttm"),
                }
                for c in columns
            ]
        if include_metrics:
            summary["metrics"] = [
                {
                    "name": m.get("metric_name"),
                    "verbose_name": m.get("verbose_name"),
                    "expression": m.get("expression"),
                    "d3format": m.get("d3format"),
                }
                for m in metrics
            ]
        if include_sql_preview and ds.get("sql"):
            sql_text = ds.get("sql") or ""
            summary["sql_preview"] = sql_text[: max(0, sql_chars)]
            summary["sql_chars"] = len(sql_text)
        return json.dumps(summary, ensure_ascii=False, indent=2, default=str)

    @staticmethod
    def _quote_ident(part: str) -> str:
        return '"' + str(part).replace('"', '""') + '"'

    @staticmethod
    def _is_table_not_found(exc: httpx.HTTPStatusError) -> bool:
        if exc.response.status_code != 422:
            return False
        try:
            msg = exc.response.json().get("message", {})
            if isinstance(msg, dict) and "table" in msg:
                return True
        except Exception:
            pass
        return "could not be found" in exc.response.text

    async def create_dataset(
        self,
        database_id: int,
        table_name: str,
        schema: str = "",
        catalog: str | None = None,
        sql: str | None = None,
        description: str | None = None,
    ) -> str:
        """Create a new dataset (physical or virtual SQL)."""
        payload: dict[str, Any] = {"database": database_id, "table_name": table_name}
        if schema:
            payload["schema"] = schema
        if catalog:
            payload["catalog"] = catalog
        if sql:
            payload["sql"] = sql
        if description:
            payload["description"] = description

        note = ""
        try:
            data = await self._post("/dataset/", payload)
        except httpx.HTTPStatusError as e:
            # Physical registration reflects the table; Trino raises NoSuchTableError for many
            # Iceberg tables -> 422 "could not be found", so retry as a virtual SELECT * (skips
            # reflection). Security 422s would hit the same access check, so those re-raise.
            if sql is None and self._is_table_not_found(e):
                ref = ".".join(self._quote_ident(p) for p in (catalog, schema, table_name) if p)
                payload["sql"] = f"SELECT * FROM {ref}"
                data = await self._post("/dataset/", payload)
                note = " (registered as virtual SELECT * — physical reflection unsupported for this table)"
            else:
                raise

        ds_id = data.get("id", data.get("data", {}).get("id", "?"))
        name = data.get("data", {}).get("table_name", table_name)
        cols = data.get("data", {}).get("columns", [])
        col_names = [c.get("column_name", "?") for c in cols]
        return (
            f"Dataset created. ID: {ds_id}, name: {name}\n"
            f"Columns ({len(col_names)}): {', '.join(col_names[:20])}"
            + (f" ... (+{len(col_names)-20} more)" if len(col_names) > 20 else "")
        )

    async def update_dataset(
        self,
        dataset_id: int,
        override_columns: bool = False,
        metrics: list[dict] | None = None,
        columns: list[dict] | None = None,
        description: str | None = None,
        sql: str | None = None,
    ) -> str:
        """Update dataset properties (metrics, columns, description, SQL)."""
        payload: dict[str, Any] = {}
        if metrics is not None:
            payload["metrics"] = metrics
        if columns is not None:
            payload["columns"] = columns
        if description is not None:
            payload["description"] = description
        if sql is not None:
            payload["sql"] = sql
        if override_columns:
            payload["override_columns"] = True

        if not payload:
            return "No properties to update."

        data = await self._put(f"/dataset/{dataset_id}?override_columns={str(override_columns).lower()}", payload)
        return f"Dataset {dataset_id} updated. Response keys: {list(data.keys())}"

    async def list_datasets(self, search: str | None = None, limit: int = 25) -> str:
        """List/search datasets."""
        filters_rison = ""
        if search:
            safe = search.replace("'", "\\'")
            filters_rison = f",filters:!((col:table_name,opr:ct,value:'{safe}'))"

        q = f"(page_size:{limit},page:0,order_column:changed_on_delta_humanized,order_direction:desc{filters_rison})"
        data = await self._get("/dataset/", {"q": q})
        datasets = data.get("result", [])
        total = data.get("count", len(datasets))

        if not datasets:
            suffix = f' matching "{search}"' if search else ""
            return f"No datasets found{suffix}"

        lines = [f"Found {total} datasets (showing {len(datasets)}):\n"]
        for ds in datasets:
            did = ds.get("id", "?")
            name = ds.get("table_name", "Untitled")
            schema = ds.get("schema", "")
            db = ds.get("database", {})
            db_name = db.get("database_name", "?") if isinstance(db, dict) else "?"
            kind = ds.get("kind", "")
            changed = ds.get("changed_on_delta_humanized", "")

            line = f"- **{schema}.{name}** (id={did}, db={db_name})"
            if kind:
                line += f" [{kind}]"
            if changed:
                line += f" | {changed}"
            lines.append(line)
        return "\n".join(lines)

    async def create_dashboard(
        self,
        title: str,
        published: bool = False,
        slug: str | None = None,
    ) -> str:
        """Create a new empty dashboard."""
        payload: dict[str, Any] = {
            "dashboard_title": title,
            "published": published,
        }
        if slug:
            payload["slug"] = slug

        data = await self._post("/dashboard/", payload)
        dash_id = data.get("id", "?")
        url = f"{self.base_url}/superset/dashboard/{dash_id}/"
        return f"Dashboard created. ID: {dash_id}\nTitle: {title}\n[Open in Superset]({url})"

    async def delete_object(self, object_type: str, object_id: int) -> str:
        """Delete a Superset object (chart, dataset, or dashboard)."""
        valid = {"chart", "dataset", "dashboard"}
        if object_type not in valid:
            return f"Invalid object_type '{object_type}'. Must be one of: {', '.join(sorted(valid))}"

        data = await self._delete(f"/{object_type}/{object_id}")
        return f"{object_type.capitalize()} {object_id} deleted. Response: {json.dumps(data, default=str)[:500]}"

    async def _fetch_thumbnail_png(self, kind: str, oid: int, max_wait: int = 75) -> bytes:
        """Fetch the server-rendered PNG via Superset's thumbnail endpoint; retries while it generates (HTTP 202)."""
        info = await self._get(f"/{kind}/{oid}")
        thumb_url = info.get("result", {}).get("thumbnail_url")
        if not thumb_url:
            raise ValueError(f"{kind} {oid}: no thumbnail_url (server-side thumbnails likely disabled)")
        await self._ensure_session()
        client = await self._get_client()
        deadline = time.monotonic() + max_wait
        while True:
            r = await client.get(f"{self.base_url}{thumb_url}")
            ctype = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ctype:
                return r.content
            if r.status_code == 401:
                await self._login()
                continue
            if r.status_code in (202, 302) and time.monotonic() < deadline:
                await asyncio.sleep(5)
                continue
            raise ValueError(f"{kind} {oid} thumbnail not ready: HTTP {r.status_code} {r.text[:150]}")

    async def get_chart_screenshot(self, chart_id: int) -> list[dict]:
        """Rendered PNG of a chart, returned as MCP image content (for visual inspection)."""
        png = await self._fetch_thumbnail_png("chart", chart_id)
        return [
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            {"type": "text", "text": f"Chart {chart_id} screenshot: PNG, {len(png)} bytes."},
        ]

    async def get_dashboard_screenshot(self, dashboard_id: int) -> list[dict]:
        """Rendered PNG of a whole dashboard, returned as MCP image content (for visual inspection)."""
        png = await self._fetch_thumbnail_png("dashboard", dashboard_id)
        return [
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            {"type": "text", "text": f"Dashboard {dashboard_id} screenshot: PNG, {len(png)} bytes."},
        ]

    # ── Tool registry ─────────────────────────────────────────────────

    def get_tools(self) -> list[dict]:
        return [
            {
                "name": "refresh_token",
                "description": (
                    "Force re-login to Superset with stored credentials. "
                    "Call this if any other superset tool returns a 401 error."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "list_dashboards",
                "description": "List/search Superset dashboards. Returns titles, IDs, slugs, owners, publish status.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Search by dashboard title (optional)"},
                        "limit": {"type": "integer", "description": "Max results (default 25)"},
                    },
                    "required": [],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_dashboard",
                "description": "Get detailed info about a Superset dashboard (description, owners, charts list). Use dashboard ID or slug.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "id_or_slug": {"type": "string", "description": "Dashboard ID (number) or slug"},
                    },
                    "required": ["id_or_slug"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "list_charts",
                "description": "List/search Superset charts. Optionally filter by name or dashboard_id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Search by chart name (optional)"},
                        "dashboard_id": {"type": "integer", "description": "Filter charts belonging to a dashboard (optional)"},
                        "limit": {"type": "integer", "description": "Max results (default 25)"},
                    },
                    "required": [],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "list_dashboard_charts",
                "description": "List charts from /dashboard/{id}/charts. Prefer this over list_charts(dashboard_id=...) for exact dashboard membership.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                    },
                    "required": ["dashboard_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_chart_params_summary",
                "description": "Get compact chart config: name, viz, datasource, core params, filter count, query_context presence.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chart_id": {"type": "integer", "description": "Chart ID"},
                    },
                    "required": ["chart_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_chart_data",
                "description": "Get the underlying data for a specific chart by its ID. Returns columns and rows.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chart_id": {"type": "integer", "description": "Chart ID"},
                    },
                    "required": ["chart_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_dashboard_layout_summary",
                "description": "Get compact dashboard position_json summary: tabs, rows, widths, chart IDs and heights. Avoids dumping full layout JSON.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                        "chart_ids": {"type": "array", "items": {"type": "integer"}, "description": "Optional chart IDs to focus readback on"},
                        "include_tabs": {"type": "boolean", "description": "Include tab children summary (default true)"},
                    },
                    "required": ["dashboard_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "patch_dashboard_position",
                "description": "Patch dashboard position_json with compact operations and return layout invariants. Operations: set_meta, set_children, remove_child, upsert_component, delete_component.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                        "operations": {"type": "array", "items": {"type": "object"}, "description": "Patch operations"},
                        "readback_component_ids": {"type": "array", "items": {"type": "string"}, "description": "Optional component IDs for focused readback"},
                        "readback_chart_ids": {"type": "array", "items": {"type": "integer"}, "description": "Optional chart IDs for focused readback"},
                    },
                    "required": ["dashboard_id", "operations"],
                },
            },
            {
                "name": "sql_query",
                "description": "Execute a SQL query via Superset SQL Lab. Returns result rows and columns.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string", "description": "SQL query to execute"},
                        "database_id": {"type": "integer", "description": "Superset database ID (default 1)"},
                        "schema": {"type": "string", "description": "Schema/database name (optional; omit to use the connection default — ClickHouse/Trino reject a non-existent schema like 'public')"},
                    },
                    "required": ["sql"],
                },
            },
            {
                "name": "create_chart",
                "description": "Create a new chart in Superset. Returns the created chart info.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slice_name": {"type": "string", "description": "Chart name"},
                        "viz_type": {"type": "string", "description": "Visualization type (e.g. 'pivot_table_v2', 'table', 'echarts_timeseries_bar')"},
                        "datasource_id": {"type": "integer", "description": "Dataset ID"},
                        "datasource_type": {"type": "string", "description": "Dataset type (default 'table')"},
                        "params": {"type": "string", "description": "Chart params as JSON string"},
                        "dashboards": {"type": "array", "items": {"type": "integer"}, "description": "List of dashboard IDs to add chart to"},
                    },
                    "required": ["slice_name", "viz_type", "datasource_id", "params"],
                },
            },
            {
                "name": "update_dashboard",
                "description": "Update dashboard properties (JSON metadata, position, etc.).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                        "json_metadata": {"type": "string", "description": "JSON metadata string (optional)"},
                        "position_json": {"type": "string", "description": "Position JSON string (optional)"},
                    },
                    "required": ["dashboard_id"],
                },
            },
            {
                "name": "update_chart",
                "description": (
                    "Update an existing Superset chart (title, datasource, params, query_context, dashboards). "
                    "Set clear_query_context=true after params/datasource changes to avoid stale query context."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chart_id": {"type": "integer", "description": "Chart ID"},
                        "slice_name": {"type": "string", "description": "Chart title (optional)"},
                        "viz_type": {"type": "string", "description": "Visualization type (optional)"},
                        "datasource_id": {"type": "integer", "description": "Dataset ID (optional)"},
                        "datasource_type": {"type": "string", "description": "Dataset type, usually 'table' (optional)"},
                        "params": {
                            "type": "string",
                            "description": "Chart params as a JSON string (optional)",
                        },
                        "query_context": {
                            "type": "string",
                            "description": "Query context as a JSON string (optional)",
                        },
                        "dashboards": {"type": "array", "items": {"type": "integer"}, "description": "Dashboard IDs (optional)"},
                        "clear_query_context": {
                            "type": "boolean",
                            "description": "If true, force query_context=null to clear stale cache (default false)",
                        },
                    },
                    "required": ["chart_id"],
                },
            },
            {
                "name": "normalize_dashboard_metrics",
                "description": (
                    "Replace adhoc SQL metrics in chart params with saved dataset metric names across a dashboard. "
                    "Use dry_run=true first, then dry_run=false to apply."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                        "dataset_id": {"type": "integer", "description": "Optional dataset ID filter; only charts from this dataset are processed"},
                        "dry_run": {"type": "boolean", "description": "Preview changes only (default true)"},
                        "clear_query_context": {"type": "boolean", "description": "Set query_context=null on updated charts (default true)"},
                    },
                    "required": ["dashboard_id"],
                },
            },
            {
                "name": "get_dataset",
                "description": "Get dataset details (columns, metrics, SQL, etc.) by dataset ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dataset_id": {"type": "integer", "description": "Dataset ID"},
                    },
                    "required": ["dataset_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_dataset_summary",
                "description": "Get compact dataset metadata. Full virtual SQL is opt-in via include_sql_preview.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dataset_id": {"type": "integer", "description": "Dataset ID"},
                        "include_columns": {"type": "boolean", "description": "Include columns list (default true)"},
                        "include_metrics": {"type": "boolean", "description": "Include metrics list (default true)"},
                        "include_sql_preview": {"type": "boolean", "description": "Include truncated SQL preview (default false)"},
                        "sql_chars": {"type": "integer", "description": "SQL preview chars if enabled (default 500)"},
                    },
                    "required": ["dataset_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "create_dataset",
                "description": "Create a new dataset in Superset. Use 'sql' for virtual (SQL-based) datasets. For Trino multi-catalog DBs pass 'catalog' (e.g. 'dwh-iceberg' for silver/gold); a physical Trino table auto-falls back to a virtual SELECT * when reflection is unsupported. Note: virtual datasets (including the Trino fallback) do NOT auto-set a time column (main_dttm_col) — set it afterwards via update_dataset or the UI if a time-series chart needs a time axis.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "database_id": {"type": "integer", "description": "Superset database connection ID (e.g. 2 for Market ClickHouse, 16 for UZMarket-Trino)"},
                        "table_name": {"type": "string", "description": "Dataset display name"},
                        "schema": {"type": "string", "description": "Schema name (e.g. 'gold', 'silver')"},
                        "catalog": {"type": "string", "description": "Catalog for multi-catalog DBs like Trino (e.g. 'dwh-iceberg'). Required for Trino silver/gold datasets."},
                        "sql": {"type": "string", "description": "SQL query for virtual dataset (optional, omit for physical table)"},
                        "description": {"type": "string", "description": "Dataset description (optional)"},
                    },
                    "required": ["database_id", "table_name"],
                },
            },
            {
                "name": "update_dataset",
                "description": "Update dataset properties: metrics, columns, description, or SQL.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dataset_id": {"type": "integer", "description": "Dataset ID"},
                        "metrics": {"type": "array", "items": {"type": "object"}, "description": "Metric definitions [{metric_name, expression, verbose_name, description}]"},
                        "columns": {"type": "array", "items": {"type": "object"}, "description": "Column overrides (optional)"},
                        "description": {"type": "string", "description": "Dataset description (optional)"},
                        "sql": {"type": "string", "description": "Updated SQL for virtual datasets (optional)"},
                        "override_columns": {"type": "boolean", "description": "If true, refresh columns from SQL (default false)"},
                    },
                    "required": ["dataset_id"],
                },
            },
            {
                "name": "list_datasets",
                "description": "List/search Superset datasets by name.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "search": {"type": "string", "description": "Search by dataset table_name (optional)"},
                        "limit": {"type": "integer", "description": "Max results (default 25)"},
                    },
                    "required": [],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "create_dashboard",
                "description": "Create a new empty dashboard in Superset.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Dashboard title"},
                        "published": {"type": "boolean", "description": "Publish immediately (default false)"},
                        "slug": {"type": "string", "description": "URL slug (optional)"},
                    },
                    "required": ["title"],
                },
            },
            {
                "name": "delete",
                "description": "Delete a Superset object (chart, dataset, or dashboard) by type and ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "object_type": {"type": "string", "description": "One of: chart, dataset, dashboard"},
                        "object_id": {"type": "integer", "description": "Object ID to delete"},
                    },
                    "required": ["object_type", "object_id"],
                },
            },
            {
                "name": "get_chart_screenshot",
                "description": "Render a PNG screenshot of a chart by ID (server-side thumbnail, ~800x600). Use for visual checks: does it render, layout, blank/empty detection.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "chart_id": {"type": "integer", "description": "Chart ID"},
                    },
                    "required": ["chart_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
            {
                "name": "get_dashboard_screenshot",
                "description": "Render a PNG screenshot of a whole dashboard by ID (server-side thumbnail, ~800x600). Use for visual inspection of the rendered dashboard.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "dashboard_id": {"type": "integer", "description": "Dashboard ID"},
                    },
                    "required": ["dashboard_id"],
                },
                "annotations": {"readOnlyHint": True},
            },
        ]

    async def call_tool(self, name: str, arguments: dict) -> str | list[dict]:
        if name == "refresh_token":
            return await self.refresh_token()
        elif name == "list_dashboards":
            return await self.list_dashboards(
                search=arguments.get("search"),
                limit=arguments.get("limit", 25),
            )
        elif name == "get_dashboard":
            return await self.get_dashboard(id_or_slug=arguments["id_or_slug"])
        elif name == "list_charts":
            return await self.list_charts(
                search=arguments.get("search"),
                dashboard_id=arguments.get("dashboard_id"),
                limit=arguments.get("limit", 25),
            )
        elif name == "list_dashboard_charts":
            return await self.list_dashboard_charts(
                dashboard_id=arguments["dashboard_id"],
            )
        elif name == "get_chart_params_summary":
            return await self.get_chart_params_summary(
                chart_id=arguments["chart_id"],
            )
        elif name == "get_chart_data":
            return await self.get_chart_data(chart_id=arguments["chart_id"])
        elif name == "get_dashboard_layout_summary":
            return await self.get_dashboard_layout_summary(
                dashboard_id=arguments["dashboard_id"],
                chart_ids=arguments.get("chart_ids"),
                include_tabs=arguments.get("include_tabs", True),
            )
        elif name == "patch_dashboard_position":
            return await self.patch_dashboard_position(
                dashboard_id=arguments["dashboard_id"],
                operations=arguments["operations"],
                readback_component_ids=arguments.get("readback_component_ids"),
                readback_chart_ids=arguments.get("readback_chart_ids"),
            )
        elif name == "sql_query":
            return await self.sql_query(
                sql=arguments["sql"],
                database_id=arguments.get("database_id", 1),
                schema=arguments.get("schema", ""),
            )
        elif name == "create_chart":
            return await self.create_chart(
                slice_name=arguments["slice_name"],
                viz_type=arguments["viz_type"],
                datasource_id=arguments["datasource_id"],
                datasource_type=arguments.get("datasource_type", "table"),
                params=arguments["params"],
                dashboards=arguments.get("dashboards"),
            )
        elif name == "update_dashboard":
            return await self.update_dashboard(
                dashboard_id=arguments["dashboard_id"],
                json_metadata=arguments.get("json_metadata"),
                position_json=arguments.get("position_json"),
            )
        elif name == "update_chart":
            return await self.update_chart(
                chart_id=arguments["chart_id"],
                slice_name=arguments.get("slice_name"),
                viz_type=arguments.get("viz_type"),
                datasource_id=arguments.get("datasource_id"),
                datasource_type=arguments.get("datasource_type"),
                params=arguments.get("params"),
                query_context=arguments.get("query_context"),
                dashboards=arguments.get("dashboards"),
                clear_query_context=arguments.get("clear_query_context", False),
            )
        elif name == "normalize_dashboard_metrics":
            return await self.normalize_dashboard_metrics(
                dashboard_id=arguments["dashboard_id"],
                dataset_id=arguments.get("dataset_id"),
                dry_run=arguments.get("dry_run", True),
                clear_query_context=arguments.get("clear_query_context", True),
            )
        elif name == "get_dataset":
            return await self.get_dataset(dataset_id=arguments["dataset_id"])
        elif name == "get_dataset_summary":
            return await self.get_dataset_summary(
                dataset_id=arguments["dataset_id"],
                include_columns=arguments.get("include_columns", True),
                include_metrics=arguments.get("include_metrics", True),
                include_sql_preview=arguments.get("include_sql_preview", False),
                sql_chars=arguments.get("sql_chars", 500),
            )
        elif name == "create_dataset":
            return await self.create_dataset(
                database_id=arguments["database_id"],
                table_name=arguments["table_name"],
                schema=arguments.get("schema", ""),
                catalog=arguments.get("catalog"),
                sql=arguments.get("sql"),
                description=arguments.get("description"),
            )
        elif name == "update_dataset":
            return await self.update_dataset(
                dataset_id=arguments["dataset_id"],
                override_columns=arguments.get("override_columns", False),
                metrics=arguments.get("metrics"),
                columns=arguments.get("columns"),
                description=arguments.get("description"),
                sql=arguments.get("sql"),
            )
        elif name == "list_datasets":
            return await self.list_datasets(
                search=arguments.get("search"),
                limit=arguments.get("limit", 25),
            )
        elif name == "create_dashboard":
            return await self.create_dashboard(
                title=arguments["title"],
                published=arguments.get("published", False),
                slug=arguments.get("slug"),
            )
        elif name == "delete":
            return await self.delete_object(
                object_type=arguments["object_type"],
                object_id=arguments["object_id"],
            )
        elif name == "get_chart_screenshot":
            return await self.get_chart_screenshot(chart_id=arguments["chart_id"])
        elif name == "get_dashboard_screenshot":
            return await self.get_dashboard_screenshot(dashboard_id=arguments["dashboard_id"])
        else:
            return f"Unknown tool: {name}"


async def check() -> int:
    """Живая проверка доступа для мастера установки (`--check`).

    Мастер проверяет каждый доступ настоящим запросом, а не «записал и
    надеюсь». Для Superset такой запрос нельзя собрать на curl, не написав
    вход в Keycloak второй раз, — поэтому проверка зовёт тот же самый код,
    которым потом ходит коннектор (`_ensure_session` → `_login`), и только
    печатает результат человеку. Ни одной строки логики входа тут нет.

    Печатает одну машиночитаемую строку: `OK:<число дашбордов>` или
    `ERROR:<причина>` — по ней setup.sh отличает успех от отказа, не гадая
    по коду возврата (`uv run` пишет в тот же поток свою установку пакетов).

    Мастер запускает это с временным SUPERSET_COOKIE_FILE: иначе уцелевшая
    с прошлого раза cookie дала бы «доступ есть» на любых, в том числе
    неверных, введённых сейчас логине и пароле.
    """
    try:
        server = SupersetMCPServer()
    except ValueError as exc:  # нет SUPERSET_URL — коннектор не стартует вовсе
        print(f"ERROR:{exc}")
        return 1
    if not server.username or not server.password:
        print("ERROR:не заданы SUPERSET_USERNAME/SUPERSET_PASSWORD")
        return 1
    try:
        await server._ensure_session()
        client = await server._get_client()
        r = await client.get(f"{server.api}/dashboard/?q=(page_size:1)",
                             headers=server._headers())
        if r.status_code != 200:
            print(f"ERROR:Superset ответил {r.status_code} на список дашбордов")
            return 1
        print(f"OK:{r.json().get('count', '?')}")
        return 0
    except Exception as exc:
        print(f"ERROR:{exc}")
        return 1
    finally:
        if server._client is not None and not server._client.is_closed:
            await server._client.aclose()


async def main():
    server = SupersetMCPServer()
    sys.stderr.write(f"Superset MCP server started for {server.base_url}\n")
    sys.stderr.flush()

    while True:
        msg = await read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            await write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "superset-mcp", "version": "1.0.0"},
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            await write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": [
                    {**t, "inputSchema": _strictify_schema(t["inputSchema"])}
                    for t in server.get_tools()
                ]},
            })
        elif method == "tools/call":
            tool_name = params.get("name", "")
            # strict mode (codex/gpt-5.5) forces every optional key to be present; the
            # model passes null for "unset" — drop nulls so the Python defaults apply.
            arguments = {k: v for k, v in (params.get("arguments") or {}).items() if v is not None}
            try:
                result = await server.call_tool(tool_name, arguments)
                content = result if isinstance(result, list) else [{"type": "text", "text": result}]
                await write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"content": content},
                })
            except httpx.HTTPStatusError as e:
                body = e.response.text[:500] if e.response else ""
                await write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"HTTP error {e.response.status_code}: {body}"}],
                        "isError": True,
                    },
                })
            except Exception as e:
                await write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                })
        else:
            if msg_id is not None:
                await write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                })


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    if len(sys.argv) > 1 and sys.argv[1] == "--check":
        sys.exit(asyncio.run(check()))
    asyncio.run(main())
