"""Supervise loopback-only official Toolbox and the Cloud Run HTTP server."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
processes = []


def stop(*_):
    for process in reversed(processes):
        if process.poll() is None:
            process.terminate()


def main():
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        sys.exit("GOOGLE_CLOUD_PROJECT must be explicitly configured")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    toolbox = subprocess.Popen([
        os.getenv("TOOLBOX_BINARY", "/usr/local/bin/toolbox"),
        "--address=127.0.0.1", "--port=5000", f"--config={ROOT / 'agent/tools.yaml'}",
    ])
    processes.append(toolbox)
    for _ in range(120):
        if toolbox.poll() is not None:
            sys.exit("Toolbox exited before readiness")
        try:
            with socket.create_connection(("127.0.0.1", 5000), timeout=0.25):
                break
        except OSError:
            time.sleep(0.25)
    else:
        stop()
        sys.exit("Toolbox did not become ready")
    web = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "api:app", "--app-dir", str(ROOT / "agent"),
        "--host", "0.0.0.0", "--port", os.getenv("PORT", "8080"), "--no-access-log",
    ])
    processes.append(web)
    try:
        while all(p.poll() is None for p in processes):
            time.sleep(0.25)
    finally:
        stop()
        for process in processes:
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
        sys.exit(next((p.returncode for p in processes if p.returncode), 0))


if __name__ == "__main__":
    main()
