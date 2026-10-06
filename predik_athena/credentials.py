"""AWS sessions with temporary credentials only.

Where the credentials come from, in order (boto3's default chain, nothing stored in the app):

- ECS task role / EC2 instance profile  -> production and staging (rotated by AWS every hour)
- AWS_PROFILE with IAM Identity Center   -> a developer's laptop (`aws sso login`)
- PREDIK_ASSUME_ROLE_ARN                 -> on top of any of the above: assume a narrower role
                                            (1 h credentials, refreshed automatically before expiry)

Long-term access keys (AKIA...) are refused when PREDIK_FORBID_LONG_TERM_KEYS=true (set it in
production) and logged as a warning otherwise.
"""
from __future__ import annotations

import logging
import os

import boto3
from botocore.credentials import DeferredRefreshableCredentials
from botocore.session import get_session

log = logging.getLogger("predik_athena")


class CredentialsError(RuntimeError):
    """The app is configured with credentials it must not use."""


def region() -> str:
    return os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes")


def check_long_term_keys(session: boto3.Session, forbid: bool) -> None:
    creds = session.get_credentials()
    if creds is None or not getattr(creds, "access_key", "").startswith("AKIA"):
        return  # no credentials yet, or temporary ones (ASIA...)
    msg = ("long-term AWS access key in use (AKIA...): use the task/instance role, `aws sso login` "
           "or PREDIK_ASSUME_ROLE_ARN instead")
    if forbid:
        raise CredentialsError(msg)
    log.warning(msg)


def make_session(role_arn: str | None = None, *, session_name: str | None = None, duration: int | None = None,
                 external_id: str | None = None, forbid_long_term: bool | None = None) -> boto3.Session:
    """A boto3 Session for the app. Arguments default to the PREDIK_* environment variables."""
    role_arn = role_arn or os.getenv("PREDIK_ASSUME_ROLE_ARN") or None
    forbid = _truthy(os.getenv("PREDIK_FORBID_LONG_TERM_KEYS")) if forbid_long_term is None else forbid_long_term
    base = boto3.Session(region_name=region())
    check_long_term_keys(base, forbid)
    if not role_arn:
        return base

    name = session_name or os.getenv("PREDIK_ROLE_SESSION_NAME") or f"predik-{os.getenv('AUTH_APP_ID') or 'app'}"
    seconds = int(duration or os.getenv("PREDIK_ASSUME_ROLE_SECONDS") or 3600)
    ext = external_id or os.getenv("PREDIK_ASSUME_ROLE_EXTERNAL_ID") or None
    sts = base.client("sts")

    def fetch() -> dict:
        kw = {"RoleArn": role_arn, "RoleSessionName": name[:64], "DurationSeconds": seconds}
        if ext:
            kw["ExternalId"] = ext
        c = sts.assume_role(**kw)["Credentials"]
        return {"access_key": c["AccessKeyId"], "secret_key": c["SecretAccessKey"],
                "token": c["SessionToken"], "expiry_time": c["Expiration"].isoformat()}

    # Deferred: no AWS call at import/startup; the first query assumes the role, later ones refresh it.
    bs = get_session()
    bs._credentials = DeferredRefreshableCredentials(refresh_using=fetch, method="sts-assume-role")
    bs.set_config_variable("region", region())
    return boto3.Session(botocore_session=bs)
