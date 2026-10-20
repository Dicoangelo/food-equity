#!/usr/bin/env bash
# Stage exact committed source, verify its tagged revision, optionally promote it.
# Requires the existing public demo and preconfigured student/CI permissions.
# Never enables APIs, changes IAM/billing, reloads data, or writes credentials.
set -euo pipefail
project="qwiklabs-gcp-04-b992b9561e32"
region="us-east1"
service="seattle-food-broker"
ci_account="food-equity-deploy@$project.iam.gserviceaccount.com"
ci=false
promote=false
evidence_dir=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ci) ci=true; shift ;;
    --promote) promote=true; shift ;;
    --evidence-dir) [[ $# -ge 2 ]] || exit 2; evidence_dir="$2"; shift 2 ;;
    --help|-h)
      echo 'Usage: bash scripts/deploy_cloud_run.sh [--ci] [--promote] [--evidence-dir DIRECTORY]'
      echo 'Default: stage and verify only. --promote releases the exact checked revision.'
      exit 0 ;;
    *) echo 'Unknown option; use --help.' >&2; exit 2 ;;
  esac
done
fail() { echo "Stop: $*" >&2; exit 1; }
root="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)"
cd "$root"
python="${FOOD_DEPLOY_PYTHON:-python3}"
command -v gcloud >/dev/null || fail 'gcloud is required.'
"$python" -c 'import websockets' || fail 'Install requirements.txt in the selected Python environment.'
[[ -z "$(git status --porcelain)" ]] || fail 'Commit all source changes before deploying a clean HEAD.'
sha="$(git rev-parse HEAD)"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || fail 'Expected an exact Git commit.'
active="$(gcloud auth list --filter=status:ACTIVE --format='value(account)')"
if "$ci"; then
  [[ "$active" == "$ci_account" ]] || fail 'CI must use the dedicated deployment service account.'
else
  [[ "$active" =~ ^student-[^[:space:]@]+@qwiklabs\.net$ ]] || fail 'Expected the active lab student account.'
fi
[[ "$(gcloud config get-value project 2>/dev/null)" == "$project" ]] || fail 'Wrong active project.'
# CI is for the canonical main branch only, including immediately before promotion.
check_main() {
  local remote_sha
  remote_sha="$(git ls-remote --exit-code origin refs/heads/main | awk '{print $1}')"
  [[ "$remote_sha" == "$sha" ]] || fail 'This build is no longer remote main; no promotion.'
}
if "$ci"; then check_main; fi
if [[ -z "$evidence_dir" ]]; then evidence_dir="$(mktemp -d /tmp/food-equity-release.XXXXXX)"; fi
mkdir -p "$evidence_dir"
evidence_dir="$(cd "$evidence_dir" && pwd)"
[[ "$evidence_dir" != "$root" && "$evidence_dir" != "$root/"* ]] || fail 'Evidence must be outside the source checkout.'
[[ -z "$(ls -A "$evidence_dir")" ]] || fail 'Use a new, empty evidence directory.'
workspace="$(mktemp -d /tmp/food-equity-build.XXXXXX)"
trap 'rm -rf "$workspace"' EXIT
# Only tracked HEAD files enter the build, never local gcloud/GitHub auth artifacts.
git archive "$sha" | tar -x -C "$workspace"
nonce="$("$python" -c 'import secrets; print(secrets.token_hex(3))')"
suffix="g${sha:0:10}-$(date -u +%s)-$nonce"
tag="$suffix"
revision="$service-$suffix"

# Raw service metadata (possibly secret-bearing env values) stays in process memory.
# Persist only a nonreversible config digest, runtime identity, and public traffic map.
snapshot_service() {
  gcloud run services describe "$service" --project "$project" --region "$region" --format=json --quiet |
    "$python" -c '
import copy,hashlib,json,sys
s=json.load(sys.stdin); spec=copy.deepcopy(s["spec"]["template"]["spec"])
for c in spec.get("containers",[]):
 c.pop("image",None)
 c["env"]=[e for e in c.get("env",[]) if e.get("name")!="APP_COMMIT_SHA"]
traffic=sorted([{ "revision":r["revisionName"],"percent":r.get("percent",0)}
 for r in s["status"].get("traffic",[]) if r.get("percent",0)>0],key=lambda r:r["revision"])
print(json.dumps({"runtime_service_account":spec.get("serviceAccountName"),
 "config_sha256":hashlib.sha256(json.dumps(spec,sort_keys=True).encode()).hexdigest(),"traffic":traffic}))'
}
snapshot_service > "$evidence_dir/before.json"
runtime_account="$("$python" -c 'import json,sys; print(json.load(open(sys.argv[1]))["runtime_service_account"] or "")' "$evidence_dir/before.json")"
[[ "$runtime_account" == *"@$project.iam.gserviceaccount.com" || "$runtime_account" == *@developer.gserviceaccount.com ]] || fail 'Existing runtime service account could not be verified.'
echo "Staging commit $sha as $revision; evidence: $evidence_dir"
gcloud run deploy "$service" --source "$workspace" --project "$project" --region "$region" \
  --service-account "$runtime_account" --no-traffic --tag "$tag" --revision-suffix "$suffix" \
  --update-env-vars "APP_COMMIT_SHA=$sha" --quiet
# Resolve and verify this tag, never latestReadyRevisionName.
verifier_args=(--candidate-tag "$tag" --expected-revision "$revision" --expected-commit "$sha" --public)
if "$ci"; then verifier_args+=(--expected-deployer "$ci_account"); fi
"$python" "$workspace/scripts/verify_live_release.py" "${verifier_args[@]}" --output "$evidence_dir/core.json"
candidate_url="$("$python" -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r["runtime_passed"]; print(r["target"]["url"])' "$evidence_dir/core.json")"
# Two fresh sessions exercise the real typed tool-call path and fixed-clock behavior.
for attempt in 1 2; do
  "$python" "$workspace/scripts/verify_live_intake.py" --base-url "$candidate_url" > "$evidence_dir/live-$attempt.json"
done
snapshot_service > "$evidence_dir/checked.json"
"$python" - "$evidence_dir/before.json" "$evidence_dir/checked.json" <<'PY'
import json,sys
before,after=[json.load(open(p)) for p in sys.argv[1:]]
if before != after:
 raise SystemExit('Stop: runtime configuration or served traffic changed; review before promotion.')
PY
if "$promote"; then
  if "$ci"; then check_main; fi
  # Re-resolve the tag immediately before promotion, without consuming another model run.
  PYTHONPATH="$workspace" "$python" - "$tag" "$revision" "$ci" <<'PY'
import sys
from scripts.verify_live_release import CI_DEPLOYER,resolve_service
resolve_service(expected_revision=sys.argv[2],candidate_tag=sys.argv[1],
                expected_deployer=CI_DEPLOYER if sys.argv[3]=='true' else None)
PY
  gcloud run services update-traffic "$service" --project "$project" --region "$region" \
    --to-revisions "$revision=100" --quiet
  snapshot_service > "$evidence_dir/promoted.json"
  "$python" - "$evidence_dir/promoted.json" "$revision" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))
if s['traffic'] != [{'revision':sys.argv[2],'percent':100}]:
 raise SystemExit('Stop: traffic receipt does not confirm the exact checked revision.')
PY
  echo "Promoted exact verified revision: $revision"
else
  echo "Verified candidate only; production traffic unchanged: $candidate_url"
  echo 'To run staging, verification and promotion together, use --promote.'
fi
