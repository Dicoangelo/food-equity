// Offline interaction tests of the served UI and recipient explorer. No network,
// credentials, cloud clients, timers or models are used by these tests.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../agent/api.py'), 'utf8');
const script = source.split('  <!-- Leaflet & App JS -->')[1].split('<script>')[1].split('</script>')[0];
const nodes = new Map();
let activeElement;
class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = {};
    this.dataset = {};
    this.style = {};
    this.value = '';
    this.textContent = '';
    this.hidden = false;
    this.isConnected = true;
    this.listeners = {};
    this.classes = new Set();
    this.classList = {
      add: (...names) => names.forEach(name => this.classes.add(name)),
      remove: (...names) => names.forEach(name => this.classes.delete(name)),
      contains: name => this.classes.has(name),
      toggle: (name, force) => {
        const enabled = force ?? !this.classes.has(name);
        if (enabled) this.classes.add(name); else this.classes.delete(name);
        return enabled;
      },
    };
  }
  set className(value) { this.classes = new Set(value.split(/\s+/).filter(Boolean)); }
  get className() { return [...this.classes].join(' '); }
  set innerHTML(_) { throw new Error('Dynamic HTML is forbidden'); }
  get innerText() { return content(this); }
  get firstElementChild() { return this.children[0] || null; }
  appendChild(child) { child.parentElement = this; child.setConnected(this.isConnected); this.children.push(child); return child; }
  append(...children) { children.forEach(child => this.appendChild(child)); }
  prepend(...children) { children.reverse().forEach(child => { child.parentElement = this; this.children.unshift(child); }); }
  replaceChildren(...children) {
    this.children.forEach(child => child.setConnected(false));
    this.children = [];
    this.textContent = '';
    this.append(...children);
  }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === 'id') nodes.set(String(value), this);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; if (name === 'inert') this.inert = false; else delete this[name]; }
  setConnected(value) { this.isConnected = value; this.children.forEach(child => child.setConnected(value)); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  removeEventListener(name, fn) { this.listeners[name] = (this.listeners[name] || []).filter(listener => listener !== fn); }
  dispatchEvent(event) { event.target ||= this; (this.listeners[event.type] || []).forEach(fn => fn(event)); this[`on${event.type}`]?.(event); }
  click() { this.dispatchEvent({type: 'click', preventDefault() {}, stopPropagation() {}}); }
  focus() { activeElement = this; }
  scrollIntoView() {}
  getClientRects() { return this.hidden ? [] : [{}]; }
  contains(node) { return this === node || this.children.some(child => child.contains?.(node)); }
  querySelectorAll(selector) {
    const matches = node => selector.split(',').some(part => {
      part = part.trim();
      if (part === 'button') return node.tagName === 'BUTTON';
      if (part === 'a[href]') return node.tagName === 'A' && Boolean(node.href || node.attributes.href);
      if (['input', 'select', 'textarea', 'summary'].includes(part)) return node.tagName === part.toUpperCase();
      if (part.startsWith('#')) return node.id === part.slice(1) || node.attributes.id === part.slice(1);
      if (part.startsWith('.')) return node.classList.contains(part.slice(1));
      if (part.startsWith('[tabindex')) return node.tabIndex !== undefined || node.attributes.tabindex !== undefined;
      const attr = part.match(/^(\w+)?\[([\w-]+)(?:="([^"]*)")?\]$/);
      return attr ? (!attr[1] || node.tagName === attr[1].toUpperCase()) &&
        node.attributes[attr[2]] !== undefined && (attr[3] === undefined || node.attributes[attr[2]] === attr[3]) : false;
    });
    return this.children.flatMap(child => [...(matches(child) ? [child] : []), ...(child.querySelectorAll?.(selector) || [])]);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
function node(id) {
  if (!nodes.has(id)) { const value = new Element('div'); value.id = id; nodes.set(id, value); }
  return nodes.get(id);
}
function content(element) {
  return [element?.textContent || '', ...(element?.children || []).map(content)].filter(Boolean).join('\n');
}
const documentListeners = {};
const document = {
  createElement: tag => new Element(tag),
  getElementById: node,
  get activeElement() { return activeElement; },
  body: new Element('body'),
  documentElement: new Element('html'),
  addEventListener(name, fn) { (documentListeners[name] ||= []).push(fn); },
  removeEventListener() {},
  querySelectorAll(selector) { return [...this.body.querySelectorAll(selector), ...[...nodes.values()].flatMap(value => value.querySelectorAll(selector))]; },
};
let mobile = false;
const media = {get matches() { return mobile; }, addEventListener() {}, removeEventListener() {}};
const context = vm.createContext({
  document, window: {matchMedia: () => media, addEventListener() {}},
  console, Date, Number, JSON, navigator: {}, URL, URLSearchParams,
  requestAnimationFrame: fn => fn(),
  fetch() { throw new Error('Profile interactions must never make a network request'); },
});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../agent/ui/offer-workflow.js'), 'utf8'), context);
vm.runInContext(script, context);
vm.runInContext(fs.readFileSync(path.join(__dirname, '../agent/ui/recipient-explorer.js'), 'utf8'), context);
const explorer = vm.runInContext('RecipientExplorer', context);
function setPayloads(city, match) {
  context.fixtureCity = city;
  context.fixtureMatch = match;
  vm.runInContext('cityPayload = fixtureCity; currentPayload = fixtureMatch;', context);
  explorer.refresh();
}
function filters(search = '', outcome = 'all', provenance = 'all', storage = 'all') {
  node('recipient-search').value = search;
  node('outcome-filter').value = outcome;
  node('provenance-filter').value = provenance;
  node('capability-filter').value = storage;
  explorer.refresh();
}
function ids() { return Array.from(explorer.visibleRecipients(), row => String(row.recipient_id)); }
const attack = '<img src=x onerror=alert(1)>';
const city = {status: 'ready', recipients: [
  {recipient_id: '1', name: 'Shared name', city: 'Seattle', lat: 47.60, lon: -122.30, cold_storage: 'commercial', max_pallets: 5, profile_source: 'generated', geography_precision: 'zip_centroid', frozen_storage: null, loading_dock: null, refrigerated_transport: null},
  {recipient_id: '2', name: 'Shared name', city: 'Tacoma', lat: 47.25, lon: -122.44, cold_storage: 'vehicle', max_pallets: null, profile_source: 'planted', geography_precision: 'zip_centroid', frozen_storage: null},
  {recipient_id: '3', name: attack, city: 'Bellevue', lat: 47.61, lon: -122.20, cold_storage: null, max_pallets: null, profile_source: 'unknown', frozen_storage: null},
], tracts: [{tract_id: '53033000100', lat: 47.60, lon: -122.30, poverty_rate: 0.9, no_vehicle_rate: 0.8}]};
const winner = {
  recipient_id: 1, name: 'Shared name', city: 'Seattle', latitude: 47.62, longitude: -122.31,
  constraint_status: 'eligible', profile_source: 'generated', cold_storage: 'commercial',
  max_pallets: 4, frozen_storage: null, refrigerated_transport: true, loading_dock: null,
  pickup_window_hours: 1, pickup_eta_hours: null, service_days: 'Mon-Fri',
  composite_score: 0.5518, semantic_distance: 0.23, tract_poverty_pct: 25, tract_no_vehicle_pct: null,
  tract_id: '53033000100', time_margin_hours: 2.5, evaluated_at: '2026-10-20T17:00:00+00:00',
  deadline_at: '2026-10-20T20:30:00+00:00', timing_basis: 'profile_pickup_estimate',
  acceptance_check: 'no_explicit_conflict', acceptance_evidence: [], reason_codes: [],
  confirmations_required: ['Confirm receiving-site storage.'],
};
const rejected = {
  recipient_id: '2', name: 'Shared name', city: 'Tacoma', constraint_status: 'rejected', composite_score: 0.99,
  max_pallets: 1, cold_storage: 'vehicle', frozen_storage: null, profile_source: 'planted',
  reason_codes: ['capacity_exceeded', 'recipient_food_restriction'],
  elimination_reasons: ['Capacity exceeded: 3 pallets offered, 1 documented.'],
  acceptance_check: 'explicit_conflict', acceptance_evidence: ['cannot accept pork'],
};
const pending = {recipient_id: '4', name: 'Unconfirmed site', city: 'Seattle', constraint_status: 'needs_confirmation', composite_score: 0.98, profile_source: 'unknown', cold_storage: null, max_pallets: null, reason_codes: ['acceptance_confirmation_required'], confirmations_required: ['Confirm acceptance of the full lot.']};
const backup = { ...winner, recipient_id: '5', name: 'Actual backup', composite_score: 0.48, latitude: 47.63 };
const match = {status: 'success', winner, runners_up: [backup], eliminated_decoys: [rejected], pending_confirmation: [pending], evaluated_at: winner.evaluated_at,
  donor: {name: 'Test donor', latitude: 47.59, longitude: -122.32, pallet_count: 3, required_storage: 'cold', deadline_at: winner.deadline_at}};

// Assertions below exercise user-visible behavior rather than private helpers.
context.applyScenario({donor_name: 'Bakery', posting_text: '200 loaves of day-old bread', required_storage: 'ambient',
  pallet_count: 1, hours_remaining: 24, donor_lat: 47.60, donor_lon: -122.31});
const fixedRequest = context.buildRequest();
filters();
setPayloads(city, null);
assert.equal(ids().length, 3);
explorer.select('3');
let detail = content(node('recipient-detail-content'));
assert(detail.includes(attack), 'Untrusted names must remain literal text');
assert.match(detail, /unknown/i, 'Missing profile facts remain unknown');
assert.doesNotMatch(detail, /0\.0000|0\.0%|0 pallets/, 'Missing values must not become numeric zero');
assert.doesNotMatch(detail, /0\.40.*semantic|Semantic.*40%/i, 'City-only profiles have no offer-fit components');

setPayloads(city, match);
assert.equal(ids().length, 5, 'Numeric and string IDs merge; equal names remain distinct recipients');
const mergedWinner = Array.from(explorer.mergedRecipients()).find(row => row.recipient_id === '1');
assert.equal(mergedWinner.max_pallets, 4, 'Current match facts override the city snapshot');
assert.equal(mergedWinner.loading_dock, null, 'Missing facts remain null after merging');
const components = explorer.scoreComponents(mergedWinner);
assert(components);
assert(Math.abs(components.approximateTotal - 0.55175) < 1e-8, 'Displayed components follow the existing engine weights');
assert.equal(components.authoritativeTotal, 0.5518, 'Backend rounding remains authoritative');
explorer.select('1');
detail = content(node('recipient-detail-content'));
assert.match(detail, /generated/i);
assert.match(detail, /confirmation|confirm/i);
assert.match(detail, /25(?:\.0)?%/, 'Use supplied candidate demographics');
assert.doesNotMatch(detail, /90(?:\.0)?%/, 'Do not substitute nearest map tract for candidate association');
assert.match(detail, /unknown/i, 'Missing no-vehicle percentage and loading dock remain unknown');
assert.match(detail, /0\.5518|55\.18/, 'Authoritative backend score survives component rounding');
assert.match(detail, /0\.40|40%/);
assert.match(detail, /0\.35|35%/);
assert.match(detail, /0\.25|25%/);
assert.match(detail, /round|approx/i, 'Components disclose rounding');
assert.match(detail, /zero|0 contribution/i, 'Missing neighborhood contribution policy is disclosed');
assert(detail.includes(winner.deadline_at), 'Exact donor deadline remains visible');
assert(detail.includes(winner.evaluated_at), 'Evaluation timestamp remains visible');
assert.match(detail, /not.*verified|unverified/i, 'Pickup timing is never presented as verified driving');

explorer.select('2');
detail = content(node('recipient-detail-content'));
assert.match(detail, /invented|planted/i);
assert.match(detail, /capacity_exceeded/);
assert.match(detail, /cannot accept pork/);
assert.doesNotMatch(detail, /0\.9900|99\.0|99%/, 'Rejected semantic hits must not appear as ranked fit');
explorer.select('4');
detail = content(node('recipient-detail-content'));
assert.match(detail, /acceptance_confirmation_required/);
assert.match(detail, /Confirm acceptance of the full lot/);
assert.doesNotMatch(detail, /0\.9800|98\.0|98%/, 'Pending facts must not receive eligible-fit UI');
assert.equal(node('recipient-detail-content').querySelectorAll('a[href]').filter(anchor =>
  /google\.com\/maps\/dir/.test(anchor.href || anchor.getAttribute('href'))).length, 0,
'Unknown recipient coordinates must not become an invented directions destination');

filters('Tacoma');
assert.deepEqual(ids(), ['2'], 'Search includes city');
filters('', 'all', 'planted');
assert.deepEqual(ids(), ['2'], 'Source filtering is independent of fit');
filters('', 'all', 'all', 'cold');
assert.deepEqual(ids().sort(), ['1', '5'], 'Vehicle refrigeration is not receiving-site cold storage');
filters('', 'rejected');
assert.deepEqual(ids(), ['2']);
filters('', 'needs_confirmation');
assert.deepEqual(ids(), ['4']);
filters('no matching recipient');
assert.equal(ids().length, 0);
assert.match(content(node('recipient-list')), /no|clear|match/i, 'Empty filter state gives an explanation');
filters();

const comparison = content(node('recipient-compare'));
assert.deepEqual(Array.from(explorer.comparisonRows(), row => row.recipient_id), ['1', '5']);
assert.match(comparison, /Actual backup/);
assert.doesNotMatch(comparison, /Unconfirmed site|cannot accept pork/, 'Comparison includes only actual eligible options');

explorer.select('1');
const anchors = node('recipient-detail-content').querySelectorAll('a[href]');
const directions = anchors.find(anchor => /google\.com\/maps\/dir/.test(anchor.href || anchor.getAttribute('href')));
assert(directions, 'Valid supplied coordinates offer an external directions action');
const url = new URL(directions.href || directions.getAttribute('href'));
assert.equal(url.origin, 'https://www.google.com');
assert.equal(url.searchParams.get('destination'), '47.62,-122.31', 'Match coordinates take priority over the city snapshot');
assert.match(directions.rel || directions.getAttribute('rel'), /noopener/);
setPayloads(city, {...match, donor: {...match.donor, latitude: null}});
explorer.select('1');
assert.equal(node('recipient-detail-content').querySelectorAll('a[href]').length, 0,
  'An invalid evaluated donor cannot silently use a different demo origin');
setPayloads({...city, recipients: [backup, ...city.recipients]}, {
  ...match, runners_up: [{...backup, recipient_id:'6', name:'First returned backup'}, backup],
});
assert.deepEqual(Array.from(explorer.comparisonRows(), row => row.recipient_id), ['1','6','5'],
  'Backend backup order survives a different city ordering');
assert.deepEqual(ids().slice(0,3), ['1','6','5'], 'Current proposals lead the directory in backend order');
setPayloads(city, match);
assert.deepEqual(JSON.parse(JSON.stringify(context.buildRequest())), JSON.parse(JSON.stringify(fixedRequest)),
  'Selection, filtering and comparison never alter the donor offer or exact anchored deadline');

context.offerChanged();
detail = content(node('recipient-detail-content'));
assert.doesNotMatch(detail, /0\.5518|55\.18|capacity_exceeded/, 'Offer edits invalidate previous decision and fit');
assert.equal(ids().length, 3, 'Match-only candidates disappear after an offer change');
assert.doesNotMatch(content(node('recipient-compare')), /Actual backup/, 'Stale alternatives are cleared');

setPayloads(city, {...match, status: 'blocked'});
assert.equal(explorer.comparisonRows().length, 0, 'A failed broker response cannot leave ranked alternatives');
assert(explorer.mergedRecipients().every(row => row._decision === 'not_evaluated'), 'Blocked results have no evaluated candidate decisions');
setPayloads(city, {status: 'no_viable_match', eliminated_decoys: [rejected], pending_confirmation: [pending], donor: match.donor});
filters('', 'needs_confirmation');
assert.deepEqual(ids(), ['4'], 'Facts-needed profiles remain inspectable on a no-match response');
explorer.select('4');
assert.match(content(node('recipient-detail-content')), /Confirm acceptance of the full lot/);
assert.equal(explorer.comparisonRows().length, 0);

filters();
setPayloads(city, match);
const markerElement = new Element('path');
const markerHandlers = {};
let markerStyle;
const marker = {
  options: {radius: 5, weight: 2},
  on(name, listener) { markerHandlers[name] = listener; return this; },
  getElement() { return markerElement; },
  setStyle(style) { markerStyle = style; return this; },
};
explorer.markMarker(marker, winner);
assert.equal(markerElement.getAttribute('role'), 'button');
assert.equal(markerElement.getAttribute('tabindex'), '0');
markerElement.dispatchEvent({type: 'keydown', key: 'Enter', preventDefault() {}});
assert.equal(explorer.selectedId(), '1', 'Keyboard marker activation selects the same canonical profile');
assert.equal(markerElement.getAttribute('aria-pressed'), 'true');
assert(markerStyle.weight > marker.options.weight, 'Selected marker has a non-color distinction');
assert.match(markerStyle.dashArray, /\d/, 'Selected marker has a dashed outline');
explorer.close();
assert.equal(activeElement, markerElement, 'Closing the profile restores focus to its marker');
assert.equal(markerElement.getAttribute('aria-pressed'), 'false');
markerHandlers.click();
assert.equal(explorer.selectedId(), '1', 'Pointer and keyboard selection share recipient identity');
explorer.close();

const card = node('recipient-list').children.find(value => value.getAttribute('data-recipient-id') === '2');
card.click();
assert.equal(explorer.selectedId(), '2');
explorer.close();
assert.equal(activeElement.getAttribute('data-recipient-id'), '2', 'Focus returns to the rebuilt selected card');
assert.notEqual(activeElement, card, 'Focus restoration does not target the detached old card');

node('recipient-compare').firstElementChild.open = true;
const compareButton = node('recipient-compare').querySelectorAll('button[data-recipient-id]')[1];
compareButton.click();
assert.equal(explorer.selectedId(), '5');
assert.equal(node('recipient-compare').firstElementChild.open, true, 'Profile selection preserves expanded comparison');
explorer.close();
assert.equal(activeElement.getAttribute('data-recipient-id'), '5');
assert(node('recipient-compare').contains(activeElement), 'Closing a comparison profile returns to its comparison control');

// Simulate the real aside structure to exercise the phone sheet's focus loop.
const panel = node('recipient-detail');
node('recipient-close').tagName = 'BUTTON';
panel.replaceChildren(node('recipient-close'), node('recipient-detail-content'));
const pageShell = new Element('main');
pageShell.className = 'page-shell';
const sidebar = new Element('aside');
sidebar.className = 'workspace-sidebar';
sidebar.inert = true;
sidebar.setAttribute('inert', '');
document.body.append(pageShell, sidebar);
mobile = true;
explorer.select('1', node('recipient-list').children[0]);
assert.equal(panel.getAttribute('aria-modal'), 'true');
assert.equal(activeElement, node('recipient-close'));
assert.equal(pageShell.inert, true, 'The phone sheet makes background content inert');
function keydown(key, shiftKey = false) {
  let prevented = false;
  const event = {key, shiftKey, preventDefault() { prevented = true; }};
  (documentListeners.keydown || []).forEach(listener => listener(event));
  return prevented;
}
assert(keydown('Tab', true), 'Backward tab at first control stays in the mobile sheet');
assert.equal(activeElement.tagName, 'A', 'Backward tab wraps to the directions control');
assert(keydown('Tab'), 'Forward tab at final control stays in the mobile sheet');
assert.equal(activeElement, node('recipient-close'));
assert(keydown('Escape'), 'Escape closes the mobile sheet');
assert.equal(explorer.selectedId(), null);
assert.equal(panel.getAttribute('aria-modal'), 'false');
assert.equal(document.body.classList.contains('recipient-sheet-open'), false);
assert.equal(activeElement.getAttribute('data-recipient-id'), '1');
assert.equal(pageShell.inert, false, 'Closing the sheet restores background interaction');
assert.equal(sidebar.inert, true, 'Pre-existing inert state is preserved');

// A desktop drawer stays open while the operator changes the offer elsewhere.
// Its directions must use the completed mutation, not the previous origin.
mobile = false;
filters();
setPayloads(city, match);
explorer.select('1');
function drawerDirections() {
  const link = node('recipient-detail-content').querySelectorAll('a[href]')
    .find(anchor => /google\.com\/maps\/dir/.test(anchor.href));
  assert(link, 'The selected profile has valid approximate directions');
  return new URL(link.href);
}
const changedScenario = {...fixedRequest, donor_name:'South Seattle pickup',
  donor_lat:47.53, donor_lon:-122.32, hours_remaining:4};
context.applyScenario(changedScenario);
assert.equal(explorer.selectedId(), '1', 'The selected city profile remains open');
assert.equal(drawerDirections().searchParams.get('origin'), '47.53,-122.32',
  'Switching a preset refreshes an open drawer after the origin changes');
assert.match(content(node('recipient-detail-content')), /Not evaluated for this offer/,
  'Changing the origin clears previous match evidence');

const reviewedDeadline = new Date(Date.now()+3*3600000).toISOString().replace('Z','+00:00');
context.renderVoiceDraft({type:'draft',draft_id:'reviewed-origin',draft_version:1,
  ready_for_review:true,missing_fields:[],fields:{
    ...changedScenario,donor_name:'Reviewed pickup',donor_lat:47.61,donor_lon:-122.25,
    hours_remaining:3,deadline_at:reviewedDeadline,
  }});
context.applyVoiceDraft();
assert.equal(explorer.selectedId(), '1', 'Applying a draft preserves the selected city profile');
assert.equal(drawerDirections().searchParams.get('origin'), '47.61,-122.25',
  'Applying a voice draft refreshes directions after the reviewed origin changes');
assert.equal(drawerDirections().searchParams.get('destination'), '47.6,-122.3');
assert.equal(context.buildRequest().deadline_at, reviewedDeadline,
  'Refreshing the drawer must retain the exact reviewed voice deadline');
explorer.close();
console.log('Recipient explorer: identity merge, provenance, unknowns, eligible fit, filters, exact clock, safe directions, stale clearing and keyboard/mobile focus passed.');
