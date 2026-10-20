"""Offline API contract tests: no cloud SDK or credential discovery."""
import asyncio
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

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
    expected = {"status": "ok", "cloud_dependencies": "not_checked"}
    for path in ('/healthz', '/api/health'):
        response = http.get(path)
        assert response.status_code == 200
        assert response.json() == expected
    assert '/api/health' in http.get('/openapi.json').json()['paths']
    response = http.get('/')
    assert response.status_code == 200
    assert "119 Profiles Ready" not in response.text
    assert "illustrative straight-line connection" in response.text
    broker.broker_posting_async.assert_not_called()


def test_served_inline_javascript_parses(client):
    """Catch Python string escaping that can break otherwise valid source JS."""
    http, broker = client
    html = http.get('/').text
    script = html.split('  <!-- Leaflet & App JS -->')[1].split('<script>')[1].split('</script>')[0]
    checked = subprocess.run(['node', '--check'], input=script, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr
    broker.broker_posting_async.assert_not_called()


def test_recipient_explorer_assets_are_same_origin_and_do_not_call_broker(client):
    http, broker = client
    html = http.get('/').text
    assert 'src="/ui/recipient-explorer.js"' in html
    assert 'href="/ui/recipient-explorer.css"' in html
    script = http.get('/ui/recipient-explorer.js')
    assert script.status_code == 200
    assert 'javascript' in script.headers['content-type']
    checked = subprocess.run(['node', '--check'], input=script.text, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr
    style = http.get('/ui/recipient-explorer.css')
    assert style.status_code == 200
    assert 'text/css' in style.headers['content-type']
    assert '#recipient-detail' in style.text
    assert http.get('/ui/api.py').status_code == 404
    broker.broker_posting_async.assert_not_called()


def test_offer_workflow_asset_is_served_and_parses_without_broker(client):
    http, broker = client
    assert 'src="/ui/offer-workflow.js"' in http.get('/').text
    response = http.get('/ui/offer-workflow.js')
    assert response.status_code == 200
    assert 'javascript' in response.headers['content-type']
    checked = subprocess.run(['node', '--check'], input=response.text, text=True, capture_output=True)
    assert checked.returncode == 0, checked.stderr
    broker.broker_posting_async.assert_not_called()


@pytest.mark.parametrize('endpoint', ['/api/match', '/api/match/stream'])
@pytest.mark.parametrize('field,value', [
    ('pallet_count', -1), ('pallet_count', 0), ('hours_remaining', 0), ('hours_remaining', -1),
    ('hours_remaining', 169), ('donor_lat', 91), ('donor_lon', -181),
    ('required_storage', 'unknown'), ('posting_text', '  '), ('donor_name', ''),
])
def test_invalid_offer_never_calls_broker(client, endpoint, field, value):
    http, broker = client
    payload = offer()
    payload[field] = value
    response = http.post(endpoint, json=payload)
    assert response.status_code == 422
    assert 'application/json' in response.headers['content-type']
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
    assert broker.broker_posting_async.call_args.kwargs['deadline_at'].isoformat() == payload['deadline_at']


@pytest.mark.parametrize('endpoint', ['/api/match', '/api/match/stream'])
@pytest.mark.parametrize('deadline', [
    (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    '2026-10-20T16:00:00',
])
def test_expired_or_naive_deadline_rejected(client, endpoint, deadline):
    http, broker = client
    payload = offer()
    payload['deadline_at'] = deadline
    response = http.post(endpoint, json=payload)
    assert response.status_code == 422
    assert 'application/json' in response.headers['content-type']
    broker.broker_posting_async.assert_not_called()


def test_stream_uses_same_normalized_offer_and_absolute_clock_as_json(client, monkeypatch):
    http, broker = client
    now = datetime.now(timezone.utc).replace(microsecond=0)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is not None else now.replace(tzinfo=None)

    monkeypatch.setattr(api, 'datetime', FrozenDateTime)
    payload = offer()
    payload['required_storage'] = ' REFRIGERATED '
    payload['deadline_at'] = (now + timedelta(minutes=45)).isoformat()
    assert http.post('/api/match', json=payload).status_code == 200
    ordinary_arguments = broker.broker_posting_async.call_args.kwargs.copy()
    broker.broker_posting_async.reset_mock()

    response = http.post('/api/match/stream', json=payload)
    assert response.status_code == 200
    arguments = broker.broker_posting_async.call_args.kwargs.copy()
    assert callable(arguments.pop('progress_callback'))
    assert arguments == ordinary_arguments
    assert arguments['hours_remaining'] == 0.75
    assert arguments['required_storage'] == 'cold'
    assert arguments['deadline_at'].isoformat() == payload['deadline_at']
    broker.broker_posting_async.assert_awaited_once()


@pytest.mark.parametrize('result_status', ['success', 'draft', 'no_viable_match', 'blocked'])
def test_stream_preserves_result_status_and_safe_ordered_progress(client, result_status):
    http, broker = client
    result = {
        'status': result_status,
        'requires_confirmation': True,
        'generation_mode': 'fixture',
        'execution_trace': [{'kind': 'mcp_tool_result', 'name': 'vector_search_recipients'}],
    }

    async def execute(**arguments):
        report = arguments['progress_callback']
        await report('connecting', {})
        await report('retrieving', {'message': 'private-upstream-response'})
        await report('checking_constraints', {
            'candidate_count': 12, 'eligible_count': True,
            'trace': 'private-raw-trace', 'donor_name': 'private-contact',
        })
        await report('unsupported-sensitive-stage', {'candidate_count': 99})
        await report('finalizing', {})
        return result

    broker.broker_posting_async.side_effect = execute
    response = http.post('/api/match/stream', json=offer())
    assert response.status_code == 200
    assert 'application/x-ndjson' in response.headers['content-type']
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-accel-buffering'] == 'no'
    assert response.text.endswith('\n')
    records = [json.loads(line) for line in response.text.splitlines()]
    assert records[0]['type'] == 'progress'
    assert records[0]['stage'] == 'accepted'
    assert [r['stage'] for r in records if r['type'] == 'progress'] == [
        'accepted', 'connecting', 'retrieving', 'checking_constraints', 'finalizing',
    ]
    assert records[3]['candidate_count'] == 12
    assert 'eligible_count' not in records[3]
    assert [r['sequence'] for r in records] == list(range(1, len(records) + 1))
    assert len({r['run_id'] for r in records}) == 1
    assert records[-1]['type'] == 'result'
    assert records[-1]['data'] == result
    assert 'private-' not in response.text
    assert 'unsupported-sensitive-stage' not in response.text
    broker.broker_posting_async.assert_awaited_once()


def test_stream_runtime_failure_is_terminal_sanitized_error(client):
    http, broker = client
    broker.broker_posting_async.side_effect = RuntimeError('private-token-do-not-return')
    response = http.post('/api/match/stream', json=offer())
    # Once streaming starts, failure is a typed terminal record, not an HTTP 502.
    assert response.status_code == 200
    records = [json.loads(line) for line in response.text.splitlines()]
    assert records[0]['stage'] == 'accepted'
    assert records[-1]['type'] == 'error'
    assert records[-1]['code'] == 'match_failed'
    assert not any(r['type'] == 'result' for r in records)
    assert 'private-token' not in response.text
    broker.broker_posting_async.assert_awaited_once()


def test_stream_broker_initialization_failure_is_sanitized(client, monkeypatch):
    http, broker = client

    def fail():
        raise RuntimeError('private-runtime-credential-detail')

    monkeypatch.setattr(api, 'get_broker_agent', fail)
    response = http.post('/api/match/stream', json=offer())
    assert response.status_code == 200
    records = [json.loads(line) for line in response.text.splitlines()]
    assert records[-1]['type'] == 'error'
    assert records[-1]['code'] == 'match_failed'
    assert 'private-runtime' not in response.text
    broker.broker_posting_async.assert_not_called()


@pytest.mark.asyncio
async def test_stream_endpoint_cancels_broker_when_response_send_disconnects(client):
    """Exercise the route's actual response class, not only the iterator helper."""
    _, broker = client
    cancelled = asyncio.Event()
    started = asyncio.Event()

    async def execute(**arguments):
        started.set()
        try:
            await arguments['progress_callback']('retrieving', {})
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    broker.broker_posting_async.side_effect = execute
    response = await api.match_surplus_stream(api.MatchRequest.model_validate(offer()))

    async def receive():
        await asyncio.Event().wait()

    async def send(message):
        if b'"stage":"retrieving"' in message.get('body', b''):
            raise OSError('simulated disconnected client')

    scope = {
        'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'},
        'method': 'POST', 'path': '/api/match/stream',
        'headers': [], 'query_string': b'', 'http_version': '1.1',
    }
    try:
        try:
            await asyncio.wait_for(response(scope, receive, send), timeout=0.5)
        except ClientDisconnect:
            pass
        assert started.is_set()
        await asyncio.wait_for(cancelled.wait(), timeout=0.2)
        broker.broker_posting_async.assert_awaited_once()
    finally:
        # A failing regression must not leave a fixture runner alive in pytest.
        await response.body_iterator.aclose()


def test_stream_response_import_works_with_production_app_directory():
    """The production uvicorn command imports api directly from agent/, not as a package."""
    agent_directory = Path(__file__).parents[1] / 'agent'
    script = """
import asyncio
import sys
sys.path.insert(0, sys.argv[1])
import api

async def check():
    payload = {
        key: value for key, value in api.SAMPLE_SCENARIOS[0].items()
        if key in api.MatchRequest.model_fields
    }
    response = await api.match_surplus_stream(api.MatchRequest.model_validate(payload))
    assert type(response).__name__ == 'MatchStreamingResponse'
    assert type(response).__module__ == 'match_stream'
    await response.body_iterator.aclose()
    assert 'broker' not in sys.modules
    assert 'agent.broker' not in sys.modules

asyncio.run(check())
"""
    checked = subprocess.run(
        [sys.executable, '-I', '-c', script, str(agent_directory)],
        text=True, capture_output=True, timeout=10,
    )
    assert checked.returncode == 0, checked.stderr


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


def test_foodkeeper_endpoint_is_read_only_bounded_and_does_not_call_broker(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    http, broker = client
    payload = {"status": "options", "products": [{"name": "Milk", "subtitle": "Whole", "product_id": "1"}], "selection_required": True, "selected_product_id": None}
    search = Mock(return_value=payload)
    monkeypatch.setattr(api, "get_foodkeeper_service", lambda: SimpleNamespace(search=search))
    response = http.get('/api/foodkeeper', params={'query': ' whole milk '})
    assert response.status_code == 200
    assert response.json() == payload
    search.assert_called_once_with('whole milk', limit=5)
    broker.broker_posting_async.assert_not_called()


@pytest.mark.parametrize('query', ['', 'a', '  ', 'x' * 121])
def test_foodkeeper_rejects_invalid_query_before_reading_dataset(client, monkeypatch, query):
    from unittest.mock import Mock
    http, _ = client
    service = Mock()
    monkeypatch.setattr(api, 'get_foodkeeper_service', service)
    assert http.get('/api/foodkeeper', params={'query': query}).status_code == 422
    service.assert_not_called()


def test_foodkeeper_failure_does_not_assume_life_or_leak_error(client, monkeypatch):
    from types import SimpleNamespace
    http, _ = client
    def fail(*args, **kwargs):
        raise RuntimeError('private-path-detail')
    monkeypatch.setattr(api, 'get_foodkeeper_service', lambda: SimpleNamespace(search=fail))
    response = http.get('/api/foodkeeper', params={'query': 'spinach'})
    assert response.status_code == 503
    assert response.json()['products'] == []
    assert 'No shelf life has been assumed' in response.json()['message']
    assert 'private-path-detail' not in response.text
