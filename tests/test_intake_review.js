'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const UI = require('../agent/ui/intake-review.js');

const START = Date.parse('2026-10-20T12:00:00Z');
const DEADLINE = '2026-10-20T18:00:00Z';
const copy = value => JSON.parse(JSON.stringify(value));
function fixture() {
  const offer = {posting_text:'We have 2 pallets of spinach, keep cold, collect within 6 hours.', donor_name:'Market',
    pallet_count:2, required_storage:'cold', hours_remaining:6, donor_lat:47.6, donor_lon:-122.3, deadline_at:DEADLINE};
  const winner = {recipient_id:'r1', name:'Harbor Pantry', constraint_status:'eligible', max_pallets:12, cold_storage:'commercial',
    pickup_window_hours:2, street:'1 Harbor Way', city:'Seattle', zip_code:'98101', profile_source:'generated', deadline_at:DEADLINE,
    confirmations_required:['Confirm today’s receiving hours.']};
  return {offer, locked:false, revision:1, result:{status:'draft', generation_mode:'deterministic_fallback', donor:{
    name:offer.donor_name, latitude:offer.donor_lat, longitude:offer.donor_lon, posting_text:offer.posting_text,
    pallet_count:offer.pallet_count, required_storage:offer.required_storage, hours_remaining:5.9, deadline_at:DEADLINE
  }, winner, runners_up:[{...winner, recipient_id:'r2', name:'Community Kitchen', max_pallets:0, profile_source:'planted'}],
  match_brief:{match_justification:'Server rationale.', drafted_sms_outreach:'Can you receive this offer?'}},
  sources:[{source_id:'match:current', source_kind:'match_result', status:'ready', fetched_at:'2026-10-20T12:00:00Z', expires_at:DEADLINE, provenance:'generated'}]};
}
function draft(snapshot) {
  return {status:'review_required',requires_review:true,fields:{posting_text:snapshot.offer.posting_text,
    required_storage:'cold',pallet_count:2,deadline_at:DEADLINE,hours_remaining:6},
  quoted_evidence:{required_storage:'keep cold',pallet_count:'2 pallets',pickup_deadline:'within 6 hours'},
  food_terms:['spinach'], questions:['Confirm pickup origin and recipient availability.']};
}
function harness(extra) {
  let snapshot = fixture(), clock = START, written = [];
  const controller = UI.create({snapshot:()=>snapshot, now:()=>clock, clipboard:{writeText:async text=>written.push(text)}, ...extra});
  return {controller, get snapshot(){return snapshot;}, set snapshot(value){snapshot=value;},
    get clock(){return clock;}, set clock(value){clock=value;}, written};
}
function load(h, value=draft(h.snapshot)) {
  const ticket=h.controller.beginIntake();
  assert.equal(h.controller.receiveIntake(value,ticket),true);
  return value;
}
function ackAll(h, value=draft(h.snapshot)) {
  const model=UI.intakeModel(value,h.snapshot.offer);
  model.facts.forEach(fact=>assert.equal(h.controller.acknowledgeFact(fact.id,true),true));
}
function applyHost(h, value) {
  return h.controller.applyReviewed(()=>{
    h.controller.invalidate('Offer changed');
    const old=h.snapshot.offer;
    const deadlines=[old.deadline_at,value.fields.deadline_at].filter(Boolean).sort((a,b)=>Date.parse(a)-Date.parse(b));
    const deadline=deadlines[0]||null;
    h.snapshot={...h.snapshot,revision:h.snapshot.revision+1,result:null,offer:{...old,
      required_storage:value.fields.required_storage||'',pallet_count:value.fields.pallet_count??'',
      deadline_at:deadline,hours_remaining:deadline?(Date.parse(deadline)-h.clock)/3600000:''}};
    return true;
  });
}

