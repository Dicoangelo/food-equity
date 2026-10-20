"""Offline API contract tests: no cloud SDK or credential discovery."""
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("food_api_contract", Path(__file__).parents[1] / "agent/api.py")
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


@pytest.fixture
def client(monkeypatch):
    broker = AsyncMock()
    broker.broker_posting_async.return_value = {
        "status": "draft", "generation_mode": "deterministic_fallback", "message": "Model unavailable",
        "execution_trace": [{"kind": "mcp_tool_result", "name": "vector_search_recipients"}],
        "requires_confirmation": True,
    }
    monkeypatch.setattr(api, "get_broker_agent", lambda: broker)
    return TestClient(api.app), broker


def offer():
    return {key: value for key, value in api.SAMPLE_SCENARIOS[0].items() if key in api.MatchRequest.model_fields}


def test_health_and_ui_do_not_call_broker(client):
    http, broker = client
    assert http.get('/healthz').json() == {"status": "ok", "cloud_dependencies": "not_checked"}
    response = http.get('/')
    assert response.status_code == 200
    assert "119 Profiles Ready" not in response.text
    assert "illustrative straight-line connection" in response.text
    broker.broker_posting_async.assert_not_called()


@pytest.mark.parametrize('field,value', [
    ('pallet_count', -1), ('pallet_count', 0), ('hours_remaining', 0), ('hours_remaining', -1),
    ('hours_remaining', 169), ('donor_lat', 91), ('donor_lon', -181),
    ('required_storage', 'unknown'), ('posting_text', '  '), ('donor_name', ''),
])
def test_invalid_offer_never_calls_broker(client, field, value):
    http, broker = client
    payload = offer()
    payload[field] = value
    assert http.post('/api/match', json=payload).status_code == 422
    broker.broker_posting_async.assert_not_called()


@pytest.mark.parametrize('field', ['pallet_count', 'hours_remaining', 'donor_lat', 'donor_lon'])
def test_nonfinite_values_are_rejected_by_model(field):
    from pydantic import ValidationError
    for value in (float('nan'), float('inf'), -float('inf')):
        payload = offer()
        payload[field] = value
        with pytest.raises(ValidationError):
            api.MatchRequest.model_validate(payload)


def test_refrigerated_alias_and_draft_trace_survive(client):
    http, broker = client
    payload = offer()
    payload['required_storage'] = ' REFRIGERATED '
    response = http.post('/api/match', json=payload)
    assert response.status_code == 200
    assert broker.broker_posting_async.call_args.kwargs['required_storage'] == 'cold'
    assert response.json()['generation_mode'] == 'deterministic_fallback'
    assert response.json()['execution_trace'][0]['name'] == 'vector_search_recipients'


def test_fixed_deadline_reduces_remaining_window(client):
    http, broker = client
    payload = offer()
    payload['deadline_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert http.post('/api/match', json=payload).status_code == 200
    remaining = broker.broker_posting_async.call_args.kwargs['hours_remaining']
    assert 0.99 < remaining <= 1.0


@pytest.mark.parametrize('deadline', [
    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    '2026-10-20T16:00:00',
])
def test_expired_or_naive_deadline_rejected(client, deadline):
    http, broker = client
    payload = offer()
    payload['deadline_at'] = deadline
    assert http.post('/api/match', json=payload).status_code == 422
    broker.broker_posting_async.assert_not_called()


def test_runtime_failure_is_explicit_and_does_not_leak_error(client):
    http, broker = client
    broker.broker_posting_async.side_effect = RuntimeError('private-token-do-not-return')
    response = http.post('/api/match', json=offer())
    assert response.status_code == 502
    assert response.json()['status'] == 'blocked'
    assert 'private-token' not in response.text


def test_scenario_coordinates_are_distinct(client):
    http, _ = client
    scenarios = http.get('/api/scenarios').json()
    assert len({(s['donor_lat'], s['donor_lon']) for s in scenarios}) == 3


def test_city_data_contract_is_available_without_model_call(client, monkeypatch):
    from types import SimpleNamespace
    http, broker = client
    payload = {"city": "Seattle", "status": "ready", "tracts": [{"tract_id": "1", "lat": 47.6, "lon": -122.3, "need_score": 0.25}], "recipients": [], "provenance": {"source": "fixture"}}
    monkeypatch.setattr(api, "get_city_service", lambda: SimpleNamespace(get_city_data=lambda: payload))
    response = http.get('/api/city')
    assert response.status_code == 200
    assert response.json() == payload
    broker.broker_posting_async.assert_not_called()


def test_city_failure_is_explicit_without_fabricated_heat_values(client, monkeypatch):
    from types import SimpleNamespace
    http, broker = client
    def fail():
        raise RuntimeError('private-query-detail')
    monkeypatch.setattr(api, "get_city_service", lambda: SimpleNamespace(get_city_data=fail))
    response = http.get('/api/city')
    assert response.status_code == 503
    assert response.json()['status'] == 'unavailable'
    assert response.json()['tracts'] == []
    assert 'private-query-detail' not in response.text
    broker.broker_posting_async.assert_not_called()
