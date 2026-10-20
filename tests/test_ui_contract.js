// Offline DOM simulation of the actual served script; no network or browser automation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../agent/api.py'), 'utf8');
const script = source.split('  <!-- Leaflet & App JS -->')[1].split('<script>')[1].split('</script>')[0];
const markup = source.split('<body id="rescue-app">')[1].split('  <!-- Leaflet & App JS -->')[0];
const markupIds = [...markup.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
assert.equal(new Set(markupIds).size,markupIds.length,'Each interactive target needs a unique HTML id');
for (const [,target] of markup.matchAll(/\bhref="#([^"]+)"/g)) {
  assert(markupIds.includes(target),`Workspace navigation must reach an existing section: ${target}`);
}
assert.match(markup,/<aside[^>]*aria-label="Coordinator workspace"/);
assert.match(markup,/<a href="#offer-panel" class="skip-link">/);
class Element {
  constructor(tag) {
    this.tagName=tag; this.children=[]; this.value=''; this.textContent=''; this.className='';
    this.classes=new Set(); this.attributes={};
    this.classList={
      add:(name)=>this.classes.add(name), remove:(name)=>this.classes.delete(name),
      toggle:(name,force)=>{ const enabled=force ?? !this.classes.has(name); if(enabled)this.classes.add(name);else this.classes.delete(name);return enabled; },
      contains:(name)=>this.classes.has(name),
    };
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children=children; }
  set innerHTML(_) { throw new Error('Dynamic innerHTML is forbidden'); }
  get innerText() { return this.textContent; }
  setAttribute(name,value) { this.attributes[name]=String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this[name]; delete this.attributes[name]; }
}
const nodes = new Map();
let focusedId=null;
Element.prototype.focus=function(){focusedId=this.fixtureId;};
Element.prototype.scrollIntoView=function(){};
function content(element) { return [element?.textContent||'',...(element?.children||[]).map(content)].filter(Boolean).join(' '); }
const context = vm.createContext({
  document: {createElement: tag => new Element(tag), getElementById: id => {if(!nodes.has(id)) {const element=new Element('div');element.fixtureId=id;nodes.set(id,element);} return nodes.get(id);}},
  window: {}, console, Date, Number, JSON, navigator: {}, TextDecoder, AbortController, setTimeout, clearTimeout,
});
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../agent/ui/offer-workflow.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(require('node:path').join(__dirname, '../agent/ui/alert-intake.js'), 'utf8'), context);
vm.runInContext(script, context);
function matchResponse(data) {
  const events = [{type:'progress',stage:'accepted'}, {type:'progress',stage:'checking_constraints',candidate_count:12}, {type:'result',data}];
  return new Response(events.map((event,i)=>JSON.stringify({...event,run_id:'fixture-run',sequence:i+1})).join('\n')+'\n', {headers:{'content-type':'application/x-ndjson'}});
}
const attack='<img src=x onerror=alert(1)>';
const pop=context.popup(attack,[attack]);
assert.equal(pop.children[0].textContent,attack);
assert.equal(pop.children[1].textContent,attack);
assert.equal(pop.children[0].children.length,0);
const scenario={donor_name:'South Seattle Dairy',posting_text:'3 pallets of milk',required_storage:'cold',pallet_count:3,hours_remaining:4,donor_lat:47.5301,donor_lon:-122.3210};
context.applyScenario(scenario);
const req=context.buildRequest();
assert.equal(req.donor_lat,47.5301);
assert.equal(req.donor_lon,-122.3210);
assert.equal(context.buildRequest().deadline_at,req.deadline_at,'Repeated runs must preserve pickup deadline');
assert.equal(nodes.get('review-deadline').getAttribute('datetime'),req.deadline_at,'Presentation retains the exact shared clock timestamp');
const summer=context.deadlinePresentation('2026-10-20T20:00:00Z',Date.parse('2026-10-20T18:30:00Z'));
assert.match(summer.local,/1:00:00 PM PDT/,'October deadline is rendered in Seattle daylight time');
assert.equal(summer.remaining,'1h 30m remaining.');
const winter=context.deadlinePresentation('2026-01-20T20:00:00Z',Date.parse('2026-01-20T18:30:00Z'));
assert.match(winter.local,/12:00:00 PM PST/,'Seattle daylight saving is applied by the timezone formatter');
assert.equal(context.deadlinePresentation('2026-10-20T20:00:00Z',Date.parse('2026-10-20T20:00:00Z')-1).expired,false);
assert.equal(context.deadlinePresentation('2026-10-20T20:00:00Z',Date.parse('2026-10-20T20:00:00Z')).expired,true);
assert.equal(context.deadlinePresentation('invalid'),null);
context.refreshDeadlineDisplay();
assert.equal(context.buildRequest().deadline_at,req.deadline_at,'Refreshing a clock display cannot reanchor its deadline');
const originalDate=context.Date;
const elapsedNow=Date.now()+30*60000;
context.Date=class extends Date {static now(){return elapsedNow;}};
nodes.get('input-donor').value='Updated donor name';
nodes.get('input-text').value='Same milk, updated packaging information';
nodes.get('input-pallets').value=4;
context.offerChanged();
assert.equal(context.buildRequest().deadline_at,req.deadline_at,'Typed non-clock edits after 30 minutes must not add 30 minutes to the deadline');
context.Date=originalDate;