test('literal donor cards distinguish supported facts and unresolved owner tasks',()=>{
  const s=fixture(), d=draft(s);
  d.pending_facts=[{field_id:'temperature',status:'unknown',owner:'recipient',question:'Confirm temperature on arrival.'}];
  const model=UI.intakeModel(d,s.offer);
  assert.equal(model.valid,true); assert.equal(model.applySafe,true);
  assert.deepEqual(model.facts.map(f=>f.id),['food_terms:0','required_storage','pallet_count','deadline_at']);
  assert.equal(model.facts.find(f=>f.id==='deadline_at').source,'existing_clock');
  assert(model.tasks.some(t=>t.owner==='recipient'&&t.id==='temperature'));
  assert(!model.facts.some(f=>f.id==='temperature'));
});
test('unsupported proposed values cannot slip through acknowledgement of other facts',()=>{
  for (const mutate of [
    d=>delete d.quoted_evidence.required_storage,
    d=>d.pending_facts=[{field_id:'pallet_count',status:'unknown',question:'Confirm count'}],
    d=>d.fields.pallet_count=-1,
  ]) {
    const h=harness(), d=draft(h.snapshot); mutate(d); load(h,d); ackAll(h,d);
    assert.equal(h.controller.canApply(),false);
    let called=false; assert.equal(h.controller.applyReviewed(()=>{called=true;return true;}),false); assert.equal(called,false);
  }
});
test('all supported statements require individual acknowledgement; unknown IDs are not reviewable',()=>{
  const h=harness(); load(h); assert.equal(h.controller.canApply(),false);
  assert.equal(h.controller.acknowledgeFact('unknown',true),false);
  ackAll(h); assert.equal(h.controller.canApply(),true);
  h.controller.acknowledgeFact('pallet_count',false); assert.equal(h.controller.canApply(),false);
});
test('unknown-only draft clears prior assumptions without verifying pending facts',()=>{
  const h=harness(); h.snapshot.offer.deadline_at=null;
  const d={status:'review_required',requires_review:true,fields:{posting_text:h.snapshot.offer.posting_text},
    quoted_evidence:{},food_terms:[],questions:['Confirm quantity, storage and pickup window.']};
  load(h,d); assert.equal(h.controller.canApply(),true);
  assert.equal(applyHost(h,d),true);
  assert.equal(h.snapshot.offer.pallet_count,''); assert.equal(h.snapshot.offer.required_storage,'');
  assert.deepEqual(h.controller.state().appliedFieldIds,[]);
  assert(h.controller.state().model.tasks.some(t=>t.owner==='donor'&&t.id==='pallet_count'));
});
test('loading clear preserves the active extraction ticket; error clear cancels it',()=>{
  const h=harness(), ticket=h.controller.beginIntake();
  h.controller.clearIntake('loading'); assert.equal(h.controller.receiveIntake(draft(h.snapshot),ticket),true);
  const next=h.controller.beginIntake(); h.controller.clearIntake('error');
  assert.equal(h.controller.receiveIntake(draft(h.snapshot),next),false);
});
test('late extraction is rejected on full offer or evidence changes even with same posting',()=>{
  for (const mutate of [
    s=>s.offer.pallet_count=4,s=>s.offer.required_storage='ambient',s=>s.offer.donor_lat=45,
    s=>s.sources[0].fetched_at='2026-10-20T12:00:01Z',s=>s.result.winner.name='New evidence'
  ]) {
    const h=harness(), value=draft(h.snapshot), ticket=h.controller.beginIntake(); mutate(h.snapshot);
    assert.equal(h.controller.receiveIntake(value,ticket),false);
  }
});
test('explicit invalidation protects against edit-and-revert ABA',()=>{
  const h=harness(), value=draft(h.snapshot), ticket=h.controller.beginIntake();
  h.controller.invalidate(); assert.equal(h.controller.receiveIntake(value,ticket),false);
});
test('match locking does not erase donor acknowledgement',()=>{
  const h=harness(); load(h); ackAll(h);
  const ids=h.controller.state().acknowledgedFieldIds;
  h.snapshot.locked=true; h.controller.refresh(); assert.equal(h.controller.canApply(),false);
  assert.deepEqual(h.controller.state().acknowledgedFieldIds,ids);
  h.snapshot.locked=false; h.controller.refresh(); assert.equal(h.controller.canApply(),true);
});
test('apply safely crosses host invalidation before assignment and binds receipt to resulting offer',()=>{
  const h=harness(), value=load(h); ackAll(h,value);
  assert.equal(applyHost(h,value),true); assert(h.controller.state().appliedFieldIds.includes('pallet_count'));
  h.snapshot.locked=true; h.controller.refresh(); h.snapshot.locked=false; h.controller.refresh();
  assert(h.controller.state().appliedFieldIds.includes('pallet_count'));
  h.snapshot.offer.donor_lat=46; h.controller.refresh(); assert.deepEqual(h.controller.state().appliedFieldIds,[]);
});
test('apply never trusts a true return after rejected or missing mutation',()=>{
  const h=harness(); h.snapshot.offer.pallet_count=7;
  const value=load(h); ackAll(h,value);
  assert.equal(h.controller.applyReviewed(()=>true),false); assert.deepEqual(h.controller.state().appliedFieldIds,[]);
});
test('false and throwing host apply callbacks create no receipt',()=>{
  for (const fn of [()=>false,()=>{throw Error('private host error');}]) {
    const h=harness(); load(h); ackAll(h); assert.equal(h.controller.applyReviewed(fn),false);
    assert.deepEqual(h.controller.state().appliedFieldIds,[]); assert(!h.controller.state().feedback.includes('private'));
  }
});
test('apply preserves earlier deadline and accepts an equivalent ISO representation',()=>{
  const h=harness(); h.snapshot.offer.deadline_at='2026-10-20T17:00:00+00:00';
  const value=load(h); ackAll(h,value);
  assert.equal(applyHost(h,value),true); assert.equal(h.snapshot.offer.deadline_at,'2026-10-20T17:00:00+00:00');
});
test('comparison retains server order and puts retrieval and operational unknowns on every card',()=>{
  const h=harness(), rows=UI.comparisonModel(h.snapshot.result,h.snapshot.sources,h.clock);
  assert.deepEqual(rows.map(r=>r.id),['r1','r2']);
  for (const row of rows) {
    assert.match(row.source.retrieval,/Evaluation received/); assert.match(row.source.freshness,/Retrieval status: ready/);
    assert.match(row.source.observation,/Unknown/); assert.match(row.source.availability,/Unconfirmed/);
    assert.match(row.availableCapacity,/Unconfirmed/);
  }
  assert.equal(rows[1].profileCapacity,'Stored profile capacity: 0 pallets');
});
test('missing source/capacity remains unknown, never available or zero',()=>{
  const h=harness(); delete h.snapshot.result.winner.max_pallets;
  const row=UI.comparisonModel(h.snapshot.result,[],h.clock)[0];
  assert.equal(row.profileCapacity,'Stored profile capacity: Unknown');
  assert.equal(row.source.status,'unavailable'); assert.equal(row.source.fetchedAt,null);
  assert.equal(row.source.observation,'Source observation date: Unknown');
});
test('explicit rejected or unconfirmed candidate blocks handoff despite winner membership',()=>{
  for (const status of ['rejected','needs_confirmation','not_evaluated',undefined]) {
    const h=harness(); h.snapshot.result.winner.constraint_status=status;
    assert.equal(UI.handoffModel(h.snapshot,null,h.clock).allowed,false);
    assert.equal(h.controller.reviewResult(true),false);
  }
});
test('blocked/no-match and expired results cannot be reviewed',()=>{
  for (const mutate of [h=>h.snapshot.result.status='blocked',h=>h.snapshot.result.winner=null,h=>h.clock=Date.parse(DEADLINE),h=>h.snapshot.locked=true]) {
    const h=harness(); mutate(h); assert.equal(h.controller.reviewResult(true),false);
  }
});
test('same deadline cannot hide obsolete donor quantity, storage, name, origin or text',()=>{
  for (const [field,value] of [['pallet_count',3],['required_storage','frozen'],['donor_name','Other'],['donor_lat',48],['donor_lon',-121],['posting_text','Different food offer']]) {
    const h=harness(); h.snapshot.offer[field]=value;
    assert.equal(h.controller.reviewResult(true),false,field);
  }
});
test('hours drift is presentation-only, and equivalent donor timestamps remain valid',()=>{
  const h=harness(); h.snapshot.result.donor.hours_remaining=5.2;
  h.snapshot.result.donor.deadline_at='2026-10-20T18:00:00+00:00';
  assert.equal(h.controller.reviewResult(true),true);
});
test('mismatched canonical or candidate deadline blocks handoff',()=>{
  for (const mutate of [s=>s.result.donor.deadline_at='2026-10-20T19:00:00Z',s=>s.result.winner.deadline_at='2026-10-20T19:00:00Z']) {
    const h=harness(); mutate(h.snapshot); assert.equal(h.controller.reviewResult(true),false);
  }
});
test('copy requires current explicit review and exports fixed deadline, owners and source labels',async()=>{
  const h=harness(); assert.equal((await h.controller.copyHandoff()).ok,false); assert.equal(h.written.length,0);
  assert.equal(h.controller.reviewResult(true),true); assert.equal((await h.controller.copyHandoff()).ok,true);
  assert.match(h.written[0],/UNSENT HANDOFF DRAFT/); assert.match(h.written[0],/Fixed pickup deadline: 2026-10-20T18:00:00Z/);
  assert.match(h.written[0],/offer.pallet_count/); assert.match(h.written[0],/recipient: Confirm/);
  assert.match(h.written[0],/Source observation date: Unknown/); assert.match(h.written[0],/deterministic_fallback/);
});
test('preview does not claim a current result was reviewed',()=>{
  const h=harness(), text=UI.exportText(UI.handoffModel(h.snapshot,null,h.clock),[]);
  assert.match(text,/Current handoff review is still pending/);
  assert(!text.includes('reviewed by the operator'));
});
test('result replacement with same recipient ID requires a new review',()=>{
  const h=harness(); h.controller.reviewResult(true);
  h.snapshot.result.match_brief.match_justification='Updated evidence';
  h.controller.refresh(); assert.equal(h.controller.state().reviewed,false);
});
test('source expiry invalidates review without object mutation',()=>{
  const h=harness(); h.snapshot.sources[0].expires_at='2026-10-20T12:00:10Z';
  h.controller.reviewResult(true); h.clock+=10001; h.controller.refresh();
  assert.equal(h.controller.state().reviewed,false);
  assert.equal(h.controller.state().model.candidate.source.status,'stale');
});
test('clipboard completion after edit, replacement or expiry never claims success',async()=>{
  for (const mutate of [h=>{h.snapshot.offer.pallet_count=4;h.controller.invalidate();},
    h=>{h.snapshot.result.match_brief.match_justification='Replacement';h.controller.refresh();},
    h=>{h.clock=Date.parse(DEADLINE);}
  ]) {
    let resolve; const h=harness({clipboard:{writeText:()=>new Promise(done=>{resolve=done;})}});
    h.controller.reviewResult(true); const copying=h.controller.copyHandoff();
    assert.equal(h.controller.state().copying,true);
    assert.equal((await h.controller.copyHandoff()).reason,'copy_in_progress');
    mutate(h); resolve(); const result=await copying;
    assert.equal(result.ok,false); assert.equal(result.clipboardMayContainPrevious,true);
    assert.match(h.controller.state().feedback,/clipboard may contain the previous draft/);
  }
});
test('expiry between apply start and callback completion creates no receipt',()=>{
  const h=harness(), d=load(h); ackAll(h,d);
  assert.equal(h.controller.applyReviewed(()=>{h.clock=Date.parse(DEADLINE);return true;}),false);
  assert.deepEqual(h.controller.state().appliedFieldIds,[]);
});
test('unchanged refresh is stable and onChange refresh cannot recurse',()=>{
  let controller, calls=0;
  const h=harness({onChange:()=>{calls++; if(controller)controller.refresh();}});
  controller=h.controller; load(h); ackAll(h); const before=calls;
  for(let i=0;i<5;i++){h.clock+=1000;controller.refresh();}
  assert.equal(calls,before); assert.equal(controller.canApply(),true);
});
test('tokens are property-order stable and reject non-JSON or cyclic snapshots',()=>{
  assert.equal(UI.stableToken({b:2,a:1}),UI.stableToken({a:1,b:2}));
  const cycle={};cycle.self=cycle;assert.throws(()=>UI.stableToken(cycle));
  assert.throws(()=>UI.stableToken({now:new Date()}));
});
