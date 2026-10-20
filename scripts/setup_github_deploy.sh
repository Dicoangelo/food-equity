#!/usr/bin/env bash
# One-time, explicitly invoked IAM bootstrap. Never run inside the deploy job.
# Uses the operator's existing gcloud session; creates no service-account key.
set -euo pipefail

PROJECT=qwiklabs-gcp-04-b992b9561e32
NUMBER=803337281376
REGION=us-east1
POOL=food-equity-github
PROVIDER=team-main
DEPLOY_SA="food-equity-deploy@${PROJECT}.iam.gserviceaccount.com"
REPO_ID=1427073876
OWNER_ID=105653169
WORKFLOW="niatalwalkar/food-equity/.github/workflows/cloudrun.yml@refs/heads/main"

if [[ "${1:-}" != "--apply" || "$#" != 1 ]]; then
  cat <<'PLAN'
Plan only; use --apply after reviewing these grants.
Trust: only niatalwalkar/food-equity (numeric repository/owner IDs),
main branch, and .github/workflows/cloudrun.yml.
Creates a dedicated deploy service account and an OIDC workload identity provider.
Enables IAM, IAM Credentials, Security Token Service and Cloud Resource Manager APIs.
Grants deployer Cloud Run Source Developer + Service Usage Consumer on this lab
project, and Service Account User on the existing runtime identity.
Grants the existing build identity Cloud Run Builder on the project if needed.
No billing changes, service-account keys, public access changes or deployment.
PLAN
  exit 0
fi

[[ "$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')" == "$NUMBER" ]]
RUNTIME_SA="$(gcloud run services describe seattle-food-broker \
  --project "$PROJECT" --region "$REGION" \
  --format='value(spec.template.spec.serviceAccountName)')"
[[ "$RUNTIME_SA" == *".gserviceaccount.com" ]]
BUILD_SA="$(gcloud builds get-default-service-account --project "$PROJECT" --region "$REGION")"
BUILD_SA="${BUILD_SA##*/}"
[[ "$BUILD_SA" == *".gserviceaccount.com" ]]

gcloud services enable iam.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com cloudresourcemanager.googleapis.com --project "$PROJECT" --quiet
if ! gcloud iam service-accounts describe "$DEPLOY_SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create food-equity-deploy --project "$PROJECT" \
    --display-name="Food Equity GitHub deploy" --quiet
fi
if ! gcloud iam workload-identity-pools describe "$POOL" --location global \
  --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location global \
    --project "$PROJECT" --display-name="Food Equity GitHub" --quiet
fi
# The binding below applies to this pool's repository_id attribute. Never reuse
# a pool containing a different provider with a weaker trust condition.
EXISTING_PROVIDERS="$(gcloud iam workload-identity-pools providers list \
  --workload-identity-pool "$POOL" --location global --project "$PROJECT" --format='value(name)')"
while IFS= read -r existing; do
  [[ -z "$existing" || "${existing##*/}" == "$PROVIDER" ]] || {
    echo "Stop: dedicated pool contains an unexpected provider; review its trust policy." >&2
    exit 1
  }
done <<< "$EXISTING_PROVIDERS"

MAPPING="google.subject=assertion.sub,attribute.repository_id=assertion.repository_id,attribute.repository_owner_id=assertion.repository_owner_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref"
CONDITION="assertion.repository_id=='${REPO_ID}' && assertion.repository_owner_id=='${OWNER_ID}' && assertion.ref=='refs/heads/main' && assertion.workflow_ref=='${WORKFLOW}'"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
  --workload-identity-pool "$POOL" --location global --project "$PROJECT" >/dev/null 2>&1; then
  # This script owns only its specifically named provider.
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" \
    --workload-identity-pool "$POOL" --location global --project "$PROJECT" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION" --quiet
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
    --workload-identity-pool "$POOL" --location global --project "$PROJECT" \
    --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="$MAPPING" --attribute-condition="$CONDITION" --quiet
fi
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY_SA" --project "$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository_id/${REPO_ID}" --quiet >/dev/null
for role in roles/run.sourceDeveloper roles/serviceusage.serviceUsageConsumer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${DEPLOY_SA}" \
    --role="$role" --condition=None --quiet >/dev/null
done
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --project "$PROJECT" \
  --role=roles/iam.serviceAccountUser --member="serviceAccount:${DEPLOY_SA}" --quiet >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:${BUILD_SA}" \
  --role=roles/run.builder --condition=None --quiet >/dev/null

echo "Federation configured for team main only. No deployment was performed."
echo "Provider: projects/${NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
