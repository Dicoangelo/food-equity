/* Retrieval freshness is separate from source observation and operational confirmation. */
const SourceStateUI = (() => {
  const statuses = ['ready','partial','stale','unavailable'];
  const kinds = ['city_recipients','census_tracts','match_result','recipient_profile'];
  const provenance = ['generated','planted','unknown'];
  const errors = ['data_unavailable','empty_layer','invalid_state','missing_source','snapshot_expired'];
  const timestamp = value => typeof value === 'string' && value.length <= 40
    && /(Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value)) ? value : null;
  const count = (value, maximum) => Number.isInteger(value) && value >= 0 && value <= maximum ? value : null;
  function normalize(raw, now = Date.now()) {
    raw = raw && typeof raw === 'object' ? raw : {};
    const coverage = raw.coverage || {};
    const state = {
      source_id: typeof raw.source_id === 'string' && /^[A-Za-z0-9_.:-]{1,160}$/.test(raw.source_id) ? raw.source_id : 'source:unknown',
      source_kind: kinds.includes(raw.source_kind) ? raw.source_kind : 'recipient_profile',
      fetched_at: timestamp(raw.fetched_at), expires_at: timestamp(raw.expires_at),
      source_observed_at:null, operational_confirmed_at:null,
      provenance:provenance.includes(raw.provenance) ? raw.provenance : 'unknown',
      status:statuses.includes(raw.status) ? raw.status : 'unavailable',
      coverage:{returned:count(coverage.returned,1000000),matching_source_rows:count(coverage.matching_source_rows,10000000),
        row_cap:count(coverage.row_cap,1000000),truncated:coverage.truncated === true,
        skipped_invalid_or_duplicate:count(coverage.skipped_invalid_or_duplicate,1000000) ?? 0},
      cache_state:['fresh','hit','expired','none'].includes(raw.cache_state) ? raw.cache_state : 'none',
      error_code:errors.includes(raw.error_code) ? raw.error_code : null,
    };
    if (state.status !== 'unavailable' && state.expires_at && Date.parse(state.expires_at) <= now) {
      state.status = 'stale'; state.cache_state = 'expired'; state.error_code = 'snapshot_expired';
    }
    return state;
  }
  function aggregate(states) {
    const values = states.map(state => typeof state === 'string' ? state : state?.status)
      .map(status => statuses.includes(status) ? status : 'unavailable');
    if (!values.length || values.every(status=>status === 'unavailable')) return 'unavailable';
    if (values.includes('stale')) return 'stale';
    return values.every(status=>status === 'ready') ? 'ready' : 'partial';
  }
  function cityStates(payload, now = Date.now()) {
    return (Array.isArray(payload?.sources) ? payload.sources : []).slice(0,8).map(raw=>normalize(raw,now));
  }
  function describe(state) {
    const normalized = normalize(state), c = normalized.coverage;
    const label = {city_recipients:'Recipient dataset',census_tracts:'Census dataset',match_result:'Current match evidence',recipient_profile:'Profile evidence'}[normalized.source_kind];
    const countText = c.returned == null ? 'coverage unknown' : `${c.returned} rows${c.truncated ? ' · truncated' : ''}${c.skipped_invalid_or_duplicate ? ` · ${c.skipped_invalid_or_duplicate} invalid/duplicate rows skipped` : ''}`;
    return `${label}: ${normalized.status} · ${countText} · fetched ${normalized.fetched_at || 'unknown'}. Source observation date unknown; operations unconfirmed.`;
  }
  function render(container, states) {
    if (!container) return;
    const normalized = states.map(state=>normalize(state));
    container.replaceChildren();
    container.appendChild(textNode('p',`Evidence sources: ${aggregate(normalized)}. Retrieval freshness does not confirm recipient availability.`,'text-xs text-warning'));
    normalized.forEach(state=>container.appendChild(textNode('p',describe(state),'text-xs text-muted mt-2')));
  }
  return {normalize,aggregate,cityStates,describe,render};
})();
