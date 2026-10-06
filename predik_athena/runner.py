"""Query runners: Athena (production/staging) and DuckDB (local mock and tests).

Every runner has the same contract: run(sql) -> list of dicts; any failure -> QueryError
(the app turns it into an HTTP 502 with the reason, never a bare 500).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .credentials import make_session
from .trail import with_trail


class QueryError(RuntimeError):
    pass


class AthenaRunner:
    """Athena through a workgroup. The boto3 client is created on first use and rebuilt if it has no
    credentials: an app started before its instance/task role was attached recovers on its own.

    Settings (arguments or env): ATHENA_WORKGROUP (one per app, with its byte limit), ATHENA_DATABASE,
    ATHENA_OUTPUT_LOCATION (only if the workgroup does not enforce one), ATHENA_TIMEOUT_SECONDS.
    """

    def __init__(self, workgroup: str | None = None, database: str | None = None, output: str | None = None,
                 timeout: int | None = None, session_factory: Callable = make_session):
        self.workgroup = workgroup or os.getenv("ATHENA_WORKGROUP") or "primary"
        self.database = database or os.getenv("ATHENA_DATABASE") or "default"
        self.output = output or os.getenv("ATHENA_OUTPUT_LOCATION") or None
        self.timeout = int(timeout or os.getenv("ATHENA_TIMEOUT_SECONDS") or 120)
        self._session_factory = session_factory
        self._client = None
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                self._client = self._session_factory().client("athena")
            return self._client

    def run(self, sql: str) -> list[dict]:
        from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

        try:
            return self._run(sql)
        except NoCredentialsError:
            self._client = None  # retry once with a fresh session (the role may have been attached since)
            try:
                return self._run(sql)
            except (BotoCoreError, ClientError) as e:
                if isinstance(e, NoCredentialsError):
                    self._client = None  # never keep a client without credentials cached
                raise QueryError(f"AWS: {e}")
        except (BotoCoreError, ClientError) as e:
            raise QueryError(f"AWS: {e}")

    def _run(self, sql: str) -> list[dict]:
        kwargs: dict[str, Any] = {
            "QueryString": with_trail(sql),  # trail comment: app, version, module, function
            "QueryExecutionContext": {"Database": self.database},
            "WorkGroup": self.workgroup,
        }
        if self.output:
            kwargs["ResultConfiguration"] = {"OutputLocation": self.output}
        qid = self.client.start_query_execution(**kwargs)["QueryExecutionId"]

        delay, start = 0.25, time.time()
        while True:
            st = self.client.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
            state = st["State"]
            if state == "SUCCEEDED":
                break
            if state in ("FAILED", "CANCELLED"):
                raise QueryError(st.get("StateChangeReason", state))
            if time.time() - start > self.timeout:
                self.client.stop_query_execution(QueryExecutionId=qid)
                raise QueryError("Athena query timed out")
            time.sleep(delay)
            delay = min(delay * 1.5, 2.0)

        rows: list[dict] = []
        cols: list[str] | None = None
        types: list[str] = []
        for page in self.client.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
            if cols is None:
                meta = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
                cols = [c["Name"] for c in meta]
                types = [c["Type"] for c in meta]
                data = page["ResultSet"]["Rows"][1:]  # first row is the header
            else:
                data = page["ResultSet"]["Rows"]
            for r in data:
                vals = [d.get("VarCharValue") for d in r["Data"]]
                rows.append({c: _convert(v, t) for c, v, t in zip(cols, vals, types)})
        return rows


def _convert(v, t):
    if v is None:
        return None
    if t in ("double", "float", "real", "decimal"):
        return float(v)
    if t in ("integer", "bigint", "smallint", "tinyint"):
        return int(v)
    return v


class DuckRunner:
    """Local mock: every "<schema>.<table>.csv" in `tables_dir` is loaded as that table, so the same SQL
    runs without AWS. Types are auto-detected; a "<schema>.<table>.types.json" next to the CSV
    ({"col": "VARCHAR"}) forces the types that must match Athena."""

    _DEFAULT_SCHEMA = re.compile(r'(?<![\w"])default\.')  # "default" is a reserved word in DuckDB
    _REGEXP_LIKE = re.compile(r"\bregexp_like\(", re.I)    # Athena/Trino name -> DuckDB regexp_matches

    def __init__(self, tables_dir: str):
        import duckdb

        self.con = duckdb.connect()
        for f in sorted(Path(tables_dir).glob("*.*.csv")):
            name = f.name[: -len(".csv")]
            types = f.with_name(name + ".types.json")
            opts = f"types={json.loads(types.read_text())!r}" if types.exists() else "header=true"
            schema, table = name.split(".")
            self.con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            self.con.execute(f'CREATE TABLE "{schema}"."{table}" AS SELECT * FROM read_csv_auto(?, {opts})', [str(f)])
        self._lock = threading.Lock()

    def run(self, sql: str) -> list[dict]:
        import duckdb

        with self._lock:
            try:
                q = self._REGEXP_LIKE.sub("regexp_matches(", self._DEFAULT_SCHEMA.sub('"default".', with_trail(sql)))
                cur = self.con.execute(q)
            except duckdb.Error as e:  # same contract as Athena
                raise QueryError(str(e).splitlines()[0])
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


class CachedRunner:
    """TTL cache in front of a runner, so identical queries don't hit (and pay) Athena again."""

    def __init__(self, inner, ttl: int, max_items: int = 2000):
        self.inner = inner
        self.ttl = ttl
        self.max_items = max_items
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def run(self, sql: str) -> list[dict]:
        key = hashlib.sha256(str(sql).encode()).hexdigest()  # bare SQL: the trail never splits the cache
        with self._lock:
            hit = self._data.get(key)
            if hit and time.time() - hit[0] < self.ttl:
                return hit[1]
        res = self.inner.run(sql)
        with self._lock:
            if len(self._data) >= self.max_items:
                self._data.clear()
            self._data[key] = (time.time(), res)
        return res


def make_runner(ttl: int | None = None):
    """DuckDB mock when MOCK_TABLES_DIR is set (local, tests), else Athena; cached CACHE_TTL_SECONDS."""
    mock = os.getenv("MOCK_TABLES_DIR")
    inner = DuckRunner(mock) if mock else AthenaRunner()
    return CachedRunner(inner, int(ttl if ttl is not None else os.getenv("CACHE_TTL_SECONDS") or 900))
