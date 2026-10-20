"""Authenticated live request. Never prints the identity token or saves credentials."""
import json
import subprocess
import urllib.request

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
REGION = "us-east1"
SERVICE = "seattle-food-broker"


def main():
    url = subprocess.check_output([
        "gcloud", "run", "services", "describe", SERVICE, "--project", PROJECT,
        "--region", REGION, "--format=value(status.url)",
    ], text=True).strip()
    token = subprocess.check_output(
        ["gcloud", "auth", "print-identity-token"], text=True
    ).strip()
    request = urllib.request.Request(
        url + "/api/match",
        data=json.dumps({
            "posting_text": "One pallet of chilled spinach and mixed leafy greens from a Seattle grocer.",
            "required_storage": "cold", "pallet_count": 1,
            "hours_remaining": 6, "donor_name": "Seattle hackathon demo",
            "donor_lat": 47.6062, "donor_lon": -122.3321,
        }).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=220) as response:
        data = json.load(response)
    stages = [e.get("stage") for e in data.get("execution_trace", [])]
    passed = (
        data.get("status") == "success" and data.get("generation_mode") == "adk_gemini"
        and "mcp_result" in stages and "adk_function_result" in stages
    )
    print(json.dumps({
        "passed": passed, "service_url": url, "status": data.get("status"),
        "generation_mode": data.get("generation_mode"), "execution_trace": data.get("execution_trace"),
        "winner": data.get("winner"), "requires_confirmation": data.get("requires_confirmation"),
    }, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
