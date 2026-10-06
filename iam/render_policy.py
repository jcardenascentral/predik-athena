#!/usr/bin/env python3
"""Minimal read-only IAM policy for one app: its workgroup, its Glue tables, its S3 prefixes.

    python iam/render_policy.py --app siteselector --database gi_site_selector \
        --s3 predik-geointelligence/tables/siteselector/ > policy.json

Take --database/--s3 from the real table locations: `aws glue get-table --database-name D --name T
--query Table.StorageDescriptor.Location`. No write access to data, no glue:Create*/Update*.
"""
from __future__ import annotations

import argparse
import json


def render(app: str, account: str, region: str, databases: list[str], s3: list[str], results: str) -> dict:
    arn = lambda svc, res: f"arn:aws:{svc}:{region}:{account}:{res}"  # noqa: E731
    buckets = sorted({p.split("/", 1)[0] for p in s3})
    res_bucket, _, res_prefix = results.removeprefix("s3://").partition("/")
    res_prefix = f"{res_prefix.rstrip('/')}/{app}/"
    return {"Version": "2012-10-17", "Statement": [
        {"Sid": "AthenaOwnWorkgroup", "Effect": "Allow",
         "Action": ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults",
                    "athena:StopQueryExecution", "athena:GetWorkGroup"],
         "Resource": arn("athena", f"workgroup/{app}")},
        {"Sid": "GlueReadOnly", "Effect": "Allow",
         "Action": ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartition",
                    "glue:GetPartitions", "glue:BatchGetPartition"],
         "Resource": [arn("glue", "catalog")] + [arn("glue", f"database/{d}") for d in databases]
                     + [arn("glue", f"table/{d}/*") for d in databases]},
        {"Sid": "S3ReadData", "Effect": "Allow", "Action": "s3:GetObject",
         "Resource": [f"arn:aws:s3:::{p.rstrip('/')}/*" for p in s3]},
        {"Sid": "S3ListData", "Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
         "Resource": [f"arn:aws:s3:::{b}" for b in buckets]},
        {"Sid": "AthenaResults", "Effect": "Allow",
         "Action": ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
         "Resource": f"arn:aws:s3:::{res_bucket}/{res_prefix}*"},
        {"Sid": "AthenaResultsBucket", "Effect": "Allow",
         "Action": ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"],
         "Resource": f"arn:aws:s3:::{res_bucket}"},
    ]}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--app", required=True)
    ap.add_argument("--database", action="append", required=True, help="Glue database (repeatable)")
    ap.add_argument("--s3", action="append", required=True, help="bucket/prefix/ with the data (repeatable)")
    ap.add_argument("--account", default="295347072680")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--results", default="s3://cadsourcedata/athenaoutput")
    a = ap.parse_args(argv)
    print(json.dumps(render(a.app, a.account, a.region, a.database, a.s3, a.results), indent=2))


if __name__ == "__main__":
    main()
