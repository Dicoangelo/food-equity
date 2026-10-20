"""Offline smoke-verifier checks: never contact Google, MCP or a model."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
import httpx

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


def recipient_evidence_fixture(now=None):
    now = now or datetime.now(timezone.utc)
    profile = {
        "recipient_id": "91", "name": "Fixture food bank", "profile_source": "generated",
        "synthetic_organization": False, "requires_confirmation": True,
        "operational_data_verified": False, "max_pallets": 2, "service_days": None,
    }
    return {
        "status": "ready", "recipient_id": "91", "profile": profile, "code": None,
        "retrieved_at": now.isoformat(), "fetched_at": now.isoformat(), "observation_date": None,
        "requires_confirmation": True, "operational_data_verified": False,
        "unknown_fields": ["service_days"],
        "source": {
            "kind": "bigquery_recipient_snapshot", "table": "a4i_food.recipient_embeddings",
            "transport": "mcp", "tool": "get_recipient_evidence",
            "source_observed_at": None, "operational_confirmed_at": None,
        },
    }


def test_evidence_receipt_preserves_unknowns_without_replacing_match():
    body = recipient_evidence_fixture(NOW)
    before = json.dumps(body, sort_keys=True)
    result = smoke.check_recipient_evidence(
        body, city_fixture()["recipients"][0], started_at=NOW - timedelta(seconds=1),
        finished_at=NOW + timedelta(seconds=1),
    )
    assert result["recipient_id"] == "91" and result["source"]["transport"] == "mcp"
    assert result["requires_confirmation"] and not result["operational_data_verified"]
    assert result["observation_date"] is None
    assert result["match_result_fields_absent"] is True
    assert "profile" not in result and "max_pallets" not in result
    assert json.dumps(body, sort_keys=True) == before


@pytest.mark.parametrize("provenance", ["generated", "planted", "unknown"])
def test_evidence_provenance_may_be_synthetic_or_unknown_but_never_operational(provenance):
    city_recipient = city_fixture()["recipients"][0]
    city_recipient["profile_source"] = provenance
    body = recipient_evidence_fixture(NOW)
    body["profile"].update(profile_source=provenance, synthetic_organization=provenance == "planted")
    result = smoke.check_recipient_evidence(body, city_recipient, started_at=NOW, finished_at=NOW)
    assert result["profile_source"] == provenance
    assert result["requires_confirmation"] and not result["operational_data_verified"]


@pytest.mark.parametrize("mutation,code", [
    (lambda b: b.update(status="unknown"), "recipient_evidence_not_ready"),
    (lambda b: b.update(recipient_id="92"), "recipient_evidence_id_mismatch"),
    (lambda b: b["profile"].update(recipient_id=91), "recipient_evidence_id_mismatch"),
    (lambda b: b["source"].update(transport="direct_bigquery"), "recipient_evidence_source"),
    (lambda b: b["source"].update(tool="vector_search_recipients"), "recipient_evidence_source"),
    (lambda b: b["source"].update(table="other.recipient_embeddings"), "recipient_evidence_source"),
    (lambda b: b.update(requires_confirmation=False), "recipient_evidence_operational_claim"),
    (lambda b: b["profile"].update(operational_data_verified=True), "recipient_evidence_operational_claim"),
    (lambda b: b["profile"].update(profile_source="verified"), "recipient_evidence_provenance"),
    (lambda b: b["profile"].update(profile_source="planted", synthetic_organization=True), "recipient_evidence_provenance"),
    (lambda b: b["profile"].update(synthetic_organization=True), "recipient_evidence_provenance"),
    (lambda b: b.update(observation_date=NOW.isoformat()), "recipient_evidence_observation_claim"),
    (lambda b: b["source"].update(operational_confirmed_at=NOW.isoformat()), "recipient_evidence_observation_claim"),
    (lambda b: b["source"].pop("source_observed_at"), "recipient_evidence_observation_claim"),
    (lambda b: b.update(fetched_at=(NOW + timedelta(seconds=1)).isoformat()), "recipient_evidence_timestamp_mismatch"),
    (lambda b: b.update(retrieved_at=None), "invalid_timestamp"),
    (lambda b: b.update(retrieved_at="2026-10-20T16:30:00"), "timezone_missing"),
    (lambda b: b.update(winner={"recipient_id": "91"}), "recipient_evidence_unexpected_match_or_private_fields"),
    (lambda b: b["profile"].update(composite_score=.9), "recipient_evidence_unexpected_match_or_private_fields"),
    (lambda b: b["profile"].update(embedding=[.1]), "recipient_evidence_unexpected_match_or_private_fields"),
    (lambda b: b["profile"].update(phone="private"), "recipient_evidence_unexpected_match_or_private_fields"),
    (lambda b: b.update(unknown_fields=[]), "recipient_evidence_profile_contract"),
])
def test_evidence_gate_refuses_substitution_provenance_and_operational_claims(mutation, code):
    body = recipient_evidence_fixture(NOW)
    mutation(body)
    with pytest.raises(smoke.VerificationError, match=code):
        smoke.check_recipient_evidence(body, city_fixture()["recipients"][0], started_at=NOW, finished_at=NOW)


@pytest.mark.parametrize("offset", [-6, 6, -86400])
def test_evidence_timestamp_must_be_from_this_request(offset):
    body = recipient_evidence_fixture(NOW + timedelta(seconds=offset))
    with pytest.raises(smoke.VerificationError, match="recipient_evidence_stale_timestamp"):
        smoke.check_recipient_evidence(body, city_fixture()["recipients"][0], started_at=NOW, finished_at=NOW)


@pytest.mark.parametrize("identifier", [None, True, -1, "", "../match", "91?x=1", "91/evidence", ["91"]])
def test_evidence_path_rejects_invalid_city_identifier(identifier):
    with pytest.raises(smoke.VerificationError, match="invalid_evidence_recipient_id"):
        smoke.recipient_evidence_path(identifier)


def test_evidence_path_canonicalizes_integer_and_encodes_allowed_colon():
    assert smoke.recipient_evidence_path(91) == "/api/recipients/91/evidence"
    assert smoke.recipient_evidence_path("fixture:91") == "/api/recipients/fixture%3A91/evidence"


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
            smoke.WORKFLOW_PATH: smoke.WORKFLOW_SOURCE.read_bytes(),
            "/api/recipients/91/evidence": recipient_evidence_fixture(),
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

    def request_stream(self, payload):
        return self.request("POST", smoke.STREAM_PATH, payload)

    def request_recipient_evidence(self, identifier):
        return self.request("GET", smoke.recipient_evidence_path(identifier))


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
    assert len(report["checks"]) == (5 if cheap else 7)
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
    for check in checks[5:]:
        assert check["passed"] is False
        assert check["response_status"] == "draft"
        assert check["observed_proposed_count"] == 1
        assert "mcp_result" in check["trace_stages"]


def stream_fixture(body=None):
    body = broker_fixture()[1] if body is None else body
    events = [
        {"type": "progress", "stage": stage}
        for stage in ("accepted", "connecting", "retrieving", "checking_constraints", "finalizing")
    ]
    events[3]["candidate_count"] = 1
    events.append({"type": "result", "data": body})
    return [
        {**event, "run_id": "run-fixture-1", "sequence": index}
        for index, event in enumerate(events, start=1)
    ]


def wire_events(events):
    return b"".join(json.dumps(event, separators=(",", ":")).encode() + b"\n" for event in events)


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
            await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


def mock_stream(monkeypatch, stream, *, content_type="application/x-ndjson", status=200):
    original_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url == URL + smoke.STREAM_PATH
        assert request.headers["Accept"] == "application/x-ndjson"
        assert TOKEN not in str(request.url)
        return httpx.Response(status, headers={"Content-Type": content_type,
                                              "Location": "https://untrusted.example"},
                              stream=stream)

    def make_client(**kwargs):
        return original_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(smoke.httpx, "AsyncClient", make_client)
    return requests


def test_stream_incrementally_reads_split_records_and_returns_only_safe_receipt(monkeypatch):
    events = stream_fixture()
    events.insert(3, {"type": "heartbeat", "run_id": "run-fixture-1", "sequence": 4})
    for index, event in enumerate(events, start=1):
        event["sequence"] = index
    raw = wire_events(events)
    # Split JSON tokens and the final newline across arbitrary network chunks.
    stream = ByteStream([raw[:13], raw[13:57], raw[57:-1], raw[-1:]])
    requests = mock_stream(monkeypatch, stream, content_type="application/x-ndjson; charset=utf-8")
    receipt, body = smoke.LiveClient(URL, TOKEN).request_stream(smoke.make_offer(now=NOW))
    assert body == events[-1]["data"]
    assert receipt["stream_events"] == len(events)
    assert receipt["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert receipt["response_bytes"] == len(raw)
    assert receipt["progress_stages"] == list(smoke.PROGRESS_STAGES[:4]) + ["finalizing"]
    assert receipt["first_event_latency_ms"] >= 0
    assert receipt["terminal_result_received"] is True
    assert requests[0].headers["Authorization"] == "Bearer " + TOKEN
    assert len(requests) == 1 and stream.closed
    assert TOKEN not in json.dumps(receipt)
    assert "run-fixture-1" not in json.dumps(receipt)
    assert "match_brief" not in json.dumps(receipt)


@pytest.mark.parametrize("mutate,code", [
    (lambda events: events[0].update(stage="connecting"), "stream_accepted_first"),
    (lambda events: events[1].update(run_id="different-run"), "stream_run_id_changed"),
    (lambda events: events[1].update(run_id=""), "stream_invalid_run_id"),
    (lambda events: events[1].update(sequence=3), "stream_sequence"),
    (lambda events: events[1].update(sequence=True), "stream_sequence"),
    (lambda events: events[2].update(stage="provider_secret"), "stream_progress_contract"),
    (lambda events: events[2].update(stage="accepted"), "stream_stage_order"),
    (lambda events: events[3].update(candidate_count=True), "stream_progress_counts"),
    (lambda events: events[3].update(candidate_count=1_000_001), "stream_progress_counts"),
    (lambda events: events[2].update(provider_message=TOKEN), "stream_progress_contract"),
    (lambda events: events[2].update(type="provider"), "stream_event_type"),
    (lambda events: events[-1].update(data=[]), "stream_result_contract"),
    (lambda events: events[-1].update(type="error", message=TOKEN), "stream_terminal_error"),
    (lambda events: events[-1]["data"].update(untrusted=float("nan")), "stream_invalid_json"),
])
def test_invalid_stream_contract_fails_without_provider_text(monkeypatch, mutate, code):
    events = stream_fixture()
    mutate(events)
    stream = ByteStream([wire_events(events)])
    mock_stream(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match=code) as caught:
        smoke.LiveClient(URL, TOKEN).request_stream({})
    assert TOKEN not in str(caught.value)
    assert stream.closed


@pytest.mark.parametrize("mutation,code", [
    (lambda raw: raw[:-1], "stream_incomplete_line"),
    (lambda raw: raw + b'{}\n', "stream_after_terminal"),
    (lambda raw: b'[]\n', "stream_event_not_object"),
    (lambda raw: b'not-json\n', "stream_invalid_json"),
    (lambda raw: b'', "stream_missing_result"),
])
def test_stream_requires_one_terminal_result_and_clean_eof(monkeypatch, mutation, code):
    stream = ByteStream([mutation(wire_events(stream_fixture()))])
    mock_stream(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match=code):
        smoke.LiveClient(URL, None).request_stream({})
    assert stream.closed


def test_stream_requires_actual_broker_progress(monkeypatch):
    events = stream_fixture()
    events = [events[0], events[-1]]
    events[-1]["sequence"] = 2
    mock_stream(monkeypatch, ByteStream([wire_events(events)]))
    with pytest.raises(smoke.VerificationError, match="stream_missing_progress"):
        smoke.LiveClient(URL, None).request_stream({})


@pytest.mark.parametrize("status,content_type,code", [
    (200, "application/json", "stream_content_type"),
    (200, "text/html", "stream_content_type"),
    (302, "text/html", None),
    (503, "text/html", None),
])
def test_stream_http_contract_never_follows_redirect_or_reads_error_body(monkeypatch, status, content_type, code):
    class UnreadStream(ByteStream):
        async def __aiter__(self):
            pytest.fail("Wrong-content-type, redirect and error bodies must not be consumed")
            yield b""
    stream = UnreadStream([])
    requests = mock_stream(monkeypatch, stream, status=status, content_type=content_type)
    client = smoke.LiveClient(URL, TOKEN)
    if code:
        with pytest.raises(smoke.VerificationError, match=code):
            client.request_stream({})
    else:
        receipt, body = client.request_stream({})
        assert receipt["http_status"] == status and body is None
    assert len(requests) == 1 and stream.closed


@pytest.mark.parametrize("limit,code", [("bytes", "response_too_large"), ("events", "stream_too_many_events")])
def test_stream_size_and_event_limits(monkeypatch, limit, code):
    monkeypatch.setattr(smoke, "MAX_RESPONSE_BYTES" if limit == "bytes" else "MAX_STREAM_EVENTS", 3)
    stream = ByteStream([wire_events(stream_fixture())])
    mock_stream(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match=code):
        smoke.LiveClient(URL, None).request_stream({})
    assert stream.closed


@pytest.mark.parametrize("terminal_received", [False, True])
def test_stream_deadline_cancels_stalled_read_and_closes_response(monkeypatch, terminal_received):
    class StalledStream(ByteStream):
        async def __aiter__(self):
            yield wire_events(stream_fixture() if terminal_received else stream_fixture()[:1])
            await asyncio.Event().wait()
    stream = StalledStream([])
    mock_stream(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match="stream_timeout"):
        smoke.LiveClient(URL, None, timeout=0.025).request_stream({})
    assert stream.closed


def test_stream_records_progress_before_producer_finishes(monkeypatch):
    observed_clocks = []
    clock = [0.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])

    class DelayedStream(ByteStream):
        async def __aiter__(self):
            clock[0] = 0.01
            yield wire_events(stream_fixture()[:1])
            clock[0] = 0.02
            observed_clocks.append(clock[0])
            yield wire_events(stream_fixture()[1:])
    mock_stream(monkeypatch, DelayedStream([]))
    receipt, _ = smoke.LiveClient(URL, None).request_stream({})
    assert observed_clocks == [0.02]
    assert receipt["first_event_latency_ms"] == 10
    assert receipt["latency_ms"] == 20


@pytest.mark.parametrize("content_type", ["text/javascript", "application/javascript; charset=utf-8"])
def test_workflow_asset_mime_and_exact_archived_source(monkeypatch, content_type):
    source = smoke.WORKFLOW_SOURCE.read_bytes()
    response = io.BytesIO(source)
    response.status = 200
    response.headers = {"Content-Type": content_type}
    client = smoke.LiveClient(URL, TOKEN)
    requests = []
    def open_asset(request, **kwargs):
        requests.append(request)
        return response
    client.opener = SimpleNamespace(open=open_asset)
    receipt, body = client.request("GET", smoke.WORKFLOW_PATH)
    evidence = smoke.check_workflow_asset(body)
    assert evidence["source_sha256"] == receipt["response_sha256"]
    assert evidence["bytes"] == len(source)
    assert requests[0].full_url == URL + smoke.WORKFLOW_PATH
    assert TOKEN not in json.dumps(receipt) + json.dumps(evidence)


def test_wrong_workflow_asset_or_mime_fails_before_model_requests(monkeypatch):
    response = io.BytesIO(b"not javascript")
    response.status = 200
    response.headers = {"Content-Type": "text/html"}
    client = smoke.LiveClient(URL, None)
    client.opener = SimpleNamespace(open=lambda *args, **kwargs: response)
    with pytest.raises(smoke.VerificationError, match="workflow_content_type"):
        client.request("GET", smoke.WORKFLOW_PATH)
    with pytest.raises(smoke.VerificationError, match="workflow_asset_mismatch"):
        smoke.check_workflow_asset(b"outdated script")

    class BadAsset(FakeClient):
        def request(self, method, path, payload=None):
            assert payload is None, "No model calls after asset failure"
            if path == smoke.WORKFLOW_PATH:
                return {"http_status": 200}, b"outdated script"
            return super().request(method, path, payload)
    checks = smoke.run_checks(BadAsset())
    assert checks[-1]["error_code"] == "workflow_asset_mismatch"
    assert len(checks) == 2


def test_release_gate_keeps_exactly_two_broker_requests_across_both_transports():
    calls = []
    class TrackingClient(FakeClient):
        def request(self, method, path, payload=None):
            calls.append((method, path))
            return super().request(method, path, payload)
    checks = smoke.run_checks(TrackingClient())
    assert all(check["passed"] for check in checks)
    assert [path for method, path in calls if method == "POST"] == [smoke.STREAM_PATH, "/api/match"]
    assert calls.count(("GET", "/api/city")) == 1
    assert calls.count(("GET", "/api/recipients/91/evidence")) == 1


def test_exact_read_uses_city_identifier_instead_of_a_hardcoded_smoke_recipient():
    calls = []
    class DifferentCity(FakeClient):
        def request(self, method, path, payload=None):
            calls.append((method, path))
            if path == "/api/recipients/112/evidence":
                body = recipient_evidence_fixture()
                body["recipient_id"] = body["profile"]["recipient_id"] = "112"
                return {"http_status": 200}, body
            receipt, body = super().request(method, path, payload)
            if path == "/api/city":
                body["recipients"][0]["recipient_id"] = 112
            return receipt, body
    checks = smoke.run_checks(DifferentCity(), skip_agent=True)
    assert all(check["passed"] for check in checks)
    assert calls.count(("GET", "/api/city")) == 1
    assert calls.count(("GET", "/api/recipients/112/evidence")) == 1
    assert not any(method == "POST" or path.endswith("/91/evidence") for method, path in calls)


@pytest.mark.parametrize("status", [404, 409, 503])
def test_failed_exact_read_blocks_release_before_model_calls(status):
    calls = []
    class BadEvidence(FakeClient):
        def request(self, method, path, payload=None):
            calls.append((method, path))
            if path == "/api/recipients/91/evidence":
                return {"http_status": status}, None
            return super().request(method, path, payload)
    checks = smoke.run_checks(BadEvidence())
    assert checks[-1]["name"] == "recipient_evidence" and not checks[-1]["passed"]
    assert checks[-1]["http_status"] == status
    assert checks[-1]["error_code"] == "unexpected_http_status"
    assert all(method == "GET" for method, _ in calls)
    assert calls.count(("GET", "/api/city")) == 1
    assert calls.count(("GET", "/api/recipients/91/evidence")) == 1


def test_invalid_city_cannot_supply_an_evidence_path_or_trigger_model_calls():
    class BadCity(FakeClient):
        def request(self, method, path, payload=None):
            assert method == "GET" and not path.endswith("/evidence")
            receipt, body = super().request(method, path, payload)
            if path == "/api/city":
                body["recipients"][0]["recipient_id"] = "../match"
            return receipt, body
    checks = smoke.run_checks(BadCity())
    assert checks[-1]["name"] == "city" and not checks[-1]["passed"]
    assert checks[-1]["error_code"] == "invalid_evidence_recipient_id"


def mock_evidence_http(monkeypatch, stream, *, status=200, content_type="application/json", cache="no-store"):
    original_client = httpx.AsyncClient
    requests = []
    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url == URL + "/api/recipients/91/evidence"
        assert request.content == b"" and request.headers["Accept"] == "application/json"
        return httpx.Response(status, headers={
            "Content-Type": content_type, "Cache-Control": cache, "Location": "https://untrusted.example",
        }, stream=stream)
    def make_client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["timeout"] <= smoke.EVIDENCE_TIMEOUT_SECONDS
        return original_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(smoke.httpx, "AsyncClient", make_client)
    return requests


@pytest.mark.parametrize("token", [None, TOKEN])
def test_evidence_get_uses_one_bounded_read_and_does_not_log_profile_or_token(monkeypatch, token):
    raw = json.dumps(recipient_evidence_fixture(NOW)).encode()
    stream = ByteStream([raw[:13], raw[13:]])
    requests = mock_evidence_http(monkeypatch, stream, content_type="application/json; charset=utf-8")
    receipt, body = smoke.LiveClient(URL, token).request_recipient_evidence(91)
    assert len(requests) == 1 and stream.closed
    assert requests[0].headers.get("Authorization") == ("Bearer " + token if token else None)
    assert receipt["cache_control_no_store"] is True
    assert receipt["response_sha256"] == hashlib.sha256(raw).hexdigest()
    assert body == recipient_evidence_fixture(NOW)
    assert TOKEN not in json.dumps(receipt)
    assert "Fixture food bank" not in json.dumps(receipt)


@pytest.mark.parametrize("status,content_type,cache,code", [
    (302, "text/html", "", None),
    (503, "text/html", "", None),
    (200, "text/html", "no-store", "evidence_content_type"),
    (200, "application/json", "max-age=3600", "evidence_cache_contract"),
])
def test_evidence_http_rejects_redirect_errors_mime_and_cached_response_without_reading_body(
    monkeypatch, status, content_type, cache, code,
):
    class UnreadStream(ByteStream):
        async def __aiter__(self):
            pytest.fail("Must not read invalid or error response bodies")
            yield b""
    stream = UnreadStream([])
    requests = mock_evidence_http(monkeypatch, stream, status=status, content_type=content_type, cache=cache)
    client = smoke.LiveClient(URL, TOKEN)
    if code:
        with pytest.raises(smoke.VerificationError, match=code):
            client.request_recipient_evidence("91")
    else:
        receipt, body = client.request_recipient_evidence("91")
        assert receipt["http_status"] == status and body is None
    assert len(requests) == 1 and stream.closed


@pytest.mark.parametrize("raw,code", [
    (b"not-json", "invalid_evidence_json"),
    (b'{"value":NaN}', "invalid_evidence_json"),
    (b"[]", "evidence_response_not_object"),
    (b"x" * (smoke.MAX_EVIDENCE_BYTES + 1), "evidence_response_too_large"),
])
def test_evidence_malformed_or_oversized_body_closes_stream(monkeypatch, raw, code):
    stream = ByteStream([raw])
    mock_evidence_http(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match=code):
        smoke.LiveClient(URL, TOKEN).request_recipient_evidence("91")
    assert stream.closed


@pytest.mark.parametrize("complete_json_before_stall", [False, True])
def test_evidence_total_deadline_cancels_stalled_read_and_closes_stream(monkeypatch, complete_json_before_stall):
    class StalledStream(ByteStream):
        cancelled = False
        async def __aiter__(self):
            if complete_json_before_stall:
                yield json.dumps(recipient_evidence_fixture()).encode()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
    stream = StalledStream([])
    requests = mock_evidence_http(monkeypatch, stream)
    with pytest.raises(smoke.VerificationError, match="evidence_http_timeout"):
        smoke.LiveClient(URL, TOKEN, timeout=.025).request_recipient_evidence("91")
    assert stream.cancelled and stream.closed and len(requests) == 1
