# Prashanth — GitHub main to the existing Cloud Run app

**20 October 2026.** Target: `qwiklabs-gcp-04-b992b9561e32` → `us-east1` → `seattle-food-broker`. [Public app](https://seattle-food-broker-6ukbp746xa-ue.a.run.app) · [Team repository](https://github.com/niatalwalkar/food-equity).

**One release path: local work → pull request → Nia's `main` → tests → a tagged candidate with no traffic → real core/Live checks → exact revision promotion.** Local builds and pushes do not update Cloud Run by themselves. Do not bypass this path with a local `gcloud run deploy` that immediately sends traffic to an unchecked revision.

The last verified production snapshot is source `d8f9c21`, revision `seattle-food-broker-00013-fum`, serving 100%. PR #10 merged to team main at `b6586cd`. Re-read the service before any release; this snapshot does not establish the state of a later teammate deployment.

**Connection status: prepared, not yet activated or proven.** The delivery branch contains `.github/workflows/cloudrun.yml`, the bootstrap script, guarded deploy helper and release telemetry. A merged workflow alone does not prove working federation or a passing deployment. IAM bootstrap does not build an image or change current traffic. Keep the current serving revision until a candidate passes.

## 1. Put Prashanth's code in team main

Keep existing local work intact. Commit it on its feature branch, push that branch, and open a pull request to `niatalwalkar/food-equity:main`. Review and merge it with the delivery tooling before release. The production container uses this repository's Dockerfile, FastAPI app, ADK broker and loopback MCP Toolbox.

For a fresh, separate copy of the prepared tooling before merge:

```bash
release_checkout="$(mktemp -d "$HOME/food-equity-delivery.XXXXXX")"
git clone --branch codex/cloudrun-delivery --single-branch \
  https://github.com/Dicoangelo/food-equity.git "$release_checkout"
cd "$release_checkout"
```

The release coordinator must publish that branch before sharing this command. It is a temporary review/bootstrap source. After merge, clone `--branch main` from `https://github.com/niatalwalkar/food-equity.git`. Do not deploy unmerged local changes as a substitute for team main.

## 2. One-time keyless connection

Nia/repository admin merges the reviewed workflow, allows its pinned GitHub Actions, and configures the `cloud-run-production` environment to allow deployment from `main` only. Apply any required reviewers there. Pull requests and forks run tests without the deployment job. **No repository variables or secrets are required**: the workflow contains these fixed, non-secret identifiers:

```text
Provider: projects/803337281376/locations/global/workloadIdentityPools/food-equity-github/providers/team-main
Deployer: food-equity-deploy@qwiklabs-gcp-04-b992b9561e32.iam.gserviceaccount.com
```

An authorized Google Cloud project administrator reviews and runs the bootstrap from the tooling checkout, using the existing authorized gcloud session. Successful student source deployment does not establish permission to configure federation or grant IAM.

```bash
# Plan only; no cloud calls or changes.
bash scripts/setup_github_deploy.sh

# One-time setup, only after the administrator approves these specific changes.
bash scripts/setup_github_deploy.sh --apply
```

Apply validates project number `803337281376`, discovers the existing runtime and default Cloud Build identities, then:

- Enables `iam.googleapis.com`, `iamcredentials.googleapis.com`, `sts.googleapis.com` and `cloudresourcemanager.googleapis.com`.
- Creates the dedicated deploy service account and `food-equity-github` pool as needed; creates/updates only its named `team-main` provider.
- Restricts GitHub claims to repository ID `1427073876`, owner ID `105653169`, `refs/heads/main`, and `niatalwalkar/food-equity/.github/workflows/cloudrun.yml@refs/heads/main`.
- Grants the restricted federated principal `roles/iam.workloadIdentityUser` on the deploy account; grants that account `roles/run.sourceDeveloper` and `roles/serviceusage.serviceUsageConsumer` on this project, plus `roles/iam.serviceAccountUser` on the existing runtime identity.
- Grants the existing default build identity `roles/run.builder` on the project.

Before reusing an existing pool, verify it contains only the intended trusted provider: the account binding is scoped to the pool's repository-ID attribute, while branch/owner/workflow restrictions live on the provider. Do not add unrelated providers to this dedicated pool.

The administrator needs API-enablement, pool/provider administration, service-account creation/policy editing and applicable project-IAM permissions. The script grants no Owner/Editor role, creates no service-account private key, changes no billing/invoker policy and does not deploy. Preserve existing runtime BigQuery/Vertex permissions. An organization policy may prevent setup; report its actual denial rather than broadening access. [Google's WIF setup](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines) · [Source-deployment roles](https://docs.cloud.google.com/run/docs/deploying-source-code#required_roles).

## 3. First deployment and proof of connection

After setup and merge, a relevant push to team `main` triggers **Test and deploy Seattle broker**. For the first controlled run, use GitHub **Actions → Test and deploy Seattle broker → Run workflow → main**. Do not run a parallel local promotion.

The workflow runs Python 3.12 tests, both Node suites and shell syntax checks before deployment authentication. It uses OIDC to impersonate the dedicated deployer, obtains a short-lived access token for up to 3,600 seconds, and passes it only through the deploy step's environment. Its temporary WIF configuration is excluded from Git/source archives and cleaned by the auth action; it is not a service-account private key. Tokens must never be printed, pasted, stored as GitHub secrets or uploaded as artifacts.

The helper archives only clean committed source, sets `APP_COMMIT_SHA`, and preserves the existing runtime account, environment settings and invoker policy. It stages a uniquely tagged revision with no production traffic, then verifies:

1. `/api/version` matches the exact source SHA and candidate revision.
2. Health, city, FoodKeeper, a real ADK/MCP bakery match and cold-capacity refusal.
3. Two fresh typed Live sessions preserve the deadline and complete the draft edit.
4. Runtime configuration and existing served traffic have not changed during checks.
5. The source commit is still remote team main immediately before promotion.

Only then does `--ci --promote` send 100% to that exact checked revision. Failed gates stop promotion. Deploy runs are serialized. Sanitized JSON receipts are retained as a GitHub Actions artifact for 14 days; the run summary links the app and version endpoint.

Confirm the workflow is successful, its promotion receipt names the checked revision at 100%, and the public URL reports the same main SHA:

```bash
curl --fail --silent --show-error \
  https://seattle-food-broker-6ukbp746xa-ue.a.run.app/api/version
```

The first real run must prove federation, source-build permissions and deployment; those remain unverified until its receipts exist. Candidate success alone does not prove physical microphone behavior.

## Controlled manual fallback

If federation is blocked, the release coordinator can use the **same reviewed main commit and helper** in the assigned student Cloud Shell. This is an explicit fallback until CI works, not a second concurrent release pipeline. Use a clean clone of team main, retain the current serving revision until checks pass, and never use `--ci` in a student session.

```bash
release_checkout="$(mktemp -d "$HOME/food-equity-release.XXXXXX")"
git clone --branch main --single-branch \
  https://github.com/niatalwalkar/food-equity.git "$release_checkout"
cd "$release_checkout"
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
node tests/test_ui_contract.js
node tests/test_recipient_explorer.js
bash scripts/deploy_cloud_run.sh --promote
```

This command requires the delivery PR to be merged first. The helper verifies the assigned-student identity class and target project without printing the account, requires a clean checkout, and performs the same candidate gates. Its default without `--promote` stages and checks only. Keep the source SHA, exact revision and evidence directory for the team. Do not run shared-data loaders, SQL replacement scripts, API enablement or IAM bootstrap as routine release steps.

## Diagnose and recover

After this delivery release is actually deployed, `/api/version` exposes the commit/revision; HTTP responses carry `X-Request-ID`, and sanitized application logs correlate request ID, route, status, latency and revision. They exclude bodies, authorization headers and transcripts. See [telemetry fields and query](TELEMETRY.md); version/health alone do not prove Gemini or BigQuery functionality.

If a promoted release regresses, the release coordinator can restore a known-good exact revision after confirming it still exists and coordinating with teammates:

```bash
gcloud run services update-traffic seattle-food-broker \
  --project=qwiklabs-gcp-04-b992b9561e32 --region=us-east1 \
  --to-revisions=seattle-food-broker-00013-fum=100 --quiet
```

**Why Actions/WIF:** it reuses the team's review/test flow and short-lived Google identity without storing a private key. A Cloud Build GitHub source connection is a possible administrator-selected replacement, requiring a GitHub App authorization, regional connection/trigger and a Google-managed Secret Manager token. It is not needed for this prepared path; do not activate both deployment systems. [Cloud Build connection requirements](https://docs.cloud.google.com/build/docs/automating-builds/github/connect-repo-github).
