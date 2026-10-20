#!/usr/bin/env bash
set -euo pipefail
# Keep the team's short entrypoint on the verified FastAPI + MCP deployment path.
# The canonical script checks the lab account/project and preserves Cloud Run IAM.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deploy_cloud_run.sh" "$@"
