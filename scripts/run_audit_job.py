"""Run the read-only audit as a bounded Cloud Run Job with private Toolbox.

The reviewed manifest must be copied into the image. This wrapper never deploys
or changes cloud resources. Only the two child processes it starts are stopped.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_postings import timestamp, validate_manifest


class JobError(Exception):
    def __init__(self, code, message, exit_code=1):
        self.code, self.message, self.exit_code = code, message, exit_code
        super().__init__(message)


def bounded_seconds(value):
    try:
        number = int(value)
    except (ValueError, TypeError):
        raise argparse.ArgumentTypeError("Timeout must be an integer.") from None
    if not 1 <= number <= 1800:
        raise argparse.ArgumentTypeError("Timeout must be between 1 and 1800 seconds.")
    return number


def port_open():
    try:
        with socket.create_connection(("127.0.0.1", 5000), timeout=.25):
            return True
    except OSError:
        return False


def wait_for_toolbox(process, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise JobError("toolbox_exited", "Toolbox exited before readiness.")
        if port_open():
            if process.poll() is not None:
                raise JobError("toolbox_exited", "Toolbox exited during readiness.")
            return
        time.sleep(.25)
    raise JobError("toolbox_startup_timeout", "Toolbox did not become ready.", 124)


def terminate_child(process):
    """Never search for or signal other processes or process groups."""
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run_job(manifest_path, evaluated_at, *, timeout_seconds=900, startup_timeout_seconds=30):
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise JobError("missing_project", "GOOGLE_CLOUD_PROJECT must be explicitly configured.")
    if os.environ.get("BIGQUERY_DATASET", "a4i_food") != "a4i_food":
        raise JobError("dataset_mismatch", "Audit dataset must match the configured a4i_food MCP source.")
    timestamp(evaluated_at)
    manifest_path = Path(manifest_path).resolve()
    validate_manifest(json.loads(manifest_path.read_text()))
    if port_open():
        raise JobError("port_in_use", "Loopback port 5000 is occupied; no existing process was changed.")
    child_env = os.environ.copy()
    child_env["TOOLBOX_URL"] = "http://127.0.0.1:5000"
    children = []
    try:
        toolbox = subprocess.Popen([
            os.getenv("TOOLBOX_BINARY", "/usr/local/bin/toolbox"),
            "--address=127.0.0.1", "--port=5000", f"--config={ROOT / 'agent/tools.yaml'}",
        ], env=child_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        children.append(toolbox)
        wait_for_toolbox(toolbox, min(startup_timeout_seconds, timeout_seconds))
        audit = subprocess.Popen([
            sys.executable, str(ROOT / "scripts/audit_postings.py"),
            "--manifest", str(manifest_path), "--evaluated-at", evaluated_at,
            "--project", project, "--dataset", "a4i_food", "--compact",
        ], env=child_env)
        children.append(audit)
        deadline = time.monotonic() + timeout_seconds
        while audit.poll() is None:
            if toolbox.poll() is not None:
                raise JobError("toolbox_exited", "Toolbox exited before the audit completed.")
            if time.monotonic() >= deadline:
                raise JobError("audit_timeout", "Audit exceeded its bounded execution time.", 124)
            time.sleep(.25)
        code = audit.returncode
        return code if code >= 0 else 128 - code
    finally:
        for child in reversed(children):
            try:
                terminate_child(child)
            except OSError:
                # A process can exit between poll and terminate; continue cleanup.
                pass


def interrupted(signum, _frame):
    raise JobError("interrupted", "Audit job interrupted; owned child processes are being stopped.",
                   128 + signum)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--evaluated-at", required=True)
    parser.add_argument("--timeout-seconds", type=bounded_seconds, default=900)
    parser.add_argument("--startup-timeout-seconds", type=bounded_seconds, default=30)
    args = parser.parse_args(argv)
    previous = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return run_job(args.manifest, args.evaluated_at, timeout_seconds=args.timeout_seconds,
                       startup_timeout_seconds=args.startup_timeout_seconds)
    except JobError as exc:
        print(json.dumps({"status": "blocked", "error_code": exc.code, "message": exc.message}))
        return exc.exit_code
    except Exception as exc:
        print(json.dumps({"status": "blocked", "error_code": type(exc).__name__,
                          "message": "Audit job could not start; check its reviewed manifest and runtime configuration."}))
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
