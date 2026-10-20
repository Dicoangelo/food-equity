"""Bounded browser presentation context, explicitly NOT authenticated broker evidence.

Read tools never invoke a matcher, provider, URL, SQL, or operational action. A malicious
browser can alter display facts; every result retains that untrusted-origin limitation.
"""
from datetime import datetime, timezone
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from agent.source_state import SourceState, aggregate_status

LIMITATION = ('Browser-reported display context; its origin has not been verified by this session. '
              'It cannot authorize eligibility or operations. Generated/planted capacity, acceptance '
              'and pickup remain unconfirmed. No new matching or data-provider request was made.')


def recipient_id(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('Invalid recipient identifier')
    value = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}', value):
        raise ValueError('Invalid recipient identifier')
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, str_strip_whitespace=True)


class Candidate(StrictModel):
    recipient_id: str
    name: str = Field(min_length=1, max_length=160)
    constraint_status: Literal['eligible', 'needs_confirmation', 'rejected', 'not_evaluated']
    profile_source: Literal['generated', 'planted', 'unknown']
    city: str | None = Field(None, max_length=160)
    cold_storage: str | None = Field(None, max_length=40)
    max_pallets: float | None = Field(None, ge=0, le=1000000)
    pickup_window_hours: float | None = Field(None, ge=0, le=10000)
    time_margin_hours: float | None = Field(None, ge=-10000, le=10000)
    composite_score: float | None = Field(None, ge=0, le=1)
    reason_codes: list[str] = Field(default_factory=list, max_length=20)
    elimination_reasons: list[str] = Field(default_factory=list, max_length=10)
    confirmations_required: list[str] = Field(default_factory=list, max_length=15)
    acceptance_evidence: list[str] = Field(default_factory=list, max_length=10)
    _identifier = field_validator('recipient_id', mode='before')(recipient_id)

    @field_validator('reason_codes', 'elimination_reasons', 'confirmations_required', 'acceptance_evidence')
    @classmethod
    def bounded_text(cls, values):
        if any(len(text) > 500 for text in values):
            raise ValueError('Evidence text is too long')
        return values

    @field_validator('max_pallets', 'pickup_window_hours', 'time_margin_hours', 'composite_score', mode='before')
    @classmethod
    def finite_number(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            raise ValueError('Finite number required')
        return value


class ResultProjection(StrictModel):
    status: Literal['none', 'success', 'draft', 'no_viable_match', 'no_match', 'blocked'] = 'none'
    winner_id: str | None = None
    alternative_ids: list[str] = Field(default_factory=list, max_length=2)
    evaluated_at: datetime | None = None
    deadline_at: datetime | None = None
    candidates: list[Candidate] = Field(default_factory=list, max_length=16)
    candidates_truncated: bool = False

    @field_validator('winner_id', mode='before')
    @classmethod
    def winner(cls, value):
        return None if value is None else recipient_id(value)

    @field_validator('alternative_ids', mode='before')
    @classmethod
    def alternatives(cls, values):
        if not isinstance(values, list):
            raise ValueError('List required')
        return [recipient_id(value) for value in values]

    @field_validator('evaluated_at', 'deadline_at')
    @classmethod
    def aware_timestamp(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError('Timezone required')
        return value

    @model_validator(mode='after')
    def consistent_ids(self):
        candidates = {row.recipient_id: row for row in self.candidates}
        if len(candidates) != len(self.candidates):
            raise ValueError('Duplicate candidate identifier')
        proposed = ([self.winner_id] if self.winner_id else []) + self.alternative_ids
        if len(proposed) != len(set(proposed)):
            raise ValueError('Duplicate proposal identifier')
        if any(identifier not in candidates or candidates[identifier].constraint_status != 'eligible'
               for identifier in proposed):
            raise ValueError('Proposal identifiers must reference reported eligible rows')
        if self.status not in ('success', 'draft') and proposed:
            raise ValueError('A non-proposal result cannot contain proposed alternatives')
        return self


class Filters(StrictModel):
    outcome: Literal['all', 'eligible', 'needs_confirmation', 'rejected', 'not_evaluated'] = 'all'
    provenance: Literal['all', 'generated', 'planted', 'unknown'] = 'all'
    storage: Literal['all', 'cold', 'ambient', 'frozen'] = 'all'
    search: str = Field('', max_length=100)


class MapPosition(StrictModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    zoom: float = Field(ge=0, le=22)
    _finite = field_validator('latitude', 'longitude', 'zoom', mode='before')(Candidate.finite_number.__func__)


class ViewContext(StrictModel):
    schema_version: Literal[1] = 1
    offer_revision: int = Field(0, ge=0, le=1000000, strict=True)
    view_revision: int = Field(0, ge=0, le=1000000, strict=True)
    evaluated_offer_revision: int | None = Field(None, ge=0, le=1000000, strict=True)
    selected_recipient_id: str | None = None
    visible_recipient_ids: list[str] = Field(default_factory=list, max_length=50)
    visible_count: int = Field(0, ge=0, le=1000000, strict=True)
    truncated: bool = False
    filters: Filters = Field(default_factory=Filters)
    map: MapPosition | None = None
    result: ResultProjection = Field(default_factory=ResultProjection)
    sources: list[SourceState] = Field(default_factory=list, max_length=8)

    @field_validator('selected_recipient_id', mode='before')
    @classmethod
    def selected(cls, value):
        return None if value is None else recipient_id(value)

    @field_validator('visible_recipient_ids', mode='before')
    @classmethod
    def visible(cls, values):
        if not isinstance(values, list):
            raise ValueError('List required')
        return [recipient_id(value) for value in values]

    @model_validator(mode='after')
    def counts(self):
        if len(set(self.visible_recipient_ids)) != len(self.visible_recipient_ids):
            raise ValueError('Duplicate visible identifier')
        if self.visible_count < len(self.visible_recipient_ids):
            raise ValueError('Visible count is inconsistent')
        if self.visible_count > len(self.visible_recipient_ids) and not self.truncated:
            raise ValueError('Truncation must be explicit')
        return self


def read_view(view, tool, identifier=None, now=None):
    """A read-only presentation result; no eligibility assertion or side effect."""
    now = now or datetime.now(timezone.utc)
    sources = [state.model_copy(update={'status':'stale','cache_state':'expired','error_code':'snapshot_expired'})
               if state.expires_at is not None and state.expires_at <= now and state.status in ('ready','partial')
               else state for state in view.sources]
    common = {'authority': 'untrusted_browser_projection', 'limitation': LIMITATION,
              'operational_availability': 'unconfirmed',
              'offer_revision': view.offer_revision, 'view_revision': view.view_revision,
              'sources': [state.model_dump(mode='json') for state in sources],
              'source_status': aggregate_status([state.status for state in sources])}
    if tool not in ('read_current_match', 'read_recipient_evidence', 'show_alternatives'):
        return {**common, 'outcome': 'refused', 'code': 'unsupported_tool',
                'message': 'Only bounded current-match and recipient display reads are available.'}
    candidates = {candidate.recipient_id: candidate for candidate in view.result.candidates}
    if tool == 'read_recipient_evidence' and identifier in candidates and candidates[identifier].constraint_status == 'not_evaluated':
        return {**common, 'outcome':'applied', 'effect':'read_only',
                'message':'Showing the captured browser profile. It has not been evaluated for this offer; acceptance and capacity are unconfirmed.',
                'reported_recipient':candidates[identifier].model_dump(mode='json')}
    if view.result.status == 'none' or view.evaluated_offer_revision is None:
        return {**common, 'outcome': 'refused', 'code': 'not_evaluated',
                'message': 'No current evaluation is displayed. Review and run the offer explicitly.'}
    if view.evaluated_offer_revision != view.offer_revision:
        return {**common, 'outcome': 'stale', 'code': 'offer_changed',
                'message': 'The displayed evaluation belongs to an older offer. Run the reviewed current offer.'}
    if view.result.deadline_at is not None and view.result.deadline_at <= now:
        return {**common, 'outcome': 'stale', 'code': 'evaluation_expired',
                'message': 'The displayed donor deadline has passed. Confirm new donor facts before another run.'}
    if view.result.status == 'blocked':
        return {**common, 'outcome': 'refused', 'code': 'evaluation_unavailable',
                'message': 'The displayed retrieval/evaluation failed; that is not a no-match finding.'}
    if tool == 'read_recipient_evidence':
        identifier = recipient_id(identifier)
        if identifier not in candidates:
            return {**common, 'outcome': 'refused', 'code': 'recipient_not_in_context',
                    'message': 'That identifier is not present in this bounded display snapshot.'}
        return {**common, 'outcome': 'applied', 'effect': 'read_only',
                'message': 'Showing browser-reported evidence for the captured recipient; acceptance remains unconfirmed.',
                'reported_recipient': candidates[identifier].model_dump(mode='json')}
    if tool == 'show_alternatives':
        return {**common, 'outcome': 'applied', 'effect': 'read_only',
                'message': 'Showing the displayed alternative order. No reranking or new match was performed.',
                'reported_alternatives': [candidates[key].model_dump(mode='json') for key in view.result.alternative_ids]}
    return {**common, 'outcome': 'applied', 'effect': 'read_only',
            'message': 'Showing the browser-reported current evaluation; all recipient operations remain unconfirmed.',
            'reported_result': view.result.model_dump(mode='json')}
