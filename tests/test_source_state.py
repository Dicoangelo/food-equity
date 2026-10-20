"""One evidence contract: freshness, incomplete reads and confirmation are separate."""
from datetime import datetime, timedelta, timezone
from itertools import product
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError
from agent.city import CityDataService
from agent.source_state import SourceState, aggregate_status
from tests.test_city import TRACT, RECIPIENT


@pytest.mark.parametrize('left,right', product(('ready','partial','stale','unavailable'), repeat=2))
def test_all_mixed_states(left,right):
    expected = ('unavailable' if left == right == 'unavailable' else 'stale' if 'stale' in (left,right)
                else 'partial' if any(value != 'ready' for value in (left,right)) else 'ready')
    assert aggregate_status([left,right]) == expected
    assert aggregate_status([right,left]) == expected


def test_unknown_and_empty_states_are_never_healthy():
    assert aggregate_status([]) == aggregate_status(['unknown']) == 'unavailable'
    assert aggregate_status(['ready','unknown']) == 'partial'


def test_cache_fetch_time_does_not_change_observation_or_confirmation():
    service = CityDataService()
    service._query = Mock(side_effect=[[TRACT],[RECIPIENT]])
    first, second = service.get_city_data(), service.get_city_data()
    assert first['sources'][0]['fetched_at'] == second['sources'][0]['fetched_at']
    assert second['sources'][0]['cache_state'] == 'hit'
    assert all(s['source_observed_at'] is None and s['operational_confirmed_at'] is None for s in second['sources'])
    assert service._query.call_count == 2


def test_partial_coverage_and_failed_refresh_are_not_disguised_as_ready():
    service = CityDataService(cache_ttl_seconds=0)
    service._query = Mock(side_effect=[[TRACT],[RECIPIENT],RuntimeError('private provider detail'),[RECIPIENT], [TRACT],[RECIPIENT]])
    initial = service.get_city_data()
    failed = service.get_city_data()
    assert failed['source_status'] == 'partial'
    assert [s['status'] for s in failed['sources']] == ['unavailable','ready']
    assert failed['tracts'] == []  # No silent retention of a previous healthy layer.
    assert failed['sources'][0]['error_code'] == 'data_unavailable'
    recovered = service.get_city_data()
    assert recovered['source_status'] == 'ready'
    assert all(s['error_code'] is None for s in recovered['sources'])
    assert initial['sources'][0]['source_observed_at'] is None


def test_unknown_rate_or_dropped_row_is_partial_with_coverage():
    service = CityDataService()
    service._query = Mock(side_effect=[[dict(TRACT,poverty_rate=None),dict(TRACT)],[RECIPIENT]])
    result = service.get_city_data()
    assert result['source_status'] == 'partial'
    assert result['sources'][0]['coverage']['skipped_invalid_or_duplicate'] == 1


@pytest.mark.parametrize('field,value', [('provenance','verified_source'),('operational_confirmed_at','2026-10-20T12:00:00Z'),('source_observed_at','2026-10-20T12:00:00Z')])
def test_browser_cannot_assert_new_verified_operations(field,value):
    with pytest.raises(ValidationError):
        SourceState(source_id='city:recipients',source_kind='city_recipients',**{field:value})