nodes.get('input-pallets').value=-2;
assert.throws(()=>context.buildRequest(),/positive pallet/);
context.applyScenario(scenario);
context.renderDecoys([{name:attack,elimination_reasons:[attack]}]);
assert.equal(nodes.get('decoys-list').children[0].children[0].textContent,attack);
context.renderResults({status:'draft',generation_mode:'deterministic_fallback',message:'Model unavailable',winner:{name:attack,profile_source:'generated'},donor:{name:attack},match_brief:{drafted_sms_outreach:attack,equity_rationale:attack},execution_trace:[{name:'vector_search_recipients'}]});
assert.equal(nodes.get('runtime-status').textContent,'Fallback draft');
assert.equal(nodes.get('brief-sms').textContent,attack);
assert.equal(nodes.get('brief-equity').textContent,attack,'Equity rationale renders as safe text');
assert.match(nodes.get('execution-trace').textContent,/vector_search_recipients/);
assert.match(nodes.get('brief-provenance').textContent,/generated/);
context.renderResults({status:'blocked',message:'MCP unavailable'});
assert.equal(nodes.get('runtime-status').textContent,'Blocked');
assert.equal(nodes.get('match-status').textContent,'MCP unavailable');
assert.equal(context.validPoint({latitude:NaN,longitude:-122}),false);
assert.equal(context.validPoint({latitude:0,longitude:0}),true);
console.log('UI contract: XSS text nodes, scenario coordinates, fixed clock, invalid input, fallback, trace, provenance and blocked state passed.');
const heat = context.heatPoints([
  {lat:47.6,lon:-122.3,need_score:0.4},
  {lat:47.61,lon:-122.31,need_score:null},
  {lat:NaN,lon:-122.3,need_score:0.9},
  {lat:47.6,lon:-122.3,need_score:1.1},
  {lat:47.6,lon:-122.3,need_score:0},
], 'need_score');
assert.equal(heat.length,2,'Missing or invalid need values must not become heat');
assert.equal(heat[0][2],0.4);
assert.equal(heat[1][2],0);
assert.equal(context.capabilityVisible({cold_storage:'commercial'},'cold'),true);
assert.equal(context.capabilityVisible({cold_storage:'vehicle'},'cold'),false);
assert.equal(context.capabilityVisible({frozen_storage:true},'frozen'),true);
context.renderRoute(null,{},{});
assert.match(nodes.get('route-status').textContent,/Routing unavailable/);
context.renderRoute(null,{latitude:47.6,longitude:-122.3},{latitude:47.7,longitude:-122.4});
assert.match(nodes.get('external-directions').href,/google.com\/maps\/dir\/\?api=1/);
assert.match(nodes.get('external-directions').href,/origin=47.6,-122.3&destination=47.7,-122.4/);
assert.doesNotMatch(nodes.get('route-status').textContent,/min estimated/);
context.renderRoute(null,{latitude:NaN,longitude:-122.3},{latitude:47.7,longitude:-122.4});
assert.equal(nodes.get('external-directions').href,undefined);
console.log('City/directions contract: valid heat values, missing-data exclusions, storage filters and safe Google Maps directions link passed.');

