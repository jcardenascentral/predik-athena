"""predik_athena: Athena for Predik apps, always with temporary AWS credentials.

    from predik_athena import make_runner
    db = make_runner()                     # Athena, or DuckDB when MOCK_TABLES_DIR is set
    rows = db.run("SELECT 1 AS x")         # -> [{"x": 1}]

See README.md for the settings and the IAM setup (workgroup + minimal policy).
"""
from .credentials import CredentialsError, make_session
from .runner import AthenaRunner, CachedRunner, QueryError, make_runner
from .trail import configure_trail, sql_source, with_trail

__version__ = "1.0.0"

__all__ = ["AthenaRunner", "CachedRunner", "CredentialsError", "QueryError", "configure_trail",
           "make_runner", "make_session", "sql_source", "with_trail"]
