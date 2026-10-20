"""Offline smoke-verifier checks: never contact Google, MCP or a model."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from scripts import verify_live_release as smoke

NOW = datetime(2026, 10, 20, 16, 30, tzinfo=timezone.utc)
URL = "https://example-service-abc-ue.a.run.app"
TOKEN = "example.header.signature"


def broker_fixture(stress=False, winner=True):
    offer = smoke.make_offer(stress, NOW)
    evaluated = NOW + timedelta(seconds=10)
    row = {
        "recipient_id": "91", "profile_source": "generated", "requires_confirmation": True,
        "operational_data_verified": False, "deadline_at": offer["deadline_at"],
        "clock_basis": "fixed_deadline", "constraint_status": "eligible",
        "max_pallets": 2, "time_margin_hours": 20, "reason_codes": [],
    }
    trace = [
        {"stage": "mcp_discovery", "tool": "vector_search_recipients"},
        {"stage": "adk_function_call", "name": "broker_match"},
        {"stage": "mcp_call", "tool": "vector_search_recipients",
         "query_sha256": hashlib.sha256(offer["posting_text"].encode()).hexdigest()},
        {"stage": "mcp_result", "tool": "vector_search_recipients",
         "candidate_count": 1, "recipient_ids": ["91"]},
        {"stage": "deterministic_constraints"},
        {"stage": "adk_function_result", "name": "broker_match"},
        {"stage": "adk_complete", "model": "gemini-2.5-flash"},
        {"stage": "dispatch_clock_recheck"},
    ]
    donor = {k: offer[k] for k in ("posting_text", "required_storage", "pallet_count", "deadline_at")}
    donor["hours_remaining"] = (smoke.timestamp(offer["deadline_at"]) - evaluated).total_seconds() / 3600
    body = {
        "status": "success" if winner else "no_viable_match",
        "generation_mode": "adk_gemini" if winner else "constraints",
        "requires_confirmation": True, "provenance_warning": "Generated profiles",
        "donor": donor, "evaluated_at": evaluated.isoformat(), "execution_trace": trace,
        "winner": row if winner else None, "runners_up": [], "eliminated_decoys": [],
        "pending_confirmation": [],
    }
    if winner:
        body.update(brief_generation="deterministic_facts", outreach_mode="gemini_plan",
                    match_brief={field: "Fixed deadline " + offer["deadline_at"] for field in
                                 ("clock_action_plan", "drafted_sms_outreach", "drafted_email_outreach")})
        trace.append({"stage": "brief_render", "facts_source": "final_deterministic_ranking"})
    else:
        row.update(constraint_status="rejected", reason_codes=["capacity_exceeded" if stress else "service_day_closed"])
        body["eliminated_decoys"].append(row)
    return offer, body


def test_bakery_text_and_quantity_are_explicit():
    offer = smoke.make_offer(now=NOW)
    assert offer["posting_text"] == "roughly 200 loaves of day-old bread and pastries from three store locations"
    assert offer["pallet_count"] == 1 and offer["hours_remaining"] == 24
    assert smoke.timestamp(offer["deadline_at"]) == NOW + timedelta(hours=24)


@pytest.mark.parametrize("winner", [False, True])
def test_real_trace_passes_without_requiring_positive_match(winner):
    offer, body = broker_fixture(winner=winner)
    result = smoke.check_broker(body, offer)
    assert result["proposed_count"] == int(winner)
    assert result["candidate_count"] == 1
    assert "match_brief" not in result  # No raw model prose in the receipt.


@pytest.mark.parametrize("mutation,code", [
    (lambda b: b.update(status="draft", generation_mode="deterministic_fallback"), "agent_incomplete_or_fallback"),
    (lambda b: b["donor"].update(deadline_at=(NOW + timedelta(hours=25)).isoformat()), "deadline_changed"),
    (lambda b: b["winner"].update(recipient_id="not-retrieved"), "ungrounded_recipient"),
    (lambda b: b["winner"].update(operational_data_verified=True), "candidate_provenance"),
    (lambda b: b["execution_trace"].pop(2), "trace_incomplete"),
    (lambda b: b["execution_trace"][2].update(query_sha256="wrong"), "retrieval_query_mismatch"),
    (lambda b: b["match_brief"].update(clock_action_plan="A made-up deadline"), "brief_deadline_missing"),
    (lambda b: b["winner"].update(max_pallets=0), "ineligible_proposal"),
])
def test_bad_runtime_evidence_fails(mutation, code):
    offer, body = broker_fixture()
    mutation(body)
    with pytest.raises(smoke.VerificationError, match=code):
        smoke.check_broker(body, offer)


def test_stress_case_requires_actual_capacity_rejection():
    offer, body = broker_fixture(stress=True, winner=False)
    assert smoke.check_broker(body, offer, stress=True)["proposed_count"] == 0
    body["eliminated_decoys"][0]["reason_codes"] = ["service_day_closed"]
    with pytest.raises(smoke.VerificationError, match="capacity_stress_not_blocked"):
        smoke.check_broker(body, offer, stress=True)


def city_fixture():
    return {
        "city": "Seattle", "status": "ready",
        "provenance": {"project_id": smoke.PROJECT, "dataset_id": "a4i_food", "rate_units": "fraction_0_to_1"},
        "tracts": [{"tract_id": "53033000100", "lat": 47.6, "lon": -122.3, "poverty_rate": .2,
                    "no_vehicle_rate": None, "assistance_rate": .1, "need_score": None}],
        "recipients": [{"recipient_id": "91", "lat": 47.6, "lon": -122.3, "profile_source": "generated",
                        "operational_data_verified": False, "requires_confirmation": True}],
        "routing": {"travel_time_verified": False},
    }


def test_city_reports_actual_counts_and_keeps_missing_rates_unknown():
    body = city_fixture()
    assert smoke.check_city(body)["tracts"] == 1
    body["tracts"][0]["poverty_rate"] = 20
    with pytest.raises(smoke.VerificationError, match="city_rate_units"):
        smoke.check_city(body)


def foodkeeper_fixture():
    return {
        "status": "options", "source": {"sha256": smoke.FOODKEEPER_SHA256},
        "selection_required": True, "selected_product_id": None,
        "remaining_freshness_hours": None, "donor_deadline_modified": False,
        "advisory_notice": "Consumer guidance only", "unknowns_required": ["product state"],
        "products": [{"product_id": "1", "remaining_freshness_hours": None,
                      "applicability": "unconfirmed_product_state_and_history"}],
    }


def test_foodkeeper_pin_and_no_inferred_freshness():
    body = foodkeeper_fixture()
    assert smoke.check_foodkeeper(body)["donor_deadline_modified"] is False
    body["donor_deadline_modified"] = True
    with pytest.raises(smoke.VerificationError, match="foodkeeper_advisory_contract"):
        smoke.check_foodkeeper(body)
    body = foodkeeper_fixture()
    body["source"]["sha256"] = "different"
    with pytest.raises(smoke.VerificationError, match="foodkeeper_source_changed"):
        smoke.check_foodkeeper(body)


def metadata():
    return {"status": {"url": URL, "latestReadyRevisionName": "r1", "latestCreatedRevisionName": "r1",
                       "traffic": [{"revisionName": "r1", "percent": 100}]}}


def mock_gcloud(monkeypatch, *, account="student-fixture@qwiklabs.net", service_data=None):
    def read(*args):
        if args[:2] == ("auth", "list"):
            return account
        if args[:3] == ("config", "get-value", "project"):
            return smoke.PROJECT
        return json.dumps(service_data or metadata())
    monkeypatch.setattr(smoke, "gcloud", read)


def test_service_url_must_match_google_resolved_origin(monkeypatch):
    mock_gcloud(monkeypatch)
    assert smoke.resolve_service(explicit_url=URL)["revision"] == "r1"
    with pytest.raises(smoke.VerificationError, match="service_url_mismatch"):
        smoke.resolve_service(explicit_url="https://untrusted.example")


def test_identity_and_revision_guards(monkeypatch):
    mock_gcloud(monkeypatch, account="personal@example.com")
    with pytest.raises(smoke.VerificationError, match="wrong_active_identity"):
        smoke.resolve_service()
    data = metadata()
    data["status"]["traffic"] = [{"revisionName": "old", "percent": 100}]
    mock_gcloud(monkeypatch, service_data=data)
    with pytest.raises(smoke.VerificationError, match="traffic_not_all_latest_revision"):
        smoke.resolve_service()


def candidate_reader(monkeypatch, *, revision="candidate", ready="True", account=None):
    data = metadata()
    data["status"]["traffic"].append({
        "tag": "test-tag", "revisionName": revision, "url": "https://test-tag---example.run.app"})
    def read(*args):
        if args[:2] == ("auth", "list"):
            return account or "student-fixture@qwiklabs.net"
        if args[:3] == ("config", "get-value", "project"):
            return smoke.PROJECT
        if args[:3] == ("run", "revisions", "describe"):
            return json.dumps({"status": {"conditions": [{"type": "Ready", "status": ready}]}})
        return json.dumps(data)
    monkeypatch.setattr(smoke, "gcloud", read)


def test_candidate_is_exact_tagged_ready_revision_not_latest(monkeypatch):
    candidate_reader(monkeypatch)
    result = smoke.resolve_service(candidate_tag="test-tag", expected_revision="candidate")
    assert result["revision"] == "candidate"
    assert result["url"] == "https://test-tag---example.run.app"
    with pytest.raises(smoke.VerificationError, match="candidate_revision_mismatch"):
        smoke.resolve_service(candidate_tag="test-tag", expected_revision="other")
    with pytest.raises(smoke.VerificationError, match="candidate_requires_tag_and_revision"):
        smoke.resolve_service(candidate_tag="test-tag")
    candidate_reader(monkeypatch, ready="False")
    with pytest.raises(smoke.VerificationError, match="candidate_revision_not_ready"):
        smoke.resolve_service(candidate_tag="test-tag", expected_revision="candidate")


def test_ci_identity_requires_explicit_fixed_deployer(monkeypatch):
    candidate_reader(monkeypatch, account=smoke.CI_DEPLOYER)
    with pytest.raises(smoke.VerificationError, match="wrong_active_identity"):
        smoke.resolve_service(candidate_tag="test-tag", expected_revision="candidate")
    result = smoke.resolve_service(candidate_tag="test-tag", expected_revision="candidate",
                                   expected_deployer=smoke.CI_DEPLOYER)
    assert result["ci_identity_verified"] and not result["student_identity_verified"]
    candidate_reader(monkeypatch, account="other@project.iam.gserviceaccount.com")
    with pytest.raises(smoke.VerificationError, match="wrong_ci_identity"):
        smoke.resolve_service(expected_deployer="other@project.iam.gserviceaccount.com")


def test_http_redirect_never_forwards_authorization():
    assert smoke.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.example") is None


def test_token_only_in_authorization_header_and_http_error_body_discarded():
    client = smoke.LiveClient(URL, TOKEN)
    def request(req, **kwargs):
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert TOKEN not in req.full_url
        raise HTTPError(req.full_url, 403, TOKEN, {}, None)
    client.opener = SimpleNamespace(open=request)
    receipt, body = client.request("GET", "/api/health")
    assert receipt["http_status"] == 403 and body is None
    assert TOKEN not in json.dumps(receipt)


class FakeClient:
    def __init__(self, *args):
        pass

    def request(self, method, path, payload=None):
        bodies = {
            "/api/health": {"status": "ok", "cloud_dependencies": "not_checked"},
            "/api/city": city_fixture(), "/api/foodkeeper?query=bread": foodkeeper_fixture(),
        }
        if payload:
            stress = payload["pallet_count"] == 10000
            _, body = broker_fixture(stress=stress, winner=False)
            body["donor"].update({k: payload[k] for k in body["donor"] if k != "hours_remaining"})
            evaluated = smoke.timestamp(payload["deadline_at"]) - timedelta(hours=payload["hours_remaining"])
            body["evaluated_at"] = evaluated.isoformat()
            body["donor"]["hours_remaining"] = payload["hours_remaining"]
            for row in body["eliminated_decoys"]:
                row["deadline_at"] = payload["deadline_at"]
            body["execution_trace"][2]["query_sha256"] = hashlib.sha256(payload["posting_text"].encode()).hexdigest()
        else:
            body = bodies[path]
        return {"http_status": 200, "latency_ms": 1}, body


@pytest.mark.parametrize("cheap", [False, True])
def test_evidence_contains_no_token_and_cheap_mode_never_claims_runtime(monkeypatch, tmp_path, capsys, cheap):
    monkeypatch.setattr(smoke, "resolve_service", lambda *a: {"url": URL, "revision": "r1"})
    monkeypatch.setattr(smoke, "identity_token", lambda: TOKEN)
    monkeypatch.setattr(smoke, "LiveClient", FakeClient)
    path = tmp_path / "evidence.json"
    assert smoke.main(["--output", str(path)] + (["--skip-agent"] if cheap else [])) == 0
    report = json.loads(path.read_text())
    assert report["runtime_passed"] is (not cheap)
    assert report["proposed_count"] == 0
    assert len(report["checks"]) == (3 if cheap else 5)
    assert TOKEN not in path.read_text() + capsys.readouterr().out


def test_existing_evidence_is_never_overwritten_or_auth_attempted(monkeypatch, tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("original")
    monkeypatch.setattr(smoke, "resolve_service", lambda *a: pytest.fail("auth must not run"))
    with pytest.raises(smoke.VerificationError, match="output_must_be_new"):
        smoke.main(["--output", str(path)])
    assert path.read_text() == "original"


def test_public_checks_never_mint_or_send_identity_token(monkeypatch, tmp_path):
    monkeypatch.setattr(smoke, "resolve_service", lambda *a: {"url": URL, "revision": "r1"})
    monkeypatch.setattr(smoke, "identity_token", lambda: pytest.fail("No token for public demo"))
    monkeypatch.setattr(smoke, "LiveClient", FakeClient)
    assert smoke.main(["--output", str(tmp_path / "public.json"), "--public"]) == 0
    client = smoke.LiveClient(URL, None)
    def request(req, **kwargs):
        assert req.get_header("Authorization") is None
        raise HTTPError(req.full_url, 403, "discard", {}, None)
    client.opener = SimpleNamespace(open=request)
    client.request("GET", "/api/health")


def test_source_telemetry_mismatch_stops_before_cloud_agent_calls():
    calls = []
    class VersionOnly:
        def request(self, method, path, payload=None):
            calls.append(path)
            return {"http_status": 200}, {
                "commit_sha": "b" * 40, "revision": "r1", "service": smoke.SERVICE}
    checks = smoke.run_checks(VersionOnly(), expected_commit="a" * 40, expected_revision="r1")
    assert calls == ["/api/version"]
    assert checks[0]["error_code"] == "deployed_source_mismatch"


def test_fallback_still_records_observed_proposals_without_runtime_success():
    class Fallback(FakeClient):
        def request(self, method, path, payload=None):
            if payload:
                _, body = broker_fixture()
                body.update(status="draft", generation_mode="deterministic_fallback")
                return {"http_status": 200, "latency_ms": 3}, body
            return super().request(method, path, payload)
    checks = smoke.run_checks(Fallback())
    for check in checks[3:]:
        assert check["passed"] is False
        assert check["response_status"] == "draft"
        assert check["observed_proposed_count"] == 1
        assert "mcp_result" in check["trace_stages"]