const fit = context.seattleFitPoints(
  {latitude:47.6097,longitude:-122.3422},
  {latitude:47.82,longitude:-122.3},
  [{latitude:40.7,longitude:-74},{latitude:47.6,longitude:-122.25}]
);
assert.equal(fit.length,3,'Distant profiles must not expand Seattle framing');
const tileEvents = {};
let tileUrl, tileOptions, fitOptions;
const fakeMap = {setView(){return this;},fitBounds(points,options){fitOptions={points,options};}};
context.L = {
  map:()=>fakeMap,
  tileLayer:(url,options)=>{tileUrl=url;tileOptions=options;return {on(name,fn){tileEvents[name]=fn;return this;},addTo(){return this;}};},
  layerGroup:()=>({addTo(){return this;},clearLayers(){}}),
};
context.initMap();
assert.equal(tileUrl,'https://tile.openstreetmap.org/{z}/{x}/{y}.png');
assert.match(tileOptions.attribution,/https:\/\/www.openstreetmap.org\/copyright/);
assert.equal(tileOptions.referrerPolicy,undefined,'Browser Referer default must remain enabled');
tileEvents.loading(); tileEvents.tileerror(); tileEvents.load();
assert.match(nodes.get('map-tile-status').textContent,/temporarily unavailable/);
tileEvents.loading(); tileEvents.load();
assert.equal(nodes.get('map-tile-status').textContent,'');
context.fitSeattleResults({latitude:47.6,longitude:-122.3},{latitude:47.8,longitude:-122.3},[]);
assert.equal(fitOptions.options.maxZoom,13);
console.log('Map tile contract: OSM attribution, tile failure/recovery and bounded Seattle framing passed.');
const beforeFoodKeeper = context.buildRequest();
context.renderFoodKeeper({status:'options',total_matches:2,selection_required:true,selected_product_id:null,advisory_notice:'Consumer quality guidance only',unknowns_required:['Exact product subtype','Storage history'],products:[{product_id:'10',name:attack,subtitle:'Unopened',category:'Dairy',subcategory:null,storage_advice:[{storage:'refrigerated',basis:'from_purchase',raw_field:'from_date_of_purchase_refrigerate_output_display_only',raw_value:attack}],raw_storage_fields:{refrigerate_output_display_only:null}}]});
assert.match(nodes.get('foodkeeper-status').textContent,/No product is automatically selected/);
const foodCard = nodes.get('foodkeeper-results').children[0];
assert.equal(foodCard.children[0].textContent,attack+' — Unopened');
assert.equal(foodCard.children[2].children[1].textContent,attack,'Raw guidance is a text node, not HTML');
assert.match(foodCard.children[2].children[0].textContent,/from purchase/);
assert.equal(context.buildRequest().deadline_at,beforeFoodKeeper.deadline_at,'Shelf-life lookup must not modify the pickup clock');
assert.equal(context.buildRequest().hours_remaining,beforeFoodKeeper.hours_remaining);
context.renderFoodKeeper({status:'no_match',products:[]});
assert.match(nodes.get('foodkeeper-status').textContent,/no shelf life has been assumed/);
assert.equal(nodes.get('foodkeeper-results').children.length,0);
console.log('FoodKeeper UI contract: ambiguous options, raw storage basis, text safety and unchanged pickup clock passed.');

