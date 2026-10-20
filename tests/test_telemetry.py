"""Offline release identity and request-log privacy checks; no cloud calls."""
import importlib.util
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("food_telemetry", Path(__file__).parents[1] / "agent/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def http(monkeypatch, caplog):
    monkeypatch.setenv("APP_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("K_REVISION", "seattle-food-broker-00010-test")
    monkeypatch.setenv("K_SERVICE", "seattle-food-broker")
    monkeypatch.setattr(api.request_logger, "propagate", True)
    caplog.set_level(logging.INFO, logger=api.request_logger.name)
    return TestClient(api.app)


def request_logs(caplog):
    return [json.loads(record.getMessage()) for record in caplog.records
            if record.name == api.request_logger.name]


def test_version_and_health_are_local_only(http, monkeypatch, caplog):
    monkeypatch.setattr(api, "get_broker_agent", lambda: pytest.fail("No cloud initialization"))
    response = http.get("/api/version")
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"commit_sha": "a" * 40,
        "revision": "seattle-food-broker-00010-test", "service": "seattle-food-broker"}
    assert UUID(response.headers["x-request-id"])
    event = request_logs(caplog)[0]
    assert event["commit_sha"] == "a" * 40
    assert event["revision"] == response.json()["revision"]
    for route in ("/api/health", "/healthz"):
        assert http.get(route).json() == {"status":"ok", "cloud_dependencies":"not_checked"}


def test_missing_or_invalid_release_values_remain_unknown(http, monkeypatch):
    monkeypatch.delenv("APP_COMMIT_SHA")
    monkeypatch.setenv("K_REVISION", "email@example.com")
    monkeypatch.setenv("K_SERVICE", "secret\nvalue")
    assert http.get("/api/version").json() == {"commit_sha":None, "revision":None, "service":None}


def test_logs_exclude_request_content_and_accept_only_uuid(http, caplog):
    request_id = "1A2B3C4D-1234-5678-90AB-123456789ABC"
    response = http.post("/api/match?token=QUERY_SECRET", json={"posting_text":"BODY_SECRET"},
                        headers={"X-Request-ID":request_id,"Authorization":"Bearer HEADER_SECRET",
                                 "X-User-Email":"IDENTITY_SECRET@example.com"})
    assert response.status_code == 422
    assert response.headers["x-request-id"] == request_id.lower()
    event = request_logs(caplog)[0]
    assert event["path"] == "/api/match"
    assert event["status"] == 422
    assert event["method"] == "POST"
    assert event["duration_ms"] >= 0
    assert event["request_id"] == response.headers["x-request-id"]
    assert set(event) == {"severity","event","method","path","status","duration_ms",
                          "request_id","revision","commit_sha","service"}
    assert "SECRET" not in json.dumps(event)


@pytest.mark.parametrize("supplied", ["email@example.com", "x" * 500, "token/secret"])
def test_unsafe_ids_and_unmatched_paths_are_never_reflected(http, caplog, supplied):
    response = http.get("/private/PATH_SECRET?token=QUERY_SECRET", headers={"X-Request-ID":supplied})
    assert response.status_code == 404
    assert str(UUID(response.headers["x-request-id"])) == response.headers["x-request-id"]
    event = request_logs(caplog)[0]
    assert event["path"] == "<unmatched>"
    assert event["request_id"] != supplied
    assert "SECRET" not in json.dumps(event)


def test_unexpected_errors_return_support_id_without_exception_details(http, caplog):
    async def failing_route():
        raise RuntimeError("SECRET exception with customer transcript")
    route_count = len(api.app.router.routes)
    api.app.add_api_route("/__telemetry_failure", failing_route)
    try:
        response = http.get("/__telemetry_failure")
    finally:
        del api.app.router.routes[route_count:]
    assert response.status_code == 500
    assert response.json()["request_id"] == response.headers["x-request-id"]
    event = request_logs(caplog)[0]
    assert event["status"] == 500
    assert event["severity"] == "ERROR"
    assert "SECRET" not in response.text + json.dumps(event)
    assert all(record.exc_info is None for record in caplog.records if record.name == api.request_logger.name)


def test_telemetry_handler_writes_json_only_to_stdout():
    handler = api.request_logger.handlers[0]
    record = logging.LogRecord(api.request_logger.name, logging.INFO, "", 0,
                               '{"event":"http_request"}', (), None)
    assert json.loads(handler.format(record)) == {"event":"http_request"}
    assert "--no-access-log" in (Path(__file__).parents[1] / "scripts/serve.py").read_text()
