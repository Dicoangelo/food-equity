/* Read-only recipient exploration. Match decisions and clocks come from the broker.
 * No model requests, weighting controls, reservation or outreach side effects.
 */
const RecipientExplorer = (() => {
  let selected = null;
  let returnFocus = null;
  let returnFocusRegion = null;
  let backdrop = null;
  const inertBackground = new Map();
  const markers = new Set();
  const labels = {
    eligible: 'Eligible proposal',
    needs_confirmation: 'Needs confirmation',
    rejected: 'Rejected for this offer',
    not_evaluated: 'Not evaluated for this offer',
  };
  const $ = id => document.getElementById(id);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const number = (value, suffix = '') => finite(value) ? `${value}${suffix}` : 'Unknown';
  const boolean = value => value === true ? 'Yes (profile)' : value === false ? 'No (profile)' : 'Unknown';
  const percent = value => finite(value) && value >= 0 && value <= 100 ? `${value.toFixed(1)}%` : 'Unknown';
  const rows = value => Array.isArray(value) ? value : [];
  const idOf = row => row && row.recipient_id != null && String(row.recipient_id).trim() !== '' ? String(row.recipient_id) : null;
  const mobile = () => typeof window.matchMedia === 'function' && window.matchMedia('(max-width:760px)').matches;
  const sourceLabel = row => row.profile_source === 'generated' ? 'Generated operations'
    : row.profile_source === 'planted' ? 'Invented teaching case' : 'Unknown provenance';
  const evaluatedPayload = () => currentPayload && ['success', 'draft', 'no_match', 'no_viable_match'].includes(currentPayload.status);

  function mergedRecipients() {
    const merged = new Map();
    rows(cityPayload?.recipients).forEach(row => {
      const id = idOf(row);
      if (id !== null) merged.set(id, {...row, recipient_id: id, _decision: 'not_evaluated', _offerRole: null, _offerOrder: null});
    });
    if (evaluatedPayload()) {
      const groups = [
        ['winner', currentPayload.winner ? [currentPayload.winner] : [], 'eligible'],
        ['backup', rows(currentPayload.runners_up).slice(0, 2), 'eligible'],
        ['pending', rows(currentPayload.pending_confirmation), 'needs_confirmation'],
        ['rejected', rows(currentPayload.eliminated_decoys), 'rejected'],
      ];
      let offerOrder = 0;
      groups.forEach(([role, candidates, fallback]) => candidates.forEach(row => {
        const id = idOf(row);
        if (id === null) return;
        // Explicit match nulls stay unknown. Never fill them from an older city read.
        const item = {...(merged.get(id) || {}), ...row, recipient_id: id};
        if (Object.prototype.hasOwnProperty.call(row, 'latitude')) item.lat = row.latitude;
        if (Object.prototype.hasOwnProperty.call(row, 'longitude')) item.lon = row.longitude;
        const decision = ['eligible', 'needs_confirmation', 'rejected'].includes(row.constraint_status)
          ? row.constraint_status : fallback;
        merged.set(id, {...item, _decision: decision, _offerRole: role, _offerOrder: offerOrder++});
      }));
    }
    // Surface the broker's existing proposal order; never recompute a ranking.
    // Stable sort preserves source order for city-only profiles.
    return [...merged.values()].sort((a, b) =>
      (a._offerOrder ?? Infinity) - (b._offerOrder ?? Infinity));
  }

  function visibleRecipients() {
    const search = ($('recipient-search')?.value || '').trim().toLocaleLowerCase();
    const outcome = $('outcome-filter')?.value || 'all';
    const provenance = $('provenance-filter')?.value || 'all';
    const storage = $('capability-filter')?.value || 'all';
    return mergedRecipients().filter(row =>
      (!search || `${row.name || ''} ${row.city || ''} ${row.zip_code || ''}`.toLocaleLowerCase().includes(search)) &&
      (outcome === 'all' || row._decision === outcome) &&
      (provenance === 'all' || (provenance === 'unknown'
        ? !['generated', 'planted'].includes(row.profile_source) : row.profile_source === provenance)) &&
      capabilityVisible(row, storage)
    );
  }

  function comparisonRows() {
    const all = mergedRecipients();
    return ['winner', 'backup'].flatMap(role => all.filter(row =>
      row._offerRole === role && row._decision === 'eligible')).slice(0, 3);
  }

  function scoreComponents(row) {
    if (row?._decision !== 'eligible') return null;
    const distance = row.semantic_distance;
    if (!finite(distance) || distance < 0 || distance > 2 || !finite(row.composite_score)) return null;
    const poverty = finite(row.tract_poverty_pct) && row.tract_poverty_pct >= 0 && row.tract_poverty_pct <= 100
      ? row.tract_poverty_pct / 100 : null;
    const vehicle = finite(row.tract_no_vehicle_pct) && row.tract_no_vehicle_pct >= 0 && row.tract_no_vehicle_pct <= 100
      ? row.tract_no_vehicle_pct / 100 : null;
    const semantic = Math.max(0, 1 - distance);
    const need = 0.5 * (poverty ?? 0) + 0.5 * (vehicle ?? 0);
    const logistics = 0.5 + (row.refrigerated_transport === true ? 0.3 : 0) + (row.loading_dock === true ? 0.2 : 0);
    const weightedSemantic = 0.40 * semantic, weightedNeed = 0.35 * need, weightedLogistics = 0.25 * logistics;
    return {semantic, need, logistics, weightedSemantic, weightedNeed, weightedLogistics,
      approximateTotal: weightedSemantic + weightedNeed + weightedLogistics,
      authoritativeTotal: row.composite_score, missingNeed: poverty === null || vehicle === null};
  }

  function badge(text, kind = '') {
    return textNode('span', text, `recipient-badge ${kind}`);
  }
  function appendFacts(parent, values) {
    const facts = document.createElement('dl');
    facts.className = 'recipient-facts';
    values.forEach(([title, value]) => {
      const pair = document.createElement('div');
      pair.appendChild(textNode('dt', title));
      pair.appendChild(textNode('dd', value));
      facts.appendChild(pair);
    });
    parent.appendChild(facts);
  }
  function section(title) {
    const node = document.createElement('section');
    node.className = 'recipient-section';
    node.appendChild(textNode('h3', title));
    return node;
  }
  function list(parent, values, fallback) {
    if (!values.length) {
      if (fallback) parent.appendChild(textNode('p', fallback, 'recipient-note'));
      return;
    }
    const ul = document.createElement('ul');
    values.forEach(value => ul.appendChild(textNode('li', value)));
    parent.appendChild(ul);
  }
  function decisionText(row) {
    return row._decision === 'eligible' ? (row._offerRole === 'winner' ? 'Proposed recipient' : 'Eligible backup')
      : labels[row._decision];
  }
  function renderList() {
    const container = $('recipient-list');
    if (!container) return;
    const all = mergedRecipients(), visible = visibleRecipients();
    $('recipient-count').textContent = `${visible.length} of ${all.length} profiles · availability unconfirmed`;
    container.replaceChildren();
    if (!visible.length) {
      container.appendChild(textNode('p', all.length
        ? 'No profiles match these filters. Clear search or change a filter.'
        : 'No recipient profiles loaded. Load city data or run an offer to inspect returned candidates.',
      'recipient-empty'));
    }
    visible.forEach(row => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'recipient-card';
      button.setAttribute('data-recipient-id', row.recipient_id);
      button.setAttribute('aria-pressed', String(selected === row.recipient_id));
      button.setAttribute('aria-controls', 'recipient-detail');
      button.setAttribute('aria-label', `${row.name || 'Unknown recipient'}, ${decisionText(row)}. Open profile`);
      button.appendChild(textNode('strong', row.name || 'Unknown recipient', 'recipient-card-name'));
      button.appendChild(textNode('span', `${row.city || 'Unknown city'} · ${row.zip_code || 'ZIP unknown'}`, 'recipient-note'));
      const badges = document.createElement('span');
      badges.className = 'recipient-badges';
      badges.appendChild(badge(decisionText(row), row._decision));
      badges.appendChild(badge(sourceLabel(row)));
      button.appendChild(badges);
      button.appendChild(textNode('span',
        `${row.cold_storage || 'Storage unknown'} · ${number(row.max_pallets, ' pallets maximum')}`, 'recipient-card-fact'));
      button.appendChild(textNode('span',
        `Pickup estimate: ${number(row.pickup_eta_hours ?? row.pickup_window_hours, ' h')} · ${row.service_days || 'Days unknown'}`,
        'recipient-card-fact'));
      button.appendChild(textNode('span', selected === row.recipient_id ? '✓ Selected profile' : 'View evidence →', 'recipient-card-action'));
      button.onclick = () => select(row.recipient_id, button);
      container.appendChild(button);
    });
  }

  function renderFit(parent, row) {
    const fit = section('Offer fit');
    if (row._decision !== 'eligible') {
      fit.appendChild(textNode('p', row._decision === 'not_evaluated'
        ? 'No offer-fit score. This city profile has not been evaluated for the current offer.'
        : 'Not ranked: this candidate did not pass every hard gate. Semantic similarity cannot override a rejection or missing facts.'));
      parent.appendChild(fit);
      return;
    }
    const components = scoreComponents(row);
    fit.appendChild(textNode('p', finite(row.composite_score)
      ? `Backend offer-fit score: ${row.composite_score.toFixed(4)} / 1`
      : 'Backend offer-fit score: Unknown', 'recipient-fit-total'));
    fit.appendChild(textNode('p', 'Ranked after storage, capacity, timing, schedule and acceptance gates. This is an offer ranking, not an organization rating.', 'recipient-note'));
    const details = document.createElement('details');
    details.appendChild(textNode('summary', 'How this offer-fit score is built'));
    details.appendChild(textNode('p', '0.40 × semantic + 0.35 × need + 0.25 × logistics', 'recipient-formula'));
    if (components) {
      [
        ['Semantic · 40%', components.semantic, components.weightedSemantic],
        ['Neighborhood proxy · 35%', components.need, components.weightedNeed],
        ['Logistics features · 25%', components.logistics, components.weightedLogistics],
      ].forEach(([label, value, contribution]) => {
        const line = document.createElement('div');
        line.className = 'recipient-component';
        line.appendChild(textNode('span', `${label}: ${value.toFixed(4)} → ${contribution.toFixed(4)}`));
        const meter = document.createElement('meter');
        meter.min = 0; meter.max = 1; meter.value = value;
        meter.setAttribute('aria-label', label);
        line.appendChild(meter);
        details.appendChild(line);
      });
      details.appendChild(textNode('p',
        `Component reconstruction: approximately ${components.approximateTotal.toFixed(4)}. API percentages and cosine distance are rounded; the backend score above is authoritative.`,
        'recipient-note'));
      if (components.missingNeed) details.appendChild(textNode('p',
        'Missing neighborhood inputs contribute zero in the current engine. Their raw values remain Unknown; this is not evidence of zero need.',
        'recipient-warning'));
    } else {
      details.appendChild(textNode('p', 'Some score inputs were not returned. Components cannot be reconstructed.', 'recipient-warning'));
    }
    details.appendChild(textNode('p', 'Semantic = max(0, 1 − cosine distance). Need = 0.5 × poverty rate + 0.5 × no-vehicle rate. Logistics = 0.5 + 0.3 for documented refrigerated transport + 0.2 for a documented loading dock.', 'recipient-note'));
    details.appendChild(textNode('p', 'Unknown logistics flags receive no feature bonus; they remain unconfirmed. Provenance is an independent evidence label.', 'recipient-note'));
    fit.appendChild(details);
    parent.appendChild(fit);
  }

  function renderDetail() {
    if (!selected) return;
    const row = mergedRecipients().find(item => item.recipient_id === selected);
    if (!row) { close(); return; }
    const container = $('recipient-detail-content');
    container.replaceChildren();
    const title = textNode('h2', row.name || 'Unknown recipient');
    title.id = 'recipient-detail-title';
    container.appendChild(title);
    container.appendChild(textNode('p', `${row.city || 'Unknown city'} · ${row.zip_code || 'ZIP unknown'} · Profile ${row.recipient_id}`, 'recipient-note'));
    const badges = document.createElement('div');
    badges.className = 'recipient-badges';
    badges.appendChild(badge(sourceLabel(row)));
    badges.appendChild(badge(row.geography_precision === 'zip_centroid' ? 'Approximate ZIP centroid' : 'Location precision unverified'));
    badges.appendChild(badge('Availability unknown'));
    container.appendChild(badges);
    container.appendChild(textNode('p', row.profile_source === 'planted'
      ? 'This organization and its operating profile are invented for the teaching dataset.'
      : row.profile_source === 'generated'
        ? 'Organization identity comes from the starter data; operating details are generated and require confirmation.'
        : 'Profile provenance has not been established. Confirm identity and all operating details.', 'recipient-provenance'));

    const decision = section('Decision for this offer');
    decision.appendChild(badge(labels[row._decision], row._decision));
    decision.appendChild(textNode('p', row._decision === 'eligible'
      ? 'A profile-based proposal. Recipient acceptance, available capacity and collection still need confirmation.'
      : row._decision === 'not_evaluated'
        ? 'Browse the profile, then run the current offer to retrieve and evaluate candidates.'
        : 'Review the returned reasons before changing any offer facts. This view cannot make a candidate eligible.',
    'recipient-note'));
    if (row._decision !== 'not_evaluated') {
      list(decision, rows(row.elimination_reasons), null);
      appendFacts(decision, [
        ['Backend reason codes', rows(row.reason_codes).join(', ') || 'None returned'],
        ['Acceptance check', row.acceptance_check || 'Unknown'],
      ]);
      list(decision, rows(row.acceptance_evidence), 'No supported restriction evidence was returned. This does not verify acceptance.');
    }
    container.appendChild(decision);

    const capability = section('Capacity and storage');
    const donor = evaluatedPayload() ? (currentPayload.donor || {}) : {};
    appendFacts(capability, [
      ['Requested quantity', number(donor.pallet_count, ' pallets')],
      ['Documented maximum', number(row.max_pallets, ' pallets')],
      ['Offer storage required', donor.required_storage || 'Unknown'],
      ['Receiving-site storage', row.cold_storage || 'Unknown'],
      ['Freezer', boolean(row.frozen_storage)],
      ['Refrigerated transport', boolean(row.refrigerated_transport)],
      ['Loading dock', boolean(row.loading_dock)],
      ['Service days', row.service_days || 'Unknown'],
      ['Weekly households (profile)', number(row.weekly_households)],
    ]);
    capability.appendChild(textNode('p', 'Documented maximum is not current availability. Vehicle refrigeration does not establish receiving-site cold storage.', 'recipient-warning'));
    container.appendChild(capability);

    const clock = section('Fixed donor clock');
    const evaluated = row._decision !== 'not_evaluated';
    appendFacts(clock, [
      ['Exact deadline', evaluated ? (row.deadline_at || donor.deadline_at || 'Unknown') : 'Not evaluated'],
      ['Evaluated at', evaluated ? (row.evaluated_at || currentPayload?.evaluated_at || 'Unknown') : 'Not evaluated'],
      ['Profile pickup estimate', number(row.pickup_window_hours, ' h')],
      ['Coordinator pickup estimate', number(row.pickup_eta_hours, ' h')],
      ['Timing basis', evaluated ? (row.timing_basis || 'Unknown') : 'Not evaluated'],
      ['Raw margin at evaluation', evaluated ? number(row.time_margin_hours, ' h') : 'Not evaluated'],
      ['Coordination buffer', evaluated ? '15 min engine default; per-run value not supplied' : 'Not evaluated'],
    ]);
    clock.appendChild(textNode('p', 'These are supplied or profile pickup estimates, not verified driving times. The coordination buffer is not a food-safety rule. Time elapsed since evaluation consumes the remaining window.', 'recipient-note'));
    const deadline = evaluated ? (row.deadline_at || donor.deadline_at) : null;
    if (deadline && Date.parse(deadline) <= Date.now()) clock.appendChild(textNode('p',
      'The displayed deadline has passed since evaluation. Confirm new donor facts before running a new offer.', 'recipient-warning'));
    container.appendChild(clock);

    const neighborhood = section('Neighborhood context');
    appendFacts(neighborhood, [
      ['Associated tract', row.tract_id || 'Unknown'],
      ['Poverty rate', percent(row.tract_poverty_pct)],
      ['Households without a vehicle', percent(row.tract_no_vehicle_pct)],
      ['Observation date', 'Not supplied by the current tables'],
    ]);
    neighborhood.appendChild(textNode('p', evaluated
      ? 'ACS-derived neighborhood indicators use an approximate tract association near a ZIP centroid. They do not establish individual hunger, an official food-desert classification or the organization’s service catchment.'
      : 'City-only profiles do not include an associated tract or its rates. No nearest-point estimate has been substituted. The map heat layer is an aggregate demographic proxy.',
    'recipient-note'));
    container.appendChild(neighborhood);
    renderFit(container, row);

    const confirmation = section('Before contacting this recipient');
    list(confirmation, rows(row.confirmations_required),
      'Confirm the actual address, acceptance of the complete manifest, available storage/capacity, receiving hours and collection plan.');
    list(confirmation, rows(row.warnings), null);
    confirmation.appendChild(textNode('p', 'Opening a profile or comparing options sends nothing and reserves no food. Review outreach drafts in the handoff step.', 'recipient-note'));
    const destination = cityPoint(row);
    const origin = evaluatedPayload() ? donor : selectedOrigin;
    if (validPoint(destination) && validPoint(origin)) {
      const link = textNode('a', 'Open approximate directions in Google Maps', 'action-secondary recipient-directions');
      link.href = `https://www.google.com/maps/dir/?api=1&origin=${origin.latitude},${origin.longitude}&destination=${destination.latitude},${destination.longitude}&travelmode=driving`;
      link.target = '_blank'; link.rel = 'noopener noreferrer';
      confirmation.appendChild(link);
      confirmation.appendChild(textNode('p', 'External navigation only. Confirm the facility entrance before travel; this app has not calculated a road route.', 'recipient-note'));
    } else confirmation.appendChild(textNode('p', 'Directions unavailable: a valid origin or recipient point was not supplied.', 'recipient-note'));
    container.appendChild(confirmation);
    updateModal();
  }

  function renderCompare() {
    const container = $('recipient-compare');
    if (!container) return;
    const expanded = container.firstElementChild?.open || false;
    container.replaceChildren();
    const candidates = comparisonRows();
    const details = document.createElement('details');
    details.className = 'recipient-comparison';
    details.open = expanded;
    details.appendChild(textNode('summary', `Compare eligible proposals (${candidates.length})`));
    if (!candidates.length) {
      details.appendChild(textNode('p', 'No eligible proposals for the current offer. Pending and rejected candidates remain in the directory with their reasons.', 'recipient-note'));
    } else {
      details.appendChild(textNode('p', `Same evaluated offer · exact donor deadline: ${currentPayload?.donor?.deadline_at || candidates[0].deadline_at || 'Unknown'}. Acceptance is unconfirmed for every proposal.`, 'recipient-note'));
      const grid = document.createElement('div');
      grid.className = 'recipient-comparison-grid';
      candidates.forEach(row => {
        const card = document.createElement('article');
        card.className = 'recipient-comparison-card';
        card.appendChild(textNode('h3', row.name || 'Unknown recipient'));
        card.appendChild(badge(decisionText(row), 'eligible'));
        card.appendChild(badge(sourceLabel(row)));
        appendFacts(card, [
          ['Backend offer fit', finite(row.composite_score) ? row.composite_score.toFixed(4) : 'Unknown'],
          ['Capacity maximum', number(row.max_pallets, ' pallets')],
          ['Storage / freezer', `${row.cold_storage || 'Unknown'} / ${boolean(row.frozen_storage)}`],
          ['Pickup estimate', number(row.pickup_eta_hours ?? row.pickup_window_hours, ' h')],
          ['Raw margin', number(row.time_margin_hours, ' h')],
          ['Receiving days', row.service_days || 'Unknown'],
        ]);
        const button = textNode('button', 'Inspect profile', 'action-secondary');
        button.type = 'button';
        button.setAttribute('data-recipient-id', row.recipient_id);
        button.onclick = () => select(row.recipient_id, button);
        card.appendChild(button);
        grid.appendChild(card);
      });
      details.appendChild(grid);
      if (candidates.length < 3) details.appendChild(textNode('p',
        'Only returned eligible proposals are shown; no extra alternatives have been invented.', 'recipient-note'));
    }
    container.appendChild(details);
  }

  function styleMarkers() {
    markers.forEach(({marker, row, baseStyle}) => {
      const chosen = selected === idOf(row);
      if (marker.setStyle) marker.setStyle(chosen
        ? {...baseStyle, radius: 9, weight: 4, dashArray: '3 2'} : baseStyle);
      const element = marker.getElement?.();
      if (element) {
        element.classList.toggle('recipient-marker-selected', chosen);
        element.setAttribute('aria-pressed', String(chosen));
      }
    });
  }
  function markMarker(marker, row) {
    const id = idOf(row);
    if (!marker || id === null) return marker;
    const record = {marker, row, baseStyle: {
      radius: marker.options?.radius || 5,
      weight: marker.options?.weight || 2,
      dashArray: marker.options?.dashArray || null,
    }};
    markers.add(record);
    const activate = () => select(id, marker.getElement?.());
    marker.on?.('click', activate);
    marker.on?.('remove', () => markers.delete(record));
    const element = marker.getElement?.();
    if (element) {
      element.setAttribute('tabindex', '0');
      element.setAttribute('role', 'button');
      element.setAttribute('aria-label', `Open profile: ${row.name || 'Unknown recipient'}`);
      element.setAttribute('aria-controls', 'recipient-detail');
      element.onkeydown = event => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(); }
      };
    }
    styleMarkers();
    return marker;
  }
  function updateModal() {
    const panel = $('recipient-detail');
    if (!panel) return;
    const modal = Boolean(selected && mobile());
    panel.setAttribute('aria-modal', String(modal));
    document.body?.classList.toggle('recipient-sheet-open', modal);
    if (modal) {
      // The drawer is outside these siblings. Preserve pre-existing inert state.
      [...(document.querySelectorAll?.('.page-shell, .workspace-sidebar, .skip-link') || [])].forEach(node => {
        if (!inertBackground.has(node)) inertBackground.set(node, {
          attribute: node.getAttribute('inert'), property: Boolean(node.inert),
        });
        node.inert = true;
        node.setAttribute('inert', '');
      });
    } else {
      inertBackground.forEach((previous, node) => {
        node.inert = previous.property;
        if (previous.attribute == null) node.removeAttribute('inert');
        else node.setAttribute('inert', previous.attribute);
      });
      inertBackground.clear();
    }
    if (modal && !backdrop && document.body?.appendChild) {
      backdrop = document.createElement('div');
      backdrop.className = 'recipient-backdrop';
      backdrop.setAttribute('aria-hidden', 'true');
      backdrop.onclick = close;
      document.body.appendChild(backdrop);
    }
    backdrop?.classList.toggle('hidden', !modal);
  }
  function refresh() {
    renderList();
    renderCompare();
    if (selected) renderDetail();
    styleMarkers();
    if ($('recipient-close')) $('recipient-close').onclick = close;
  }
  function select(id, opener) {
    id = id == null ? null : String(id);
    if (!mergedRecipients().some(row => row.recipient_id === id)) return;
    selected = id;
    returnFocus = opener || document.activeElement;
    returnFocusRegion = $('recipient-compare')?.contains?.(returnFocus) ? 'compare' : 'list';
    const panel = $('recipient-detail');
    panel.classList.remove('hidden');
    refresh();
    const row = mergedRecipients().find(item => item.recipient_id === id);
    const point = cityPoint(row);
    if (map?.panTo && validPoint(point)) map.panTo([point.latitude, point.longitude], {animate: false});
    $('recipient-close')?.focus?.({preventScroll: true});
  }
  function close() {
    const previousId = selected;
    selected = null;
    $('recipient-detail')?.classList.add('hidden');
    updateModal();
    renderList();
    styleMarkers();
    let target = returnFocus;
    // List rendering replaces its buttons; find the equivalent current control.
    if (!target?.isConnected && previousId !== null) {
      const controls = returnFocusRegion === 'compare'
        ? [...($('recipient-compare')?.querySelectorAll?.('button[data-recipient-id]') || [])]
        : [...($('recipient-list')?.children || [])];
      target = controls.find(node =>
        node.getAttribute?.('data-recipient-id') === previousId);
    }
    (target || $('recipient-search'))?.focus?.({preventScroll: true});
    returnFocus = null;
    returnFocusRegion = null;
  }
  document.addEventListener?.('keydown', event => {
    if (!selected) return;
    const panel = $('recipient-detail');
    if (event.key === 'Escape' && (mobile() || panel.contains?.(document.activeElement))) {
      event.preventDefault(); close(); return;
    }
    if (event.key !== 'Tab' || !mobile()) return;
    const focusable = [...panel.querySelectorAll('button, a[href], summary, input, select, textarea, [tabindex="0"]')]
      .filter(node => !node.disabled && node.getClientRects().length);
    if (!focusable.length) { event.preventDefault(); panel.focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {
      event.preventDefault(); first.focus();
    }
  });
  window.addEventListener?.('resize', updateModal);
  return {refresh, select, close, mergedRecipients, visibleRecipients, comparisonRows, scoreComponents,
    markerSelected: id => selected !== null && selected === String(id), markMarker, selectedId: () => selected};
})();
