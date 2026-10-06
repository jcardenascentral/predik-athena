from datetime import datetime, timedelta, timezone

import boto3
import pytest

from predik_athena import CredentialsError, make_session


def test_long_term_key_refused_when_forbidden(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE000000000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    with pytest.raises(CredentialsError):
        make_session(forbid_long_term=True)
    monkeypatch.setenv("PREDIK_FORBID_LONG_TERM_KEYS", "true")
    with pytest.raises(CredentialsError):
        make_session()


def test_long_term_key_only_warns_by_default(monkeypatch, caplog):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE000000000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    make_session()
    assert "long-term AWS access key" in caplog.text


def test_temporary_keys_are_fine(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ASIAEXAMPLE000000000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "t")
    make_session(forbid_long_term=True)


def test_no_credentials_is_not_an_error_at_startup():
    """The role may be attached after the app starts: the first query retries (see AthenaRunner)."""
    assert make_session(forbid_long_term=True).get_credentials() is None


def test_assume_role_is_lazy_and_refreshes(monkeypatch):
    calls = []

    class FakeSTS:
        def assume_role(self, **kw):
            calls.append(kw)
            # 60 s left: inside botocore's refresh window, so every read refreshes
            return {"Credentials": {"AccessKeyId": f"ASIA{len(calls)}", "SecretAccessKey": "s", "SessionToken": "t",
                                    "Expiration": datetime.now(timezone.utc) + timedelta(seconds=60)}}

    orig = boto3.Session.client
    monkeypatch.setattr(boto3.Session, "client",
                        lambda self, name, *a, **k: FakeSTS() if name == "sts" else orig(self, name, *a, **k))
    s = make_session("arn:aws:iam::123456789012:role/SiteSelectorReader", external_id="ext-1",
                     session_name="predik-siteselector")
    assert calls == []                                    # nothing at startup
    assert s.get_credentials().get_frozen_credentials().access_key == "ASIA1"
    assert calls[0] == {"RoleArn": "arn:aws:iam::123456789012:role/SiteSelectorReader",
                        "RoleSessionName": "predik-siteselector", "DurationSeconds": 3600, "ExternalId": "ext-1"}
    assert s.get_credentials().get_frozen_credentials().access_key == "ASIA2"   # refreshed before expiry
    assert s.client("athena").meta.region_name == "us-east-1"