async function testRescueWorkflow() {
  const bakery=JSON.parse(source.match(/SAMPLE_SCENARIOS = (\[[\s\S]*?\n\])/)[1]).find(item=>item.id==='bakery');
  assert.equal(bakery.posting_text,'roughly 200 loaves of day-old bread and pastries from three store locations');
  assert.equal(bakery.pallet_count,1,'Audited quantity is an explicit hypothetical capacity scenario');
  assert.match(bakery.notes,/hypothetical 1-pallet.*not a loaves-to-pallet conversion/);
  const dairy={...scenario,id:'dairy'};
  const requests=[];
  context.fetch=async (url,options)=>{requests.push({url,options});return {ok:true,json:async()=>[dairy,bakery]};};
  await context.loadScenarios();
  const choices=nodes.get('scenario-buttons').children;
  assert.equal(choices.length,3);
  choices[0].onclick();
  assert.equal(requests.length,1,'Selecting a preset must not call the live broker');
  assert.equal(requests[0].url,'/api/scenarios');
  let offer=context.buildRequest();
  assert.equal(offer.required_storage,'ambient');
  assert.equal(offer.pallet_count,1);
  assert.equal(offer.posting_text,bakery.posting_text,'Preset must preserve the exact audited query');
  assert.match(nodes.get('match-status').textContent,/hypothetical 1-pallet/);
  assert.equal(offer.hours_remaining,24);
  assert.equal(offer.donor_lat,bakery.donor_lat);
  assert.equal(choices[0].getAttribute('aria-pressed'),'true');
  choices[1].onclick();
  assert.equal(context.buildRequest().required_storage,'cold');
  assert.equal(context.buildRequest().hours_remaining,4);
  choices[2].onclick();
  offer=context.buildRequest();
  assert.equal(offer.pallet_count,50);
  assert.match(offer.posting_text,/50 pallets.*full lot/);
  assert.equal(offer.donor_lat,bakery.donor_lat);
  assert.equal(choices[0].getAttribute('aria-pressed'),'false');

  context.renderResults({status:'no_match',pending_confirmation:[{
    name:attack,elimination_reasons:[],confirmations_required:[attack,'Confirm actual storage']
  }],eliminated_decoys:[{name:'Small store',elimination_reasons:['Capacity below full lot']} ]});
  assert.equal(nodes.get('pending-container').classList.contains('hidden'),false);
  assert.equal(nodes.get('pending-list').children[0].children[0].textContent,attack);
  assert.match(nodes.get('pending-list').children[0].children[1].textContent,/Confirm actual storage/);
  assert.equal(nodes.get('result-counts').textContent,'1 rejected · 1 awaiting details');
  assert.equal(nodes.get('step-handoff').disabled,true);
  assert.match(nodes.get('result-description').textContent,/do not extend a deadline/);

  choices[0].onclick();
  let resolveMatch;
  const matchCalls=[];
  context.fetch=(url,options)=>{
    matchCalls.push({url,options});
    return new Promise(resolve=>{resolveMatch=resolve;});
  };
  // Validation must happen before locking the offer, requesting data or moving focus to progress.
  nodes.get('input-pallets').value='0';
  await context.runMatching();
  assert.equal(matchCalls.length,0);
  assert.equal(focusedId,'offer-panel');
  assert.notEqual(nodes.get('input-pallets').disabled,true);
  assert.equal(nodes.get('progress-history').children.length,0);
  assert.equal(nodes.get('match-progress').classList.contains('hidden'),true);
  nodes.get('input-pallets').value=1;
  const first=context.runMatching();
  assert.equal(focusedId,'review-panel','A valid run makes progress visible before the request resolves');
  assert.equal(nodes.get('btn-cancel-match').classList.contains('hidden'),false);
  assert.equal(nodes.get('progress-history').children.length,0,'No stage is invented while awaiting the server');
  assert.equal(nodes.get('review-panel').getAttribute('aria-busy'),'true');
  assert.equal(nodes.get('pending-container').classList.contains('hidden'),true);
  assert.equal(nodes.get('input-pallets').disabled,true);
  assert(choices.every(button=>button.disabled),'All preset buttons are locked during a request');
  await context.runMatching();
  choices[2].onclick();
  assert.equal(matchCalls.length,1,'Rapid clicks must not overlap live model requests');
  assert.equal(nodes.get('input-pallets').value,1,'Locked preset actions must not change the in-flight offer');
  resolveMatch(matchResponse({status:'blocked',message:'Toolbox unavailable'}));
  await first;
  assert.equal(nodes.get('input-pallets').disabled,false);
  assert(choices.every(button=>!button.disabled));
  assert.equal(nodes.get('review-panel').getAttribute('aria-busy'),'false');
  assert.equal(nodes.get('action-retry').classList.contains('hidden'),false);
  assert.deepEqual(nodes.get('progress-history').children.map(row=>row.getAttribute('data-stage')),['accepted','checking_constraints']);
  assert(nodes.get('progress-history').children.every(row=>row.getAttribute('aria-current')===undefined));
  assert.match(nodes.get('progress-outcome').textContent,/could not complete a usable result/);

  assert.equal(matchCalls[0].url,'/api/match/stream');
  assert.equal(nodes.get('btn-cancel-match').classList.contains('hidden'),true);
  const fixedDeadline=context.buildRequest().deadline_at;
  const encoder=new TextEncoder();
  let progressController, readerCanceled=false, pendingSignal;
  context.fetch=async(url,options)=>{
    pendingSignal=options.signal;
    return new Response(new ReadableStream({
      start(controller){progressController=controller;},
      cancel(){readerCanceled=true;},
    }),{headers:{'content-type':'application/x-ndjson'}});
  };
  const pending=context.runMatching();
  await new Promise(setImmediate);
  progressController.enqueue(encoder.encode(JSON.stringify({type:'progress',stage:'checking_constraints',candidate_count:12,run_id:'cancel-fixture',sequence:1})+'\n'));
  await new Promise(setImmediate);
  assert.match(nodes.get('result-description').textContent,/Checking storage/,'Real progress is shown before a terminal result');
  assert.match(nodes.get('result-counts').textContent,/12 retrieved candidates.*shortlist/);
  assert.equal(nodes.get('btn-cancel-match').classList.contains('hidden'),false);
  assert.deepEqual(nodes.get('progress-history').children.map(row=>row.getAttribute('data-stage')),['checking_constraints'],'Skipped stages must not be invented');
  assert.match(content(nodes.get('progress-history')),/12 retrieved candidates.*shortlist/);
  context.matchProgress('untrusted-stage',{candidate_count:999});
  assert.equal(nodes.get('progress-history').children.length,1);
  context.matchProgress('checking_constraints',{candidate_count:-1});
  assert.equal(nodes.get('progress-history').children.length,1,'A repeated stage updates its receipt instead of adding a fake new step');
  assert.match(content(nodes.get('progress-history')),/12 retrieved candidates/,'Invalid repeated counts cannot replace a valid count');
  context.cancelMatching();
  await pending;
  assert.equal(pendingSignal.aborted,true);
  assert.equal(readerCanceled,true,'Cancel closes the active response reader');
  assert.match(nodes.get('progress-outcome').textContent,/Check canceled.*No result was used/);
  assert.equal(nodes.get('progress-history').children.length,1,'Cancellation keeps the received stage history without claiming completion');
  assert.equal(nodes.get('review-panel').getAttribute('data-state'),'canceled');
  assert.equal(nodes.get('input-pallets').disabled,false);
  assert.equal(nodes.get('btn-cancel-match').classList.contains('hidden'),true);
  assert.equal(nodes.get('action-retry').classList.contains('hidden'),false);
  assert.equal(context.buildRequest().deadline_at,fixedDeadline);
  context.fetch=async()=>matchResponse({status:'no_match'});
  await context.runMatching();
  assert.equal(context.buildRequest().deadline_at,fixedDeadline,'Retry preserves the canceled offer clock');
  assert.deepEqual(nodes.get('progress-history').children.map(row=>row.getAttribute('data-stage')),['accepted','checking_constraints'],'Retry owns a new history');
  assert.match(nodes.get('progress-outcome').textContent,/No recipient passed/);

  const readStream=context.OfferWorkflow.readMatchStream;
  context.OfferWorkflow.readMatchStream=async(...args)=>{
    const result=await readStream(...args);
    context.cancelMatching();
    return result;
  };
  await context.runMatching();
  assert.equal(nodes.get('review-panel').getAttribute('data-state'),'canceled','Cancel winning the final-record race must discard the result');
  assert.equal(nodes.get('step-handoff').disabled,true);
  context.OfferWorkflow.readMatchStream=readStream;

  const realSetTimeout=context.setTimeout, realClearTimeout=context.clearTimeout;
  let expireRequest, requestLimit, cleared=false;
  context.setTimeout=(fn,ms)=>{expireRequest=fn;requestLimit=ms;return 17;};
  context.clearTimeout=id=>{cleared=id===17;};
  context.fetch=async()=>new Response(new ReadableStream(),{headers:{'content-type':'application/x-ndjson'}});
  const timed=context.runMatching();
  await new Promise(setImmediate);
  assert.equal(requestLimit,175000);
  expireRequest();
  await timed;
  assert.match(nodes.get('result-description').textContent,/time limit.*same pickup deadline/);
  assert.equal(nodes.get('input-pallets').disabled,false);
  assert.equal(cleared,true);
  assert.equal(context.buildRequest().deadline_at,fixedDeadline);
  context.setTimeout=realSetTimeout;context.clearTimeout=realClearTimeout;

  const payload={status:'draft',generation_mode:'deterministic_fallback',
    winner:{name:'Food Bank',city:'Seattle',profile_source:'generated'},
    runners_up:[{name:'One actual backup'}],
    match_brief:{drafted_sms_outreach:'Please confirm acceptance.',drafted_email_outreach:'Email draft.',
      clock_action_plan:'Confirm timing.',equity_rationale:'Neighborhood proxy, not individual eligibility.'},
    provenance_warning:'Generated profiles; verify with recipient.'};
  context.renderResults(payload);
  assert.equal(nodes.get('step-handoff').disabled,false);
  assert.equal(nodes.get('action-handoff').classList.contains('hidden'),false);
  let copied=[];
  context.navigator.clipboard={writeText:async text=>copied.push(text)};
  await context.copyMatchBrief();
  assert.match(copied[0],/Available backups: One actual backup\n/);
  assert.match(copied[0],/Generation mode: deterministic_fallback/);
  assert.match(copied[0],/Generated profiles; verify with recipient/);
  assert.match(nodes.get('handoff-feedback').textContent,/no outreach was sent/);
  assert.equal(nodes.get('step-handoff').getAttribute('aria-current'),'step');
  await context.copyEmail();
  assert.equal(copied[1],'Email draft.');
  context.offerChanged();
  await context.copyMatchBrief();
  assert.equal(copied.length,2,'Changing an offer invalidates the previous copy payload');
  assert.equal(nodes.get('step-handoff').disabled,true);
  assert.equal(nodes.get('brief-container').classList.contains('hidden'),true);

  context.fetch=async()=>{throw new Error('Network unavailable');};
  await context.runMatching();
  assert.equal(nodes.get('review-panel').getAttribute('data-state'),'blocked');
  assert.equal(nodes.get('input-pallets').disabled,false,'Transport errors release the form lock');
  assert.equal(nodes.get('action-retry').classList.contains('hidden'),false);
  console.log('Rescue workflow: fill-only presets, pending facts, single-flight requests, invalidated results, truthful handoff copies and recoverable errors passed.');
}
async function testVoiceIntake() {
  const timers=new Map(); let timerId=0;
  context.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};
  context.clearTimeout=id=>timers.delete(id);
  context.location={protocol:'https:',host:'food.example'};
  Object.assign(context,{ArrayBuffer,DataView,Float32Array,Int16Array,Blob});
  const revoked=[];
  context.URL={createObjectURL:()=> 'blob:worklet-test',revokeObjectURL:url=>revoked.push(url)};
  const sockets=[];
  class Socket {
    constructor(url){this.url=url;this.readyState=1;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
    send(data){this.sent.push(data);}
    close(){this.readyState=3;this.closed=true;}
    receive(data){this.onmessage?.({data:typeof data==='string'?data:JSON.stringify(data)});}
  }
  context.WebSocket=Socket;
  const audioContexts=[];
  class AudioContext {
    constructor(){this.state='running';this.currentTime=0;this.destination={};this.audioWorklet={addModule:async()=>{}};audioContexts.push(this);}
    resume(){return Promise.resolve();}
    close(){this.state='closed';return Promise.resolve();}
    createMediaStreamSource(){this.input={connect(){},disconnect(){this.disconnected=true;}};return this.input;}
    createBuffer(channels,n,rate){this.lastRate=rate;this.samples=new Float32Array(n);return {duration:n/rate,getChannelData:()=>this.samples};}
    createBufferSource(){this.lastSource={connect(){},disconnect(){},start(){this.started=true;},stop(){this.stopped=true;}};return this.lastSource;}
  }
  context.window.AudioContext=AudioContext;
  const worklets=[];
  context.AudioWorkletNode=class {
    constructor(){this.port={};worklets.push(this);}
    connect(){}
    disconnect(){this.disconnected=true;}
  };
  const streams=[];
  const createStream=()=>{const track={stopped:false,stop(){this.stopped=true;}};const stream={track,getTracks:()=>[track]};streams.push(stream);return stream;};
  let micRequests=0;
  context.navigator.mediaDevices={getUserMedia:async()=>{micRequests++;return createStream();}};
  const calls=[];
  context.fetch=async url=>{calls.push(url);return {ok:true,json:async()=>({enabled:false,max_session_seconds:180})};};
  await context.loadVoiceConfig();
  await context.startVoiceSession(true);
  assert.equal(sockets.length,0,'Disabled config must not connect a session');
  assert.equal(micRequests,0,'Config check and disabled Start must not request microphone permission');
  assert.equal(nodes.get('voice-start').disabled,true);
  assert.match(nodes.get('voice-status').textContent,/not enabled/);
  context.fetch=async url=>{calls.push(url);return {ok:true,json:async()=>({enabled:true,max_session_seconds:180})};};
  await context.loadVoiceConfig();
  assert.equal(micRequests,0,'Enabled config alone must not open the microphone');
  context.applyScenario(scenario);
  const before=context.buildRequest().deadline_at;
  const tick=async()=>{await Promise.resolve();await Promise.resolve();await Promise.resolve();};
  async function readySession(mic=false) {
    const promise=context.startVoiceSession(mic);
    await tick();
    const socket=sockets.at(-1);
    socket.receive({type:'ready'});
    await promise;
    return socket;
  }
  const socket=await readySession(true);
  assert.equal(socket.url,'wss://food.example/api/live/intake');
  assert.equal(micRequests,1);
  const initial=JSON.parse(socket.sent[0]);
  assert.equal(initial.type,'context');
  assert.equal(initial.fields.deadline_at,before,'Existing form deadline must be forwarded unchanged');
  assert.equal(initial.fields.donor_lat,scenario.donor_lat);
  worklets.at(-1).port.onmessage({data:new Int16Array(320).buffer});
  assert.equal(socket.sent.at(-1).byteLength,640,'Audio uploads are bounded PCM frames');
  context.playVoiceAudio(new Int16Array([0,32767,-32768]).buffer);
  assert.equal(audioContexts[0].lastRate,24000,'Live audio playback uses the server’s 24 kHz PCM rate');
  assert.equal(audioContexts[0].samples[2],-1);
  assert.equal(audioContexts[0].lastSource.started,true);
  socket.receive({type:'interrupted'});
  assert.equal(audioContexts[0].lastSource.stopped,true,'Model interruption cancels queued speech');
  const fields={...scenario,posting_text:attack,deadline_at:new Date(Date.now()+3*3600000).toISOString(),hours_remaining:3};
  const draft={type:'draft',draft_id:'draft-one',draft_version:2,fields,missing_fields:[],ready_for_review:true,deadline_changed:true};
  socket.receive(draft);
  assert.equal(nodes.get('voice-apply').disabled,false);
  assert.equal(nodes.get('voice-draft').children[1].textContent,'Available surplus: '+attack);
  socket.receive({...draft,draft_version:1,fields:{...fields,posting_text:'stale offer'}});
  socket.receive({...draft,draft_id:'another-draft',draft_version:3,fields:{...fields,posting_text:'wrong session'}});
  assert.equal(nodes.get('voice-draft').children[1].textContent,'Available surplus: '+attack,'Older versions and foreign draft IDs must not replace the current draft');
  socket.receive({type:'transcript',role:'model',text:attack,final:true});
  assert.equal(nodes.get('voice-transcript').children[0].textContent,'Intake agent: '+attack);
  socket.receive('null'); // malformed but valid JSON must not break the session.
  const lateMessage=socket.onmessage;
  context.applyVoiceDraft();
  assert.equal(socket.closed,true);
  assert(streams.every(stream=>stream.track.stopped),'Applying must release every microphone track');
  assert(audioContexts.every(ctx=>ctx.state==='closed'),'Applying must close input and output audio contexts');
  assert(worklets.every(worklet=>worklet.disconnected),'Applying must disconnect the worklet');
  assert(revoked.includes('blob:worklet-test'));
  assert.equal(timers.size,0,'Applying cancels session timers');
  assert(calls.every(url=>url==='/api/live/config'),'Voice Start, draft and Apply must not call the matching API');
  let request=context.buildRequest();
  assert.equal(request.deadline_at,fields.deadline_at,'Apply must preserve the exact server deadline string');
  assert.equal(request.posting_text,attack);
  nodes.get('input-text').value='Reviewed bread offer with updated packaging';
  context.offerChanged();
  request=context.buildRequest();
  assert.equal(request.deadline_at,fields.deadline_at,'Non-clock edits must not restart an applied voice deadline');
  lateMessage({data:JSON.stringify({...draft,draft_version:4,fields:{...fields,posting_text:'late stale update'}})});
  assert.equal(nodes.get('input-text').value,'Reviewed bread offer with updated packaging','Closed-session events cannot change the form');
  const posted=[];
  context.fetch=async(url,options)=>{posted.push({url,body:JSON.parse(options.body)});return matchResponse({status:'no_match'});};
  await context.runMatching();
  assert.equal(posted.length,1);
  assert.equal(posted[0].body.deadline_at,fields.deadline_at,'Only the explicit match action posts the reviewed fixed deadline');
  nodes.get('input-hours').value=2;
  context.offerChanged('hours');
  assert.notEqual(context.buildRequest().deadline_at,fields.deadline_at,'An explicit hours edit replaces the voice clock');

  nodes.get('voice-text').value='Only one pallet is available.';
  const sendText=context.sendVoiceText();
  await tick();
  const textSocket=sockets.at(-1);
  textSocket.receive({type:'ready'});
  await sendText;
  assert.equal(micRequests,1,'An explicit typed Send starts its session without microphone access');
  assert.equal(JSON.parse(textSocket.sent.at(-1)).type,'text');
  textSocket.receive({...draft,draft_id:'text-draft',draft_version:1,fields:{...fields,deadline_at:'2020-01-01T10:00:00Z'}});
  assert.equal(nodes.get('voice-apply').disabled,true,'Expired drafts are not applicable');
  const beforeInvalid=nodes.get('input-text').value;
  context.applyVoiceDraft();
  assert.equal(nodes.get('input-text').value,beforeInvalid);
  textSocket.receive({...draft,draft_id:'text-draft',draft_version:2,fields:{...fields,pallet_count:'1'}});
  assert.equal(nodes.get('voice-apply').disabled,true,'Numeric strings must not bypass validation');
  context.offerChanged();
  assert.equal(textSocket.closed,true,'Editing the form ends a session whose context is now stale');

  let resolvePermission;
  context.navigator.mediaDevices.getUserMedia=()=>{micRequests++;return new Promise(resolve=>{resolvePermission=resolve;});};
  const pending=context.startVoiceSession(true);
  await tick();
  sockets.at(-1).receive({type:'ready'});
  await tick();
  assert.equal(typeof resolvePermission,'function');
  context.stopVoiceSession();
  const lateStream=createStream();
  resolvePermission(lateStream);
  await pending;
  assert.equal(lateStream.track.stopped,true,'Stop during a permission prompt must stop any subsequently granted stream');
  assert.equal(timers.size,0);

  context.navigator.mediaDevices.getUserMedia=async()=>{throw new Error('Permission denied');};
  const denied=context.startVoiceSession(true);
  await tick(); sockets.at(-1).receive({type:'ready'}); await denied;
  assert.equal(sockets.at(-1).closed,true);
  assert.match(nodes.get('voice-status').textContent,/typed note/);
  assert.equal(nodes.get('voice-send').disabled,false,'Permission failure leaves explicit typed fallback available');
  const errorSocket=await readySession(false);
  errorSocket.onerror();
  assert.equal(errorSocket.closed,true);
  assert.equal(nodes.get('voice-start').disabled,false,'Transport failure permits an explicit retry');
  assert(audioContexts.every(ctx=>ctx.state==='closed'));
  assert.equal(timers.size,0);

  // Execute the exact embedded worklet at real device rates without a browser or microphone.
  const workletCode=vm.runInContext('voiceWorkletSource',context);
  for (const rate of [16000,44100,48000]) {
    const frames=[];let Processor;
    const audioVM=vm.createContext({Float32Array,Int16Array,Math,sampleRate:rate,
      AudioWorkletProcessor:class {constructor(){this.port={postMessage:buffer=>frames.push(new Int16Array(buffer))};}},
      registerProcessor:(_,type)=>{Processor=type;}});
    vm.runInContext(workletCode,audioVM);
    const processor=new Processor();
    const count=Math.round(rate*0.12);
    for(let offset=0;offset<count;offset+=128) processor.process([[new Float32Array(Math.min(128,count-offset)).fill(0.5)]]);
    assert(Math.abs(frames.length*320+processor.count-1920)<=1,'120 ms resamples to 1,920 samples within one retained interpolation sample');
    assert(frames.every(frame=>frame.length===320 && frame.every(sample=>sample===16384)),`Valid 16 kHz PCM at device rate ${rate}`);
  }
  console.log('Voice intake: feature gate, explicit permission, text fallback, safe versioned drafts, no auto-match, exact deadline, PCM resampling and race-safe cleanup passed.');
}
testRescueWorkflow().then(testVoiceIntake).catch(error=>{console.error(error);process.exitCode=1;});
