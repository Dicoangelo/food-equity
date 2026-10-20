"""Shared evidence status. Retrieval freshness never confirms recipient operations."""
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

STATUSES = ('ready', 'partial', 'stale', 'unavailable')
ERROR_CODES = {'data_unavailable', 'empty_layer', 'invalid_state', 'missing_source', 'snapshot_expired'}


class Coverage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    returned: int | None = Field(None, ge=0, le=1000000)
    matching_source_rows: int | None = Field(None, ge=0, le=10000000)
    row_cap: int | None = Field(None, ge=0, le=1000000)
    truncated: bool = False
    skipped_invalid_or_duplicate: int = Field(0, ge=0, le=1000000)


class SourceState(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    source_id: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9_.:-]+$')
    source_kind: Literal['city_recipients', 'census_tracts', 'match_result', 'recipient_profile']
    fetched_at: datetime | None = None
    # The current starter tables supply neither observation nor operational confirmation dates.
    # Do not accept those as authority from browser-supplied context.
    source_observed_at: None = None
    operational_confirmed_at: None = None
    provenance: Literal['generated', 'planted', 'unknown'] = 'unknown'
    status: Literal['ready', 'partial', 'stale', 'unavailable'] = 'unavailable'
    coverage: Coverage = Field(default_factory=Coverage)
    cache_state: Literal['fresh', 'hit', 'expired', 'none'] = 'none'
    expires_at: datetime | None = None
    error_code: Literal['data_unavailable', 'empty_layer', 'invalid_state', 'missing_source', 'snapshot_expired'] | None = None

    @field_validator('fetched_at', 'expires_at')
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError('Timezone required')
        return value


def aggregate_status(states):
    """Healthy plus failed is partial; partial never disappears in mixed states."""
    values = [state if state in STATUSES else 'unavailable' for state in states]
    if not values or all(state == 'unavailable' for state in values):
        return 'unavailable'
    if 'stale' in values:
        return 'stale'
    if any(state != 'ready' for state in values):
        return 'partial'
    return 'ready'


def city_sources(payload):
    """Derive both layer states from existing cache, row-cap and read-error evidence."""
    result = []
    fetched = datetime.fromisoformat(payload['as_of'])
    cache = payload.get('cache', {})
    ttl = cache.get('effective_ttl_seconds', cache.get('ttl_seconds', 0))
    for layer, kind in (('tracts', 'census_tracts'), ('recipients', 'city_recipients')):
        limits = payload.get('limits', {}).get(layer, {})
        rows = payload.get(layer, [])
        errors = [e.get('code') for e in payload.get('errors', []) if e.get('layer') == layer]
        partial = bool(limits.get('truncated') or limits.get('skipped_invalid_or_duplicate'))
        if layer == 'tracts':
            partial = partial or any(row.get('need_score') is None for row in rows)
        status = 'unavailable' if not rows or errors else 'partial' if partial else 'ready'
        state = SourceState(
            source_id=f'city:{layer}', source_kind=kind, fetched_at=fetched,
            status=status, coverage=Coverage.model_validate(limits),
            cache_state='hit' if cache.get('hit') else 'fresh',
            expires_at=fetched + timedelta(seconds=ttl),
            error_code=next((e for e in errors if e in ERROR_CODES), None),
        )
        result.append(state.model_dump(mode='json'))
    return result


def with_source_states(payload):
    payload['sources'] = city_sources(payload)
    payload['source_status'] = aggregate_status([s['status'] for s in payload['sources']])
    return payload
