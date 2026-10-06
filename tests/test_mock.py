import pytest

from predik_athena import QueryError, make_runner
from predik_athena.runner import CachedRunner, DuckRunner


def test_mock_runs_the_same_sql(tmp_path, monkeypatch):
    (tmp_path / "gi_site_selector.zonas.csv").write_text("zona,tam\nJALISCO,120\nCOLIMA,30\n")
    (tmp_path / "default.levels.csv").write_text("level\n10\n")
    (tmp_path / "default.levels.types.json").write_text('{"level": "VARCHAR"}')
    monkeypatch.setenv("MOCK_TABLES_DIR", str(tmp_path))
    db = make_runner()
    assert isinstance(db, CachedRunner) and isinstance(db.inner, DuckRunner)
    assert db.run("SELECT sum(tam) AS t FROM gi_site_selector.zonas") == [{"t": 150}]
    assert db.run("SELECT level FROM default.levels") == [{"level": "10"}]          # reserved word + types
    assert db.run("SELECT zona FROM gi_site_selector.zonas WHERE regexp_like(zona, '^J')") == [{"zona": "JALISCO"}]
    with pytest.raises(QueryError):
        db.run("SELECT nope FROM gi_site_selector.zonas")
