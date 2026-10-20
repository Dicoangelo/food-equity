"""Authenticated live request. Never prints the identity token or saves credentials."""
import argparse
import json
import math
import subprocess
import urllib.error
import urllib.request

PROJECT = "qwiklabs-gcp-04-b992b9561e32"
REGION = "us-east1"
SERVICE = "seattle-food-broker"


def positive_number(maximum):
    def parse(value):
        try:
            number = float(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError("must be a number") from exc
        if not math.isfinite(number) or not 0 < number <= maximum:
            raise argparse.ArgumentTypeError(f"must be finite, greater than 0 and at most {maximum}")
        return number
    return parse


def query_text(value):
    value = value.strip()
    if not 5 <= len(value) <= 4000:
        raise argparse.ArgumentTypeError("query must contain 5–4000 characters")
    return value


def arguments(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Verify authenticated ADK + MCP + Gemini execution. An honest no-match "
            "result passes by default; --require-match also requires a viable proposal."
        )
    )
    parser.add_argument(
        "--query", type=query_text,
        default="One pallet of chilled spinach and mixed leafy greens from a Seattle grocer.",
        help="donor offer text (5–4000 characters; keep its quantity/storage consistent with the flags)",
    )
    parser.add_argument("--storage", choices=("cold", "ambient", "frozen"), default="cold")
    parser.add_argument("--quantity", "--pallets", dest="pallet_count", type=positive_number(10000),
                        default=1.0, help="quantity in pallets, including fractions (default: 1)")
    parser.add_argument("--hours", type=positive_number(168), default=6.0,
                        help="hours remaining before the pickup deadline (default: 6)")
    parser.add_argument("--require-match", action="store_true",
                        help="fail unless execution returns a viable proposal with a Gemini brief")
    return parser.parse_args(argv)


def assess_response(data, require_match=False):
    """Keep cloud execution health distinct from whether today's corpus can accept an offer."""
    trace = data.get("execution_trace", [])
    events = trace if isinstance(trace, list) and all(isinstance(e, dict) for e in trace) else []
    stages = {e.get("stage") for e in events}
    required_stages = {
        "mcp_discovery", "mcp_call", "mcp_result", "deterministic_constraints",
        "adk_function_call", "adk_function_result", "adk_complete",
    }
    positive = (
        data.get("status") == "success" and data.get("generation_mode") == "adk_gemini"
        and isinstance(data.get("winner"), dict) and bool(data["winner"])
        and isinstance(data.get("match_brief"), dict) and bool(data["match_brief"])
    )
    honest_no_match = (
        data.get("status") == "no_viable_match" and data.get("generation_mode") == "constraints"
        and data.get("winner") is None
    )
    execution_passed = (
        required_stages <= stages and "execution_error" not in stages
        and (positive or honest_no_match)
    )
    return {
        "passed": execution_passed and (positive or not require_match),
        "execution_passed": execution_passed,
        "viable_match_passed": execution_passed and positive,
        "expectation": "viable_match" if require_match else "healthy_execution",
        "missing_stages": sorted(required_stages - stages),
    }


def main(argv=None):
    args = arguments(argv)
    offer = {
        "posting_text": args.query, "required_storage": args.storage,
        "pallet_count": args.pallet_count, "hours_remaining": args.hours,
        "donor_name": "Seattle hackathon demo", "donor_lat": 47.6062, "donor_lon": -122.3321,
    }
    try:
        url = subprocess.check_output([
            "gcloud", "run", "services", "describe", SERVICE, "--project", PROJECT,
            "--region", REGION, "--format=value(status.url)",
        ], text=True, stderr=subprocess.PIPE).strip()
        token = subprocess.check_output(
            ["gcloud", "auth", "print-identity-token"], text=True, stderr=subprocess.PIPE
        ).strip()
        request = urllib.request.Request(
            url + "/api/match", data=json.dumps(offer).encode(),
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=220) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            raise ValueError("Expected a response object")
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        # Do not print provider bodies, subprocess output, headers or credential values.
        print(json.dumps({
            "passed": False, "execution_passed": False, "viable_match_passed": False,
            "error_code": type(exc).__name__,
            "http_status": exc.code if isinstance(exc, urllib.error.HTTPError) else None,
        }, indent=2))
        return 1
    verdict = assess_response(data, require_match=args.require_match)
    print(json.dumps({
        **verdict, "service_url": url, "input": offer, "status": data.get("status"),
        "generation_mode": data.get("generation_mode"), "execution_trace": data.get("execution_trace"),
        "winner": data.get("winner"), "requires_confirmation": data.get("requires_confirmation"),
    }, indent=2))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
