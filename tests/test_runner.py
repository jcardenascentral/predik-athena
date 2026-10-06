import boto3
import pytest
from botocore.exceptions import NoCredentialsError
from botocore.stub import Stubber

from predik_athena import AthenaRunner, CachedRunner, QueryError, configure_trail, sql_source


def stubbed_runner(**kw):
    client = boto3.client("athena", region_name="us-east-1", aws_access_key_id="ASIAX", aws_secret_access_key="x",
                          aws_session_token="t")
    sent = []
    client.meta.events.register("provide-client-params.athena.StartQueryExecution",
                                lambda params, **_: sent.append(params))
    r = AthenaRunner(workgroup="siteselector", database="gi_site_selector", **kw)
    r._client = client
    return r, Stubber(client), sent


RESULTS = {"ResultSet": {
    "ResultSetMetadata": {"ColumnInfo": [{"Name": "zona", "Type": "varchar"}, {"Name": "tam", "Type": "bigint"},
                                         {"Name": "pen", "Type": "double"}]},
    "Rows": [{"Data": [{"VarCharValue": "zona"}, {"VarCharValue": "tam"}, {"VarCharValue": "pen"}]},
             {"Data": [{"VarCharValue": "JALISCO"}, {"VarCharValue": "120"}, {"VarCharValue": "0.25"}]},
             {"Data": [{"VarCharValue": "COLIMA"}, {}, {"VarCharValue": "0.1"}]}]}}


def test_runs_in_its_workgroup_with_trail_and_types():
    configure_trail("SiteSelector", "1.2.3")
    r, stub, sent = stubbed_runner()
    stub.add_response("start_query_execution", {"QueryExecutionId": "q1"})
    stub.add_response("get_query_execution", {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}})
    stub.add_response("get_query_results", RESULTS)

    @sql_source("zonas")
    def resumen_sql():
        return "SELECT zona, tam, pen FROM zonas"

    with stub:
        rows = r.run(resumen_sql())
    assert rows == [{"zona": "JALISCO", "tam": 120, "pen": 0.25}, {"zona": "COLIMA", "tam": None, "pen": 0.1}]
    assert sent[0]["WorkGroup"] == "siteselector"
    assert sent[0]["QueryExecutionContext"] == {"Database": "gi_site_selector"}
    assert "ResultConfiguration" not in sent[0]              # the workgroup enforces the output
    q = sent[0]["QueryString"]
    assert q.startswith("/* SiteSelector 1.2.3 ") and '"module":"zonas"' in q and q.endswith("FROM zonas")


def test_failed_query_and_aws_errors_become_query_errors():
    r, stub, _ = stubbed_runner()
    stub.add_response("start_query_execution", {"QueryExecutionId": "q1"})
    stub.add_response("get_query_execution", {"QueryExecution": {"Status": {
        "State": "FAILED", "StateChangeReason": "Query exhausted resources: bytes scanned limit"}}})
    stub.add_client_error("start_query_execution", "AccessDeniedException", "not authorized on workgroup/primary")
    with stub:
        with pytest.raises(QueryError, match="bytes scanned limit"):
            r.run("SELECT 1")
        with pytest.raises(QueryError, match="AWS: .*AccessDenied"):
            r.run("SELECT 1")


def test_no_credentials_retries_once_with_a_fresh_session():
    made = []

    class NoCreds:
        def start_query_execution(self, **kw):
            raise NoCredentialsError()

    class Session:
        def client(self, name):
            made.append(name)
            return NoCreds()

    r = AthenaRunner(session_factory=Session)
    with pytest.raises(QueryError, match="Unable to locate credentials"):
        r.run("SELECT 1")
    assert made == ["athena", "athena"] and r._client is None


def test_cache():
    calls = []

    class Inner:
        def run(self, sql):
            calls.append(sql)
            return [{"n": len(calls)}]

    c = CachedRunner(Inner(), ttl=60)
    assert c.run("SELECT 1") == c.run("SELECT 1") == [{"n": 1}]
    assert c.run("SELECT 2") == [{"n": 2}]
