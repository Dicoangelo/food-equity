#!/usr/bin/env bash
set -euo pipefail
# Run only in the verified student Cloud Shell. This script changes no IAM or billing.
project="qwiklabs-gcp-04-b992b9561e32"
region="us-east1"
service="${FOOD_BROKER_SERVICE:-seattle-food-broker}"
active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
[[ "$active" == student-*@qwiklabs.net ]] || { echo "Stop: expected active lab student identity."; exit 1; }
[[ "$(gcloud config get-value project 2>/dev/null)" == "$project" ]] || { echo "Stop: wrong active project."; exit 1; }
cd "$(dirname "$0")/.."
gcloud run deploy "$service" --source . --project "$project" --region "$region" \
  --no-allow-unauthenticated --min-instances 0 --max-instances 2 \
  --cpu 1 --memory 1Gi --concurrency 8 --timeout 240 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$project,GOOGLE_CLOUD_LOCATION=$region,GOOGLE_GENAI_USE_VERTEXAI=true,GEMINI_MODEL=gemini-2.5-flash"
gcloud run services describe "$service" --project "$project" --region "$region" --format='value(status.url)'
