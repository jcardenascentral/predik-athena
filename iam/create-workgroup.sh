#!/usr/bin/env bash
# One Athena workgroup per app, with its settings ENFORCED (the app cannot override them):
#   - results in s3://<bucket>/athenaoutput/<app>/, encrypted (SSE-S3)
#   - per-query scan limit: a runaway query is cancelled by Athena, not billed in full
#   - CloudWatch metrics per app
#
#   bash iam/create-workgroup.sh siteselector            # 2 GB per query (default)
#   bash iam/create-workgroup.sh siteselector 5368709120 # 5 GB
set -euo pipefail
APP="${1:?usage: create-workgroup.sh <app_id> [bytes_per_query]}"
BYTES="${2:-2147483648}"
OUTPUT="${ATHENA_RESULTS_BUCKET:-s3://cadsourcedata/athenaoutput}/$APP/"
CONF="ResultConfigurationUpdates={OutputLocation=$OUTPUT,EncryptionConfiguration={EncryptionOption=SSE_S3}},EnforceWorkGroupConfiguration=true,PublishCloudWatchMetricsEnabled=true,BytesScannedCutoffPerQuery=$BYTES"

if aws athena get-work-group --work-group "$APP" >/dev/null 2>&1; then
  aws athena update-work-group --work-group "$APP" --configuration-updates "$CONF"
  echo "workgroup $APP: updated"
else
  aws athena create-work-group --name "$APP" --tags Key=app,Value="$APP" --configuration \
    "ResultConfiguration={OutputLocation=$OUTPUT,EncryptionConfiguration={EncryptionOption=SSE_S3}},EnforceWorkGroupConfiguration=true,PublishCloudWatchMetricsEnabled=true,BytesScannedCutoffPerQuery=$BYTES"
  echo "workgroup $APP: created"
fi
