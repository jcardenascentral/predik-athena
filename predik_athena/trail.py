"""SQL trail: a comment prepended to every query sent to Athena so monitoring can tell
which app, version, module and function produced it.

Format (inspired by QuickSight's query comments):

/* SiteSelector 1.0.0 3f0c...-uuid
 {"partner":"SiteSelector","app":"SiteSelector","version":"1.0.0","module":"zonas",
  "function":"app.queries.resumen_sql","endpoint":"GET /api/zonas",
  "handler":"zonas","view":"resumen","requestId":"..."} */
SELECT ...

- Line 1: app name, version and a unique id for this query (easy to grep / regex).
- Line 2: JSON with the details (one line, so it parses with json_extract on the query text).

How it works:
- SQL builders are decorated with @sql_source("<module>"). They then return a TracedSQL
  (a str subclass) that remembers the builder and the request context at build time.
- An app-level FastAPI dependency (trail_context) records the endpoint, the handler and the
  UI view (header X-View sent by app.js) in a context variable for each request.
- AthenaRunner.run() calls with_trail(sql) right before start_query_execution. The cache key
  is the bare SQL, so the trail never causes cache misses.
"""
from __future__ import annotations

import contextvars
import functools
import json
import re
import uuid

import os

_APP = {"name": os.getenv("PREDIK_APP_NAME", "PredikApp"), "version": os.getenv("PREDIK_APP_VERSION", "0.0.0")}


def configure_trail(app_name: str, version: str) -> None:
    """Name + version written at the start of every query (call once at startup)."""
    _APP.update(name=app_name, version=version)

# Per-request context, filled by trail_context() (FastAPI dependency) and read by the SQL builders.
_request_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("predik_trail_request", default=None)

_VIEW_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class TracedSQL(str):
    """A SQL string that also carries where it came from (module, function, request context)."""

    trail: dict

    def __new__(cls, sql: str, trail: dict):
        obj = super().__new__(cls, sql)
        obj.trail = trail
        return obj


def sql_source(module: str):
    """Decorator for SQL builder functions: tags the returned SQL with module + function name.

    `module` is the functional area (zonas, planes, mapas...). Builders that
    serve several areas take it from their `item_type` argument when present.
    """

    def deco(fn):
        fname = f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            sql = fn(*args, **kwargs)
            mod = module
            if module == "*":  # generic builder: the item type names the module (tam, own_sales...)
                mod = kwargs.get("item_type") or (args[1] if len(args) > 1 else "generic")
            info = {"module": str(mod), "function": fname}
            req = _request_ctx.get()
            if req:
                info.update(req)
            return TracedSQL(sql, info)

        return wrapper

    return deco


def set_request_context(endpoint: str | None, handler: str | None, view: str | None, request_id: str):
    v = (view or "").strip().lower()
    _request_ctx.set({
        "endpoint": endpoint,
        "handler": handler,
        "view": v if _VIEW_RE.match(v) else None,  # untrusted header: keep only safe ids
        "requestId": request_id,
    })


async def trail_context(request) -> None:
    """App-level FastAPI dependency (async, so the context var reaches the endpoint's thread)."""
    route = request.scope.get("route")
    endpoint = f"{request.method} {getattr(route, 'path', request.url.path)}"
    handler_fn = request.scope.get("endpoint")
    handler = getattr(handler_fn, "__name__", None)
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    set_request_context(endpoint, handler, request.headers.get("x-view") or request.headers.get("x-mm-view"), rid[:64])


def set_user(user: str | None) -> None:
    """Adds the user (from the scope token) to this request's trail; the dict is shared with the
    endpoint's thread, so SQL built afterwards in the same request carries it."""
    ctx = _request_ctx.get()
    if ctx is not None and user:
        ctx["user"] = user


def _safe(s: str) -> str:
    # The trail must never close the comment early or break the statement.
    return s.replace("*/", "* /").replace("/*", "/ *")


def build_comment(trail: dict | None = None, query_id: str | None = None) -> str:
    t = dict(trail or {})
    t.setdefault("module", "unknown")
    t.setdefault("function", "unknown")
    name, version = _APP["name"], _APP["version"]
    payload = {"partner": name, "app": name, "version": version}
    payload.update({k: v for k, v in t.items() if v is not None})
    qid = query_id or str(uuid.uuid4())
    body = _safe(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return f"/* {_safe(name)} {_safe(version)} {_safe(qid)} \n {body} */"


def with_trail(sql: str, trail: dict | None = None) -> str:
    """SQL with the trail comment in front. Uses sql.trail when sql is a TracedSQL."""
    t = trail if trail is not None else getattr(sql, "trail", None)
    if t is None:
        # Untagged SQL still gets a trail (module/function unknown) so nothing reaches Athena bare.
        t = {"module": "unknown", "function": "unknown", **(_request_ctx.get() or {})}
    return build_comment(t) + "\n" + str(sql)
