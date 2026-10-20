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
  --no-allow-unauthenticated --min 1 --max 4 --min-instances 0 --max-instances 4 \
  --cpu 2 --memory 2Gi --concurrency 4 --timeout 240 --cpu-boost \
  --startup-probe "httpGet.path=/healthz,httpGet.port=8080,periodSeconds=5,timeoutSeconds=2,failureThreshold=24" \
  --liveness-probe "httpGet.path=/healthz,httpGet.port=8080,periodSeconds=30,timeoutSeconds=3,failureThreshold=3" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$project,GOOGLE_CLOUD_LOCATION=$region,GOOGLE_GENAI_USE_VERTEXAI=true,GEMINI_MODEL=gemini-2.5-flash"
gcloud run services describe "$service" --project "$project" --region "$region" --format='value(status.url)'
