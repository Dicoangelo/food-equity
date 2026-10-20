/* Human review only. This module never requests, ranks, sends, reserves, or moves a clock. */
(function (root) {
  'use strict';
  const OWNERS = ['donor', 'recipient', 'coordinator'];
  const OFFER_FIELDS = ['posting_text', 'donor_name', 'pallet_count', 'required_storage', 'hours_remaining', 'donor_lat', 'donor_lon', 'deadline_at'];
  const str = value => typeof value === 'string' ? value : '';
  const list = value => Array.isArray(value) ? value : [];
  const time = value => typeof value === 'string' && /(Z|[+-]\d{2}:\d{2})$/i.test(value) && Number.isFinite(Date.parse(value)) ? Date.parse(value) : null;
  const presentNumber = value => value !== '' && value !== null && value !== undefined && Number.isFinite(Number(value)) ? Number(value) : null;
  const idOf = row => row && row.recipient_id !== undefined && row.recipient_id !== null ? String(row.recipient_id) : '';
  const clone = value => JSON.parse(JSON.stringify(value));

  function stableToken(value) {
    const visited = new Set();
    function encode(item) {
      if (item === null) return 'null';
      if (typeof item === 'string' || typeof item === 'boolean') return JSON.stringify(item);
      if (typeof item === 'number' && Number.isFinite(item)) return JSON.stringify(item);
      if (!item || typeof item !== 'object' || visited.has(item)) throw new TypeError('Snapshot must contain plain JSON data.');
      if (!Array.isArray(item) && Object.getPrototypeOf(item) !== Object.prototype && Object.getPrototypeOf(item) !== null) throw new TypeError('Snapshot must contain plain JSON data.');
      visited.add(item);
      const result = Array.isArray(item) ? '[' + item.map(encode).join(',') + ']' :
        '{' + Object.keys(item).filter(key => item[key] !== undefined).sort().map(key => JSON.stringify(key) + ':' + encode(item[key])).join(',') + '}';
      visited.delete(item);
      return result;
    }
    return encode(value);
  }

  function sourceModel(raw, now) {
    raw = raw && typeof raw === 'object' ? raw : {};
    let status = ['ready', 'partial', 'stale', 'unavailable'].includes(raw.status) ? raw.status : 'unavailable';
    if (status !== 'unavailable' && time(raw.expires_at) !== null && time(raw.expires_at) <= now) status = 'stale';
    const evaluation = raw.source_kind === 'match_result';
    return {
      id: str(raw.source_id) || 'source:unknown',
      kind: str(raw.source_kind) || 'recipient_profile',
      provenance: ['generated', 'planted'].includes(raw.provenance) ? raw.provenance : 'unknown',
      status,
      fetchedAt: time(raw.fetched_at) === null ? null : raw.fetched_at,
      expiresAt: time(raw.expires_at) === null ? null : raw.expires_at,
      retrieval: (evaluation ? 'Evaluation received: ' : 'Retrieved: ') + (time(raw.fetched_at) === null ? 'Unknown' : raw.fetched_at),
      freshness: 'Retrieval status: ' + status + (time(raw.expires_at) === null ? ' · expiry unknown' : ' · ' + (evaluation ? 'evaluation validity ends ' : 'expires ') + raw.expires_at),
      observation: 'Source observation date: Unknown',
      availability: 'Recipient availability: Unconfirmed',
      evaluation
    };
  }

  function ownerTask(id, question, owner) {
    return {id: str(id), question: str(question), owner: OWNERS.includes(owner) ? owner : 'coordinator'};
  }

  function intakeModel(draft, offer) {
    offer = offer || {};
    const valid = draft && draft.status === 'review_required' && draft.requires_review === true &&
      draft.fields && draft.fields.posting_text === offer.posting_text && Array.isArray(draft.questions);
    if (!valid) return {valid: false, facts: [], tasks: [], error: 'Extract this offer again before reviewing its details.'};
    const fields = draft.fields;
    const posting = str(offer.posting_text);
    const quoted = draft.quoted_evidence || {};
    const pending = list(draft.pending_facts);
    const unresolved = new Set(pending.filter(item => item && item.status !== 'supported').map(item => str(item.field_id || item.id)));
    const facts = [];
    const tasks = [];
    const addFact = (id, label, value, quote, source) => {
      if (unresolved.has(id)) return false;
      if (source === 'donor' && (!str(quote).trim() || !posting.includes(quote))) return false;
      facts.push({id, label, value, quote: str(quote), source});
      return true;
    };
    list(draft.food_terms).forEach((term, index) => {
      if (typeof term === 'string' && term.trim() && posting.includes(term)) addFact('food_terms:' + index, 'Food described', term, term, 'donor');
    });
    if (['cold', 'frozen', 'ambient'].includes(fields.required_storage) &&
        addFact('required_storage', 'Storage', fields.required_storage, quoted.required_storage, 'donor')) {
      // Source-backed donor statement; acknowledgement is not operational verification.
    } else tasks.push(ownerTask('required_storage', 'Confirm the required storage conditions.', 'donor'));
    if (Number.isInteger(fields.pallet_count) && fields.pallet_count > 0 && fields.pallet_count <= 10000 &&
        addFact('pallet_count', 'Quantity', String(fields.pallet_count) + ' pallets', quoted.pallet_count, 'donor')) {
      // Quantity is accepted only with the production parser's literal supporting quote.
    } else tasks.push(ownerTask('pallet_count', 'Confirm how many pallets are available.', 'donor'));
    const oldDeadline = time(offer.deadline_at), proposed = time(fields.deadline_at);
    const fixed = oldDeadline !== null && (proposed === null || oldDeadline <= proposed);
    if (fixed) {
      if (!addFact('deadline_at', 'Fixed pickup deadline', offer.deadline_at, '', 'existing_clock')) tasks.push(ownerTask('deadline_at', 'Confirm the fixed pickup deadline.', 'donor'));
    }
    else if (proposed !== null && addFact('deadline_at', 'Proposed pickup deadline', fields.deadline_at, quoted.pickup_deadline, 'donor')) {
      // A shorter deadline must still have a literal donor quote.
    } else tasks.push(ownerTask('deadline_at', 'Confirm the fixed pickup deadline.', 'donor'));
    draft.questions.forEach((question, index) => {
      if (typeof question === 'string' && question.trim()) tasks.push(ownerTask('question:' + index, question, 'coordinator'));
      else if (question && typeof question.question === 'string') tasks.push(ownerTask(question.id || 'question:' + index, question.question, question.owner));
    });
    pending.forEach((item, index) => {
      if (item && item.status !== 'supported') tasks.push(ownerTask(item.field_id || item.id || 'pending:' + index,
        item.question || 'Confirm this unresolved detail before dispatch.', item.owner));
    });
    const supported = new Set(facts.map(fact => fact.id));
    const storageSafe = fields.required_storage == null || fields.required_storage === '' || supported.has('required_storage');
    const quantitySafe = fields.pallet_count == null || supported.has('pallet_count');
    const deadlineSafe = fields.deadline_at == null || (proposed !== null && supported.has('deadline_at'));
    const applySafe = storageSafe && quantitySafe && deadlineSafe;
    return {valid: true, applySafe, facts, tasks, fields: clone(fields),
      error: applySafe ? '' : 'A proposed field lacks supported evidence. Extract again or enter the details directly; no unreviewed field will be applied.'};
  }

  function comparisonModel(payload, sources, now = Date.now()) {
    payload = payload || {};
    const rows = payload.winner ? [payload.winner].concat(list(payload.runners_up)) : [];
    return rows.map((row, index) => {
      const id = idOf(row);
      const states = list(sources);
      const raw = states.find(source => source.source_id === 'recipient:' + id) ||
        states.find(source => source.source_kind === 'match_result') ||
        states.find(source => source.source_kind === 'city_recipients') || {};
      const source = sourceModel(raw, now);
      const provenance = ['generated', 'planted'].includes(row.profile_source) ? row.profile_source : 'unknown';
      const capacity = presentNumber(row.max_pallets);
      return {
        id, position: index, role: index === 0 ? 'Proposed match' : 'Alternative ' + index,
        name: str(row.name) || 'Unnamed recipient',
        address: [row.street, row.city, row.zip_code].filter(value => value !== null && value !== undefined && value !== '').map(String).join(', ') || 'Address not provided',
        decision: ['eligible', 'needs_confirmation', 'rejected', 'not_evaluated'].includes(row.constraint_status) ? row.constraint_status : 'not_evaluated',
        provenance, profileLabel: 'Profile source: ' + provenance,
        profileCapacity: 'Stored profile capacity: ' + (capacity === null || capacity < 0 ? 'Unknown' : capacity + ' pallets'),
        availableCapacity: 'Available capacity: Unconfirmed',
        storage: 'Stored profile storage: ' + (str(row.cold_storage) || 'Unknown'),
        pickupWindow: 'Stored profile pickup window: ' + (presentNumber(row.pickup_window_hours) === null ? 'Unknown' : row.pickup_window_hours + ' hours'),
        source, warnings: list(row.warnings).filter(value => typeof value === 'string'),
        confirmations: list(row.confirmations_required).filter(value => typeof value === 'string')
      };
    });
  }

  function handoffModel(snapshot, receipt, now = Date.now()) {
    const offer = snapshot.offer || {}, payload = snapshot.result || {};
    const candidates = comparisonModel(payload, snapshot.sources, now);
    const candidate = candidates[0];
    let reason = '';
    if (snapshot.locked) reason = 'Wait for the current match check to finish.';
    else if (!['success', 'draft'].includes(payload.status) || !candidate) reason = 'A current proposed match is required.';
    else if (!candidate.id || candidate.decision !== 'eligible') reason = 'This recipient is not eligible for the current handoff.';
    const deadline = time(offer.deadline_at);
    const resultDeadline = time(payload.donor && payload.donor.deadline_at || payload.deadline_at);
    if (!reason && (deadline === null || resultDeadline === null || deadline !== resultDeadline)) reason = 'Run the match check for the current fixed pickup deadline.';
    const donor = payload.donor || {};
    const sameText = (left, right) => typeof left === 'string' && typeof right === 'string' && left.trim() === right.trim();
    const sameNumber = (left, right) => presentNumber(left) !== null && presentNumber(left) === presentNumber(right);
    const sameOffer = sameText(donor.name, offer.donor_name) &&
      sameText(donor.required_storage, offer.required_storage) &&
      sameNumber(donor.pallet_count, offer.pallet_count) &&
      sameNumber(donor.latitude, offer.donor_lat) && sameNumber(donor.longitude, offer.donor_lon) &&
      (donor.posting_text === undefined || sameText(donor.posting_text, offer.posting_text));
    if (!reason && !sameOffer) reason = 'The result belongs to a different offer. Run the current offer again.';
    if (!reason && payload.winner.deadline_at !== undefined && time(payload.winner.deadline_at) !== deadline) reason = 'The recipient evidence uses a different pickup deadline. Run the current offer again.';
    if (!reason && deadline <= now) reason = 'The pickup deadline has passed. Review the offer before continuing.';
    const tasks = [];
    const seen = new Set();
    function add(task) {
      if (!task.question) return;
      const key = task.owner + ':' + task.question;
      if (!seen.has(key)) { seen.add(key); tasks.push(task); }
    }
    if (receipt) list(receipt.tasks).forEach(add);
    list(payload.confirmations_required).forEach((question, index) => {
      if (typeof question === 'string') add(ownerTask('result:' + index, question, 'coordinator'));
    });
    if (candidate) candidate.confirmations.forEach((question, index) => add(ownerTask('recipient:' + index, question, 'recipient')));
    add(ownerTask('recipient_acceptance', 'Confirm acceptance, available capacity, storage and receiving hours with the recipient.', 'recipient'));
    add(ownerTask('pickup_eta', 'Confirm pickup origin, contact and arrival before the fixed deadline.', 'coordinator'));
    add(ownerTask('source_recheck', 'Recheck source details; retrieval does not confirm current operations.', 'coordinator'));
    return {
      allowed: !reason, reason, offer: clone(offer), candidate, candidates,
      deadline: deadline === null ? null : offer.deadline_at,
      tasks, reviewedFieldIds: receipt ? list(receipt.reviewedFieldIds).slice() : [],
      justification: str(payload.match_brief && payload.match_brief.match_justification),
      sms: str(payload.match_brief && payload.match_brief.drafted_sms_outreach),
      email: str(payload.match_brief && payload.match_brief.drafted_email_outreach),
      generationMode: str(payload.generation_mode) || 'Unknown',
      resultStatus: str(payload.status)
    };
  }

  function exportText(model, reviewedFields, reviewed = false) {
    if (!model.allowed || !model.candidate) return '';
    const c = model.candidate, offer = model.offer;
    const lines = [
      'UNSENT HANDOFF DRAFT',
      'Nothing has been sent, accepted or reserved.',
      '',
      'Offer: ' + (str(offer.posting_text) || 'Not specified'),
      'Donor: ' + (str(offer.donor_name) || 'Not specified'),
      'Quantity: ' + (presentNumber(offer.pallet_count) === null ? 'Not specified' : offer.pallet_count + ' pallets'),
      'Storage: ' + (str(offer.required_storage) || 'Not specified'),
      'Pickup origin: ' + (presentNumber(offer.donor_lat) === null || presentNumber(offer.donor_lon) === null ? 'Not specified' : offer.donor_lat + ', ' + offer.donor_lon),
      'Fixed pickup deadline: ' + model.deadline,
      '',
      'Proposed recipient: ' + c.name + ' (ID ' + c.id + ')',
      'Address: ' + c.address,
      'Match decision: ' + c.decision + ' · result ' + model.resultStatus,
      c.profileLabel, c.profileCapacity, c.availableCapacity, c.storage,
      'Evidence source: ' + c.source.id, c.source.retrieval, c.source.freshness,
      c.source.observation, c.source.availability,
      '',
      reviewed ? 'Current offer, result, source labels and pending tasks reviewed by the operator.' : 'Current handoff review is still pending.',
      'Reviewed field IDs (acknowledged, not independently verified): ' + list(reviewedFields).join(', '),
      '',
      'Pending owner tasks:'
    ];
    model.tasks.forEach(task => lines.push('- ' + task.owner + ': ' + task.question));
    if (model.justification) lines.push('', 'Match rationale: ' + model.justification);
    if (model.candidates.length > 1) {
      lines.push('', 'Alternatives in server order:');
      model.candidates.slice(1).forEach(row => lines.push('- ' + row.name + ' (ID ' + row.id + ') · ' + row.decision + ' · ' + row.profileLabel + ' · ' + row.source.freshness + ' · ' + row.source.availability));
    }
    if (model.sms) lines.push('', 'Unsent SMS draft:', model.sms);
    if (model.email) lines.push('', 'Unsent email draft:', model.email);
    lines.push('', 'Draft generation: ' + model.generationMode);
    return lines.join('\n');
  }

  function create(options) {
    if (!options || typeof options.snapshot !== 'function') throw new TypeError('snapshot getter required');
    const getSnapshot = options.snapshot, containers = options.containers || {};
    const now = options.now || Date.now;
    const clipboard = options.clipboard || (root.navigator && root.navigator.clipboard);
    let generation = 0, pending = null, ticket = null, acknowledgements = new Set();
    let receipt = null, resultReview = null, copying = false, feedback = '', lastRenderKey = null, lastNoticeKey = null;
    const get = () => getSnapshot() || {};
    function offerToken(snapshot) {
      return stableToken({offer: snapshot.offer || {}, revision: snapshot.revision === undefined ? null : snapshot.revision});
    }
    function token(snapshot) {
      const owned = Object.assign({}, snapshot);
      delete owned.locked;
      // Crossing a retrieval expiry invalidates a result review even without a network update.
      owned.effective_sources = list(snapshot.sources).map(source => sourceModel(source, now()));
      return stableToken(owned);
    }
    function activeReceipt(snapshot) {
      return receipt && receipt.offerToken === offerToken(snapshot) ? receipt : null;
    }
    function reconcile(snapshot) {
      const owned = token(snapshot);
      if (pending && pending.token !== owned) { pending = null; acknowledgements.clear(); feedback = 'The offer or evidence changed. Extract and review the details again.'; }
      if (receipt && receipt.offerToken !== offerToken(snapshot)) receipt = null;
      if (resultReview && (resultReview.token !== owned || !handoffModel(snapshot, activeReceipt(snapshot), now()).allowed)) {
        resultReview = null;
        feedback = 'The offer or result changed. Review the current handoff again.';
      }
    }
    function canApply() {
      const snapshot = get();
      reconcile(snapshot);
      if (!pending || snapshot.locked || pending.token !== token(snapshot)) return false;
      const model = pending.model;
      const deadline = time(model.fields.deadline_at);
      return model.applySafe && model.facts.every(fact => acknowledgements.has(fact.id)) &&
        (deadline === null || deadline > now()) &&
        (time(snapshot.offer && snapshot.offer.deadline_at) === null || time(snapshot.offer.deadline_at) > now());
    }
    function beginIntake() {
      generation++;
      pending = null; acknowledgements.clear(); feedback = '';
      ticket = {generation, token: token(get())};
      render();
      return ticket;
    }
    function receiveIntake(draft, ownedTicket) {
      const snapshot = get();
      if (!ownedTicket || ownedTicket !== ticket || ownedTicket.generation !== generation || ownedTicket.token !== token(snapshot) || snapshot.locked) return false;
      const model = intakeModel(draft, snapshot.offer);
      if (!model.valid) return false;
      pending = {model, token: ownedTicket.token};
      acknowledgements.clear();
      feedback = '';
      render();
      return true;
    }
    function clearIntake(reason) {
      if (reason !== 'loading') { generation++; ticket = null; }
      pending = null; acknowledgements.clear();
      render();
    }
    function invalidate(reason) {
      generation++; ticket = null; pending = null; receipt = null; resultReview = null; acknowledgements.clear();
      feedback = reason || 'The offer changed. Review its current details before copying.';
      render();
    }
    function acknowledgeFact(id, checked) {
      reconcile(get());
      if (!pending || !pending.model.facts.some(fact => fact.id === id) || get().locked) return false;
      if (checked) acknowledgements.add(id); else acknowledgements.delete(id);
      render();
      return true;
    }
    function applyReviewed(apply) {
      if (typeof apply !== 'function' || !canApply()) return false;
      const before = clone(get());
      const model = clone(pending.model);
      const reviewedFieldIds = model.facts.filter(fact => acknowledgements.has(fact.id)).map(fact => fact.id);
      let applied;
      try { applied = apply(); } catch (_) { invalidate('The reviewed details could not be applied. Check the form and review again.'); return false; }
      if (applied !== true) { invalidate('The reviewed details were not applied. Check the form and review again.'); return false; }
      const after = get(), fields = model.fields, form = after.offer || {}, old = before.offer || {};
      const expectedDeadline = [old.deadline_at, fields.deadline_at].filter(value => time(value) !== null).sort((a, b) => time(a) - time(b))[0] || null;
      const sameUntouched = ['posting_text', 'donor_name', 'donor_lat', 'donor_lon'].every(key => stableToken(form[key] === undefined ? null : form[key]) === stableToken(old[key] === undefined ? null : old[key]));
      const expectedStorage = fields.required_storage || '';
      const expectedQuantity = fields.pallet_count === undefined ? null : fields.pallet_count;
      const properDeadline = expectedDeadline === null ? time(form.deadline_at) === null : time(form.deadline_at) === time(expectedDeadline) && time(form.deadline_at) > now();
      const hours = presentNumber(form.hours_remaining);
      const properHours = expectedDeadline === null ? hours === null : hours !== null && hours > 0 && Math.abs(hours - (time(expectedDeadline) - now()) / 3600000) < 1 / 60;
      if (!sameUntouched || form.required_storage !== expectedStorage || presentNumber(form.pallet_count) !== expectedQuantity || !properDeadline || !properHours || after.locked) {
        invalidate('The applied form does not match the reviewed details. Check it and review again.');
        return false;
      }
      // Host offerChanged() may synchronously invalidate during apply; only now bind the receipt to the new form.
      receipt = {offerToken: offerToken(after), reviewedFieldIds, tasks: model.tasks};
      pending = null; ticket = null; acknowledgements.clear(); resultReview = null; generation++;
      feedback = 'Reviewed donor statements applied. Pending tasks still need their owners.';
      render();
      return true;
    }
    function reviewResult(checked) {
      const snapshot = get();
      reconcile(snapshot);
      const model = handoffModel(snapshot, activeReceipt(snapshot), now());
      if (!checked) { resultReview = null; feedback = ''; render(); return true; }
      if (!model.allowed || copying) { resultReview = null; render(); return false; }
      const fields = OFFER_FIELDS.map(id => 'offer.' + id).concat(model.reviewedFieldIds.map(id => 'donor.' + id), ['result.recipient_id', 'result.sources', 'result.pending_tasks']);
      resultReview = {token: token(snapshot), generation, candidateId: model.candidate.id, fields};
      feedback = 'Current handoff reviewed. It remains unsent.';
      render();
      return true;
    }
    async function copyHandoff() {
      const snapshot = get();
      reconcile(snapshot);
      const model = handoffModel(snapshot, activeReceipt(snapshot), now());
      if (copying) return {ok: false, reason: 'copy_in_progress'};
      if (!model.allowed || !resultReview || resultReview.token !== token(snapshot)) {
        feedback = model.reason || 'Review the current handoff before copying.';
        render(); return {ok: false, reason: 'review_required'};
      }
      if (!clipboard || typeof clipboard.writeText !== 'function') {
        feedback = 'Clipboard is unavailable. The unsent draft remains visible for manual copying.';
        render(); return {ok: false, reason: 'clipboard_unavailable'};
      }
      const owned = resultReview, ownedGeneration = generation, frozen = exportText(model, owned.fields, true);
      copying = true; feedback = 'Copying the reviewed, unsent draft…'; render();
      try {
        await clipboard.writeText(frozen);
        const current = get();
        const stillCurrent = generation === ownedGeneration && resultReview === owned && token(current) === owned.token &&
          handoffModel(current, activeReceipt(current), now()).allowed && !current.locked;
        copying = false;
        if (!stillCurrent) {
          resultReview = null;
          feedback = 'The offer, evidence or deadline changed while copying. Your clipboard may contain the previous draft; review and copy the current handoff again. Nothing was sent.';
          render();
          return {ok: false, reason: 'stale_after_copy', clipboardMayContainPrevious: true};
        }
        feedback = 'Reviewed handoff copied. Nothing was sent, accepted or reserved.';
        render(); return {ok: true, text: frozen};
      } catch (_) {
        copying = false;
        feedback = 'Copy did not complete. Review the current draft before trying again.';
        render(); return {ok: false, reason: 'clipboard_failed'};
      }
    }
    function refresh() { reconcile(get()); render(); }
    function state() {
      const snapshot = get();
      reconcile(snapshot);
      const model = handoffModel(snapshot, activeReceipt(snapshot), now());
      return {pending: !!pending, canApply: canApply(), acknowledgedFieldIds: Array.from(acknowledgements),
        appliedFieldIds: activeReceipt(snapshot) ? receipt.reviewedFieldIds.slice() : [], reviewed: !!resultReview,
        canCopy: model.allowed && !!resultReview && !copying, copying, feedback, model};
    }
    function render() {
      const snapshot = get();
      reconcile(snapshot);
      const renderKey = stableToken({token: token(snapshot), locked: !!snapshot.locked, generation, pending: !!pending,
        acknowledgements: Array.from(acknowledgements), receipt: activeReceipt(snapshot), reviewed: !!resultReview,
        copying, feedback, reason: handoffModel(snapshot, activeReceipt(snapshot), now()).reason, canApply: canApply()});
      if (renderKey === lastRenderKey) return;
      lastRenderKey = renderKey;
      const document = root.document || Object.values(containers).find(container => container && container.ownerDocument)?.ownerDocument;
      const focused = document && document.activeElement;
      const focusKey = focused && focused.getAttribute && focused.getAttribute('data-fe-review-focus');
      const hadFocus = focused && Object.values(containers).some(container => container && container.contains(focused));
      function el(tag, text, cls) {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (cls) node.className = cls;
        return node;
      }
      function heading(container, title, intro) {
        container.appendChild(el('h3', title, 'fe-review-title'));
        if (intro) container.appendChild(el('p', intro, 'fe-review-intro'));
      }
      function taskList(container, tasks) {
        if (!tasks.length) return;
        const ul = el('ul', undefined, 'fe-review-tasks');
        tasks.forEach(task => {
          const li = el('li');
          li.appendChild(el('span', task.owner, 'fe-review-owner'));
          li.appendChild(el('span', task.question));
          ul.appendChild(li);
        });
        container.appendChild(ul);
      }
      if (document && containers.intake) {
        const container = containers.intake; container.replaceChildren(); container.classList.add('fe-review');
        heading(container, 'Review donor details', 'Acknowledge the quoted statements. Questions remain assigned to an owner.');
        if (!pending) container.appendChild(el('p', activeReceipt(snapshot) ? 'Reviewed details are applied to this offer. Pending confirmations remain in the handoff.' : 'Extract the offer details to review the donor’s statements.', 'fe-review-empty'));
        else {
          const cards = el('div', undefined, 'fe-review-facts');
          pending.model.facts.forEach(fact => {
            const article = el('article', undefined, 'fe-review-fact');
            article.appendChild(el('span', fact.label, 'fe-review-label'));
            article.appendChild(el('p', fact.value, 'fe-review-value'));
            if (fact.quote) article.appendChild(el('blockquote', fact.quote, 'fe-review-quote'));
            article.appendChild(el('p', fact.source === 'existing_clock' ? 'Source: existing fixed pickup clock' : 'Source: donor statement', 'fe-review-meta'));
            const label = el('label', undefined, 'fe-review-check');
            const input = el('input'); input.type = 'checkbox'; input.checked = acknowledgements.has(fact.id); input.disabled = !!snapshot.locked;
            input.setAttribute('data-fe-review-focus', 'fact:' + fact.id);
            input.addEventListener('change', () => acknowledgeFact(fact.id, input.checked));
            label.appendChild(input); label.appendChild(el('span', 'I reviewed this ' + (fact.source === 'existing_clock' ? 'fixed deadline' : 'donor statement')));
            article.appendChild(label); cards.appendChild(article);
          });
          container.appendChild(cards);
          if (pending.model.error) container.appendChild(el('p', pending.model.error, 'fe-review-warning'));
          if (pending.model.tasks.length) { container.appendChild(el('h4', 'Still to confirm', 'fe-review-subtitle')); taskList(container, pending.model.tasks); }
        }
      }
      if (document && containers.comparison) {
        const container = containers.comparison; container.replaceChildren(); container.classList.add('fe-review');
        heading(container, 'Compare recipient evidence', 'The server’s proposed match and alternatives, in their returned order.');
        const candidates = comparisonModel(snapshot.result, snapshot.sources, now());
        if (!candidates.length) container.appendChild(el('p', 'Run a match check to compare recipient evidence.', 'fe-review-empty'));
        const grid = el('div', undefined, 'fe-review-comparison');
        candidates.forEach(candidate => {
          const card = el('article', undefined, 'fe-review-candidate');
          card.appendChild(el('span', candidate.role, 'fe-review-label'));
          card.appendChild(el('h4', candidate.name, 'fe-review-subtitle'));
          card.appendChild(el('p', candidate.address, 'fe-review-meta'));
          card.appendChild(el('span', candidate.decision.replace(/_/g, ' '), 'fe-review-decision'));
          [candidate.profileLabel, candidate.profileCapacity, candidate.availableCapacity, candidate.storage, candidate.pickupWindow,
            'Evidence source: ' + candidate.source.id, candidate.source.retrieval, candidate.source.freshness,
            candidate.source.observation, candidate.source.availability].forEach(text => card.appendChild(el('p', text, 'fe-review-evidence-line')));
          candidate.warnings.forEach(text => card.appendChild(el('p', text, 'fe-review-warning')));
          grid.appendChild(card);
        });
        container.appendChild(grid);
      }
      if (document && containers.handoff) {
        const container = containers.handoff; container.replaceChildren(); container.classList.add('fe-review');
        const model = handoffModel(snapshot, activeReceipt(snapshot), now());
        heading(container, 'Review the unsent handoff', 'Review this offer, the proposed recipient, source labels and pending tasks together.');
        if (model.candidate) {
          const summary = el('div', undefined, 'fe-review-handoff-summary');
          summary.appendChild(el('h4', model.candidate.name, 'fe-review-subtitle'));
          summary.appendChild(el('p', model.offer.posting_text || 'Offer not specified', 'fe-review-offer'));
          summary.appendChild(el('p', 'Fixed pickup deadline: ' + (model.deadline || 'Not specified'), 'fe-review-deadline'));
          summary.appendChild(el('p', model.candidate.profileLabel + ' · ' + model.candidate.source.freshness, 'fe-review-meta'));
          summary.appendChild(el('p', model.candidate.availableCapacity + ' · ' + model.candidate.source.availability, 'fe-review-warning'));
          container.appendChild(summary);
          taskList(container, model.tasks);
        }
        if (!model.allowed) container.appendChild(el('p', model.reason, 'fe-review-warning'));
        else {
          const label = el('label', undefined, 'fe-review-check fe-review-result-check');
          const input = el('input'); input.type = 'checkbox'; input.checked = !!resultReview; input.disabled = copying;
          input.setAttribute('data-fe-review-focus', 'result-review');
          input.addEventListener('change', () => reviewResult(input.checked));
          label.appendChild(input); label.appendChild(el('span', 'I reviewed this current offer, proposed recipient, source labels and pending tasks. Nothing is confirmed or sent by this review.'));
          container.appendChild(label);
          const preview = el('details', undefined, 'fe-review-export');
          preview.appendChild(el('summary', 'Read the complete unsent draft'));
          preview.appendChild(el('pre', exportText(model, resultReview ? resultReview.fields : model.reviewedFieldIds.map(id => 'donor.' + id), !!resultReview), 'fe-review-draft'));
          container.appendChild(preview);
          const button = el('button', copying ? 'Copying…' : 'Copy unsent handoff', 'fe-review-copy');
          button.type = 'button'; button.disabled = !resultReview || copying;
          button.setAttribute('data-fe-review-focus', 'copy-handoff');
          button.addEventListener('click', () => { void copyHandoff(); }); container.appendChild(button);
        }
        const status = el('p', feedback || 'Nothing has been sent, accepted or reserved.', 'fe-review-feedback');
        status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); container.appendChild(status);
      }
      if (hadFocus && focusKey && document) {
        const targets = Object.values(containers).flatMap(container => container ? Array.from(container.querySelectorAll('[data-fe-review-focus]')) : []);
        const replacement = targets.find(node => node.getAttribute('data-fe-review-focus') === focusKey && !node.disabled);
        if (replacement) replacement.focus();
      }
      const notice = {pending: !!pending, canApply: canApply(), reviewed: !!resultReview, copying,
        canCopy: !!resultReview && !copying && handoffModel(snapshot, activeReceipt(snapshot), now()).allowed, feedback};
      const noticeKey = stableToken(notice);
      if (noticeKey !== lastNoticeKey) {
        lastNoticeKey = noticeKey;
        if (typeof options.onChange === 'function') options.onChange(notice);
      }
    }
    render();
    return {beginIntake, receiveIntake, clearIntake, acknowledgeFact, canApply, applyReviewed, invalidate, refresh, reviewResult, copyHandoff, state};
  }

  const api = {create, stableToken, intakeModel, comparisonModel, sourceModel, handoffModel, exportText};
  root.FoodEquityReview = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
