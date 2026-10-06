import os

import pytest


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch, tmp_path):
    """Tests never reach AWS nor read the developer's ~/.aws."""
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
              "PREDIK_ASSUME_ROLE_ARN", "PREDIK_FORBID_LONG_TERM_KEYS", "MOCK_TABLES_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    os.environ.pop("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", None)
