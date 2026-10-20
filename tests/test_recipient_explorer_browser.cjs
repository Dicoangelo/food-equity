// Optional real-browser QA: NODE_PATH=<playwright package root> node this-file.cjs
// Uses an isolated browser and local fixture server. Never calls the live broker.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const serveOnly = process.argv.includes('--serve');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'agent/api.py'), 'utf8');
const html = source.split('html_content = r"""')[1].split('"""')[0];
function fixtureHtml() {
  if (!serveOnly) return html;
  const current = fs.readFileSync(path.join(root, 'agent/api.py'), 'utf8').split('html_content = r"""')[1].split('"""')[0];
  const banner = '<div id="local-review-banner" role="status" style="position:relative;margin:8px auto;z-index:1;max-width:calc(100vw - 24px);box-sizing:border-box;padding:9px 16px;border:1px solid #fbbf24;border-radius:8px;background:#422006;color:#fef3c7;font:600 13px/1.4 system-ui;text-align:center;pointer-events:none">Local review · sample data · no live checks</div>';
  return current.replace(/(<body[^>]*>)/, '$1' + banner);
}
const scenarios = JSON.parse(source.match(/SAMPLE_SCENARIOS = (\[[\s\S]*?\n\])/)[1]);
const cityRows = [
  {recipient_id:'1',name:'Harbor Pantry',city:'Seattle',lat:47.61,lon:-122.32,cold_storage:'commercial',frozen_storage:null,max_pallets:4,pickup_window_hours:1,service_days:'Daily',profile_source:'generated',geography_precision:'zip_centroid'},
  {recipient_id:'2',name:'Community Kitchen',city:'Seattle',lat:47.64,lon:-122.30,cold_storage:'dry',frozen_storage:false,max_pallets:2,pickup_window_hours:2,service_days:'Daily',profile_source:'planted',geography_precision:'zip_centroid'},
  {recipient_id:'3',name:'North Pantry',city:'Shoreline',lat:47.72,lon:-122.34,cold_storage:null,frozen_storage:null,max_pallets:null,pickup_window_hours:null,service_days:null,profile_source:'generated',geography_precision:'zip_centroid'},
];
const fetchedAt=new Date().toISOString(), expiresAt=new Date(Date.now()+300000).toISOString();
const city = {sources:[{source_id:'city:recipients',source_kind:'city_recipients',status:'ready',provenance:'unknown',fetched_at:fetchedAt,expires_at:expiresAt,cache_state:'fresh',coverage:{returned:3,matching_source_rows:3,row_cap:100,truncated:false,skipped_invalid_or_duplicate:0}}],status:'ready',as_of:'2026-10-20T17:30:00Z',recipients:cityRows,tracts:[
  {tract_id:'53033000100',lat:47.61,lon:-122.32,poverty_rate:.2,no_vehicle_rate:.3,need_score:.25,population:1000},
],warnings:['Offline QA fixtures; no operational availability.'],provenance:{observation_date:null}};
function evaluated(row, decision) {
  return {...row,latitude:row.lat,longitude:row.lon,constraint_status:decision,
    refrigerated_transport:true,loading_dock:false,semantic_distance:.1,composite_score:.6475,
    tract_poverty_pct:20,tract_no_vehicle_pct:30,tract_id:'53033000100',
    acceptance_check:'no_explicit_restriction_detected',acceptance_evidence:[],
    confirmations_required:['Confirm current capacity and recipient acceptance.'],
    timing_basis:'profile_pickup_estimate',time_margin_hours:22.8,reason_codes:[],warnings:[]};
}
let matchCalls = 0;
const requests = [];
const server = http.createServer((req,res) => {
  requests.push(req.url);
  const json = value => {res.setHeader('content-type','application/json');res.end(JSON.stringify(value));};
  if(req.url === '/') {res.setHeader('content-type','text/html');return res.end(fixtureHtml());}
  if(req.url.startsWith('/ui/')) {
    const name=req.url.slice('/ui/'.length);
    if(!['intake-review.js','intake-review.css','food-rescue-network.png','review-crate-spot.png','companion.js','companion.css','alert-intake.js','recipient-explorer.js','recipient-explorer.css','offer-workflow.js','source-state.js','view-context.js','workspace.css','workspace.js'].includes(name)) {res.writeHead(404);return res.end();}
    res.setHeader('content-type',name.endsWith('.png')?'image/png':name.endsWith('.js')?'text/javascript':'text/css');
    return res.end(fs.readFileSync(path.join(root,'agent/ui',name)));
  }
  if(req.url === '/api/scenarios') return json(scenarios);
  if(req.url === '/api/city') return json(city);
  if(req.url === '/api/live/config') return json({enabled:false});
  if(req.url === '/api/match/stream') {
    matchCalls++;
    let raw='';
    req.on('data',chunk=>raw+=chunk);
    return req.on('end',()=>{
      const offer=JSON.parse(raw);
      const timing={deadline_at:offer.deadline_at,evaluated_at:new Date().toISOString()};
      const winner={...evaluated(cityRows[0],'eligible'),...timing};
      const backup={...evaluated(cityRows[1],'eligible'),...timing};
      const pending={...evaluated(cityRows[2],'needs_confirmation'),...timing,
        composite_score:.99,reason_codes:['acceptance_confirmation_required'],elimination_reasons:['Receiving-site storage is unknown.']};
      const payload={status:'draft',generation_mode:'deterministic_fallback',...timing,winner,runners_up:[backup],
        pending_confirmation:[pending],eliminated_decoys:[],
        donor:{name:offer.donor_name,posting_text:offer.posting_text,latitude:offer.donor_lat,longitude:offer.donor_lon,
          pallet_count:offer.pallet_count,required_storage:offer.required_storage,deadline_at:offer.deadline_at},
        match_brief:{drafted_sms_outreach:'Please confirm acceptance.',drafted_email_outreach:'Please review capacity.'}};
      res.setHeader('content-type','application/x-ndjson');
      res.end([{type:'progress',stage:'accepted'},{type:'progress',stage:'checking_constraints',candidate_count:3},{type:'result',data:payload}]
        .map((event,i)=>JSON.stringify({...event,run_id:'fixture-'+matchCalls,sequence:i+1})).join('\n')+'\n');
    });
  }
  res.writeHead(404);res.end();
});
async function run() {
  const {chromium} = require('playwright');
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {});
  try {
    const page=await browser.newPage();
    page.setDefaultTimeout(15000);
    // A regression must fail the test, never activate a real microphone.
    await page.addInitScript(()=>{
      window.qaMicrophoneRequests=0;
      if(navigator.mediaDevices) navigator.mediaDevices.getUserMedia=()=>{
        window.qaMicrophoneRequests++;
        return Promise.reject(new Error('Microphone access is disabled in local fixture QA.'));
      };
    });
    const sockets=[];
    page.on('websocket',socket=>sockets.push(socket.url()));
    const errors=[];
    page.on('pageerror',error=>errors.push({width:page.viewportSize()?.width,message:error.message,stack:error.stack}));
    const output=process.env.QA_OUTPUT_DIR;
    if(output) fs.mkdirSync(output,{recursive:true});
    const apiCount=()=>requests.filter(url=>url.startsWith('/api/')).length;
    const screenshot=async(name,width)=>{
      if(output) await page.screenshot({path:path.join(output,`${name}-${width}.png`),fullPage:false});
    };
    const nav=async name=>{
      const button=page.locator(`.workspace-tabs [data-workspace-view="${name}"]`);
      await button.click();
      assert.equal(await button.getAttribute('aria-current'),'page');
      await page.locator(`#${name==='match'?'match':name}-workspace`).waitFor({state:'visible'});
    };
    const state=()=>page.evaluate(()=>({
      form:buildRequest(),result:currentPayload,
      mapSame:document.getElementById('map')===window.qaMapNode && map===window.qaMapInstance,
    }));
    const stable=async(before,label)=>{
      const after=await state();
      assert.deepEqual(after,before,label);
      assert.equal(after.mapSame,true,'Workspace changes preserve the same map DOM node and Leaflet instance');
    };
    for(const width of [1440,1024,768,390,320]) {
      await page.setViewportSize({width,height:900});
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.waitForFunction(()=>document.getElementById('recipient-list').textContent.includes('Harbor Pantry'));
      await page.waitForFunction(()=>typeof L !== 'undefined' && document.querySelector('.leaflet-container') && window.FoodWorkspace);
      await page.evaluate(()=>{window.qaMapNode=document.getElementById('map');window.qaMapInstance=map;window.scrollTo(0,0);});
      assert.equal(await page.locator('#local-review-banner').count(),0,'The review banner is only injected in --serve mode');
      await screenshot('workspace',width);
      await page.locator('#scenario-panel > summary').click();
      await page.getByRole('button',{name:/Bakery surplus/}).click();
      assert.equal(await page.locator('#input-pallets').inputValue(),'1');
      const beforeNavigation=await state(),navigationCalls=apiCount();
      for(const name of ['recipients','evidence','match','recipients']) {
        await nav(name);
        await stable(beforeNavigation,'Switching workspace views must preserve the form, fixed deadline and current result');
      }
      assert.equal(apiCount(),navigationCalls,'Workspace navigation never requests matching or source data');
      const countBefore=matchCalls;
      const first=page.locator('#recipient-list button').filter({hasText:'Harbor Pantry'}).first();
      await first.click();
      await page.locator('#recipient-detail').waitFor({state:'visible'});
      assert.match(await page.locator('#recipient-detail-content').innerText(),/generated|Generated/);
      assert.match(await page.locator('#recipient-detail-content').innerText(),/Unknown/);
      assert.match(await page.locator('#recipient-detail-content').innerText(),/Profile evidence: ready.*operations unconfirmed/s);
      assert.match(await page.locator('#source-state-summary').textContent(),/Recipient dataset: ready/);
      assert.equal(matchCalls,countBefore,'Opening a profile must not request matching');
      await page.keyboard.press('Escape');
      await page.locator('#recipient-detail').waitFor({state:'hidden'});
      assert(await first.evaluate(el=>el===document.activeElement),'Escape restores focus to the profile opener');
      await page.locator('#recipient-search').fill('Shoreline');
      assert.equal(await page.locator('#recipient-list button').count(),1);
      assert.equal(await page.locator('#map [role="button"][aria-controls="recipient-detail"]').count(),1,'Map and list share the filters');
      await page.locator('#recipient-search').fill('');
      const marker=page.locator('#map [aria-label="Open profile: Harbor Pantry"]');
      await marker.focus();
      await page.keyboard.press('Enter');
      await page.locator('#recipient-detail').waitFor({state:'visible'});
      assert.equal(await page.locator('#recipient-detail-title').innerText(),'Harbor Pantry','Marker and list open the same profile');
      await page.locator('#recipient-close').click();
      await nav('match');
      await page.locator('#btn-match').click();
      await page.locator('#brief-container').waitFor({state:'visible'});
      await page.locator('#review-panel').scrollIntoViewIfNeeded();
      await screenshot('result',width);
      const completed=await state(),completedCalls=apiCount();
      for(const name of ['recipients','evidence','match']) {
        await nav(name);
        await stable(completed,'Navigation after matching preserves the exact result and pickup deadline');
        assert.match(await page.locator('#workspace-clock').innerText(),/^Deadline ·/,'A hidden ancestor view does not invalidate the active pickup deadline');
      }
      assert.equal(apiCount(),completedCalls,'Reviewing completed results across views makes no extra API requests');
      await page.locator('#inspect-winner').click();
      const detail=await page.locator('#recipient-detail-content').innerText();
      assert.match(detail,/0\.6475|64\.75/);
      assert.match(detail,/15 min|0\.25/);
      await page.getByText('How this offer-fit score is built',{exact:true}).click();
      assert.match(await page.locator('#recipient-detail-content').innerText(),/rounded|rounding/i);
      assert(await page.locator('#recipient-detail').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Drawer must not overflow horizontally');
      if(width<=390) {
        assert.equal(await page.locator('#recipient-detail').getAttribute('aria-modal'),'true');
        assert(await page.locator('.page-shell').evaluate(el=>el.inert),'Mobile profile makes background inert');
        await page.locator('#recipient-close').focus();
        await page.keyboard.press('Shift+Tab');
        assert(await page.locator('#recipient-detail').evaluate(el=>el.contains(document.activeElement)),'Mobile sheet traps keyboard focus');
      }
      await page.locator('#recipient-detail').evaluate(el=>el.scrollTop=0);
      await screenshot('profile',width);
      await page.locator('#recipient-close').click();
      await page.locator('#recipient-compare summary').click();
      const compareButton=page.locator('#recipient-compare button').nth(1);
      await compareButton.click();
      assert.equal(await page.locator('#recipient-detail-title').innerText(),'Community Kitchen');
      await page.locator('#recipient-close').click();
      assert(await compareButton.evaluate(el=>el===document.activeElement),'Closing comparison profile restores its opener');
      await nav('recipients');
      await page.locator('#outcome-filter').selectOption('needs_confirmation');
      assert.equal(await page.locator('#recipient-list button').count(),1);
      assert.equal(await page.locator('#map [role="button"][aria-controls="recipient-detail"]').count(),1,'Evaluated map pins obey outcome filters');
      await page.locator('#recipient-list button').first().click();
      assert.match(await page.locator('#recipient-detail-content').innerText(),/acceptance_confirmation_required/);
      assert.doesNotMatch(await page.locator('#recipient-detail-content').innerText(),/0\.9900|99\.00/);
      await page.locator('#recipient-close').click();
      await page.locator('#outcome-filter').selectOption('all');
      await page.locator('#fit-recipient-view').click();
      await page.locator('#map-panel').scrollIntoViewIfNeeded();
      await screenshot('recipients',width);
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`No page overflow at ${width}px`);
      const apiCallsBefore=apiCount();
      const profileButton=page.locator('#recipient-list button').filter({hasText:'Community Kitchen'}).first();
      await profileButton.click();
      assert.equal(apiCount(),apiCallsBefore,'Profile selection must reuse loaded data');
      const selectedBefore=await page.evaluate(()=>JSON.stringify(ViewContext.snapshot()));
      const beforeRead=await state();
      await page.locator('#recipient-ask-assistant').click();
      await page.locator('#assistant-drawer').waitFor({state:'visible'});
      await page.locator('#recipient-detail').waitFor({state:'hidden'});
      assert.equal(await page.evaluate(()=>JSON.stringify(ViewContext.snapshot())),selectedBefore,'Asking about a recipient preserves the selected ID and evidence revision');
      assert.equal(await page.locator('#assistant-context-name').innerText(),'Community Kitchen');
      assert(await page.locator('.page-shell').evaluate(el=>el.inert),'Assistant makes workspace background inert');
      assert.equal(await page.locator('#assistant-drawer').getAttribute('aria-modal'),'true');
      await page.locator('#assistant-close').focus();
      await page.keyboard.press('Shift+Tab');
      assert(await page.locator('#assistant-drawer').evaluate(el=>el.contains(document.activeElement)),'Assistant traps backward tab');
      await page.keyboard.press('Tab');
      assert(await page.locator('#assistant-drawer').evaluate(el=>el.contains(document.activeElement)),'Assistant traps forward tab');
      await page.evaluate(()=>{
        const snapshot=ViewContext.snapshot();
        const owner={turn_id:'browser-fixture',expected_offer_revision:snapshot.offer_revision,expected_view_revision:snapshot.view_revision};
        ViewActionReceipts.begin(owner);
        ViewActionReceipts.accept({type:'action_result',...owner,call_id:'fixture-read',
          outcome:'applied',effect:'read_only',resulting_revision:snapshot.view_revision,
          payload:{authority:'untrusted_browser_projection',reported_recipient:snapshot.result.candidates.find(row=>row.recipient_id===snapshot.selected_recipient_id),sources:snapshot.sources}});
        ViewActionReceipts.finish(owner);
      });
      const receipt=page.locator('#voice-action-receipt');
      assert.match(await receipt.innerText(),/Profile: Community Kitchen/);
      assert.match(await receipt.innerText(),/availability unconfirmed/);
      assert.match(await receipt.innerText(),/Confirm current capacity and recipient acceptance/);
      assert.match(await receipt.innerText(),/Source status: partial/);
      assert.equal(await receipt.locator('details').last().getAttribute('open'),null,'Technical JSON is optional, not the primary answer');
      await stable(beforeRead,'Opening the assistant and reading evidence preserves offer, deadline and match');
      assert.equal(apiCount(),apiCallsBefore,'Opening the assistant never starts a provider or source request');
      await receipt.scrollIntoViewIfNeeded();
      assert(await receipt.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Evidence receipt fits the viewport');
      await screenshot('assistant-recipient',width);
      await page.keyboard.press('Escape');
      await page.locator('#assistant-drawer').waitFor({state:'hidden'});
      await page.locator('#recipient-detail').waitFor({state:'visible'});
      assert.equal(await page.evaluate(()=>JSON.stringify(ViewContext.snapshot())),selectedBefore,'Closing the assistant restores the same recipient and evidence revision');
      assert(await page.locator('#recipient-ask-assistant').evaluate(el=>el===document.activeElement),'Escape restores focus to the recipient Ask button');
      await page.keyboard.press('Escape');
      await page.locator('#recipient-detail').waitFor({state:'hidden'});
      assert(await profileButton.evaluate(el=>el===document.activeElement),'Second Escape closes only the profile and restores its opener');
      assert.equal(await page.locator('.page-shell').evaluate(el=>el.inert),false,'Closing both dialogs restores the workspace');
      await nav('evidence');
      await page.evaluate(()=>window.scrollTo(0,0));
      await screenshot('evidence',width);
      await page.locator('.workspace-topbar [data-open-assistant]').click();
      await page.locator('#assistant-drawer').waitFor({state:'visible'});
      await screenshot('evidence-assistant',width);
      await page.locator('#assistant-close').click();
      assert.equal(await page.locator('.page-shell').evaluate(el=>el.inert),false);
      assert.equal(apiCount(),apiCallsBefore,'Evidence and assistant views make no unexpected calls');
      await nav('match');
      assert(await page.evaluate(()=>{
        const snapshot=ViewContext.snapshot();
        const owner={turn_id:'before-quantity-edit',expected_offer_revision:snapshot.offer_revision,expected_view_revision:snapshot.view_revision};
        if(!ViewActionReceipts.begin(owner)) return false;
        const accepted=ViewActionReceipts.accept({type:'action_result',...owner,call_id:'before-quantity-edit-read',
          outcome:'applied',effect:'read_only',resulting_revision:snapshot.view_revision,
          payload:{authority:'untrusted_browser_projection',reported_result:snapshot.result,sources:snapshot.sources}});
        ViewActionReceipts.finish(owner);
        return accepted;
      }),'Prepare current evidence immediately before testing offer invalidation');
      assert.match(await page.locator('#voice-action-receipt').textContent(),/Reported winner: Harbor Pantry/);
      await page.locator('#input-pallets').fill('2');
      await page.locator('#input-pallets').dispatchEvent('input');
      assert.match(await page.locator('#voice-action-receipt').textContent(),/workspace changed.*current view/i,'Offer edits explain why a new evidence read is required');
      assert.doesNotMatch(await page.locator('#voice-action-receipt').textContent(),/Harbor Pantry|Technical details/,'Prior evidence and its raw details cannot remain under the updated offer');
      assert.equal(await page.locator('#voice-action-receipt details').count(),0,'Old evidence disclosures are removed');
      assert.equal((await state()).form.deadline_at,beforeRead.form.deadline_at,'A quantity edit invalidates fit without extending the donor deadline');
      await nav('recipients');
      assert.match(await page.locator('#recipient-list').innerText(),/Not evaluated/i);
      assert.equal((await state()).mapSame,true);
      await nav('match');
      await page.locator('#btn-match').click();
      await page.locator('#brief-container').waitFor({state:'visible'});
      assert.match(await page.locator('#workspace-clock').innerText(),/^Deadline ·/);
      const clockCheckCalls=apiCount();
      await page.locator('#edit-pickup-window').click();
      await page.locator('#input-hours').fill('2');
      await page.waitForFunction(()=>document.getElementById('workspace-clock').textContent==='2h pickup window');
      assert(await page.locator('#review-clock').evaluate(el=>el.hidden||el.classList.contains('hidden')),'Changing hours invalidates the previous fixed deadline display');
      assert.equal(await page.evaluate(()=>offerClock.deadline),null,'Changing hours does not silently start a new check clock');
      assert.equal(await page.evaluate(()=>currentPayload),null,'Changing hours invalidates the completed result');
      if(!await page.locator('#scenario-panel').evaluate(el=>el.open)) await page.locator('#scenario-panel > summary').click();
      await page.getByRole('button',{name:/Bakery surplus/}).click();
      const scenarioHours=Number(await page.locator('#input-hours').inputValue());
      await page.waitForFunction(hours=>document.getElementById('workspace-clock').textContent===`${hours}h pickup window`,scenarioHours);
      assert.equal(await page.evaluate(()=>offerClock.deadline),null,'A newly loaded scenario shows its window until a check establishes the deadline');
      assert(await page.locator('#review-clock').evaluate(el=>el.hidden||el.classList.contains('hidden')),'Scenario selection cannot display the previous fixed deadline');
      assert.equal(apiCount(),clockCheckCalls,'Hours edits and scenario selection never run matching or fetch sources');
      assert.equal(await page.evaluate(()=>window.qaMicrophoneRequests),0,'Workspace and assistant presentation never request microphone access');
      assert.doesNotMatch(await page.locator('body').innerText(),/gods?-eye-view|god.?s eye view/i,'Product copy has no inspiration-repository branding');
      console.log(`Workspace browser QA passed at ${width}px.`);
      await page.locator('.workspace-topbar [data-open-assistant]').click();
      const beforeRecovery=await state(), recoveryCallsBefore=apiCount();
      // Browser-level handler recovery with an explicit fixture transport, never a live provider.
      await page.evaluate(()=>{
        window.recoveryFixture={sockets:[]};
        window.WebSocket=class {
          constructor(){this.readyState=1;this.bufferedAmount=0;this.sent=[];recoveryFixture.sockets.push(this);
            setTimeout(()=>this.emit({type:'ready',provider_epoch:1,audio_activity:true,input_correlation:true,max_recoveries:3}),0);}
          send(raw){this.sent.push(typeof raw==='string'?JSON.parse(raw):raw);}
          emit(value){this.onmessage?.({data:JSON.stringify(value)});}
          close(){this.readyState=3;this.closed=true;}
        };
        voice.enabled=true;voice.config={max_session_seconds:180};refreshVoiceControls();
        document.getElementById('voice-panel').open=true;
      });
      await page.locator('#voice-text').fill('Explain the displayed profile');await page.locator('#voice-send').click();
      await page.waitForFunction(()=>recoveryFixture.sockets[0]?.sent.some(item=>item.type==='text'));
      await page.evaluate(()=>{
        const socket=recoveryFixture.sockets[0],view=ViewContext.snapshot();
        recoveryFixture.old={turn_id:'browser-old',provider_epoch:1,request_id:socket.sent.find(item=>item.type==='text').request_id,
          expected_offer_revision:view.offer_revision,expected_view_revision:view.view_revision};
        socket.emit({type:'turn_started',...recoveryFixture.old});
        socket.emit({type:'transcript',...recoveryFixture.old,role:'model',text:'Initial commentary.'});
      });
      await page.locator('#voice-text').fill('Correction: explain what still needs confirmation');await page.locator('#voice-send').click();
      assert.equal(await page.evaluate(()=>recoveryFixture.sockets.length),1);
      await page.evaluate(()=>{
        const socket=recoveryFixture.sockets[0];
        socket.emit({type:'recovering',provider_epoch:2,reason:'text_correction',recovery_count:1});
        socket.emit({type:'transcript',...recoveryFixture.old,role:'model',text:'STALE COMMENTARY'});
      });
      assert.match(await page.locator('#voice-status').innerText(),/Updating the live session/);
      assert.equal(await page.locator('#voice-stop').isEnabled(),true);
      assert.equal(await page.locator('#voice-send').innerText(),'Queue correction');
      if(output) await page.screenshot({path:path.join(output,`recovery-${width}.png`),fullPage:false});
      await page.evaluate(()=>{
        const socket=recoveryFixture.sockets[0],view=ViewContext.snapshot();socket.emit({type:'resumed',provider_epoch:2,recovery_count:1});
        const fresh={turn_id:'browser-new',provider_epoch:2,request_id:socket.sent.filter(item=>item.type==='text').at(-1).request_id,
          expected_offer_revision:view.offer_revision,expected_view_revision:view.view_revision};
        socket.emit({type:'turn_started',...fresh});socket.emit({type:'turn_complete',...recoveryFixture.old});
        socket.emit({type:'transcript',...fresh,role:'model',text:'Confirm the recipient capacity and storage.'});
        socket.emit({type:'turn_complete',...fresh});
      });
      assert.doesNotMatch(await page.locator('#voice-transcript').innerText(),/STALE COMMENTARY/);
      assert.match(await page.locator('#voice-transcript').innerText(),/Confirm the recipient capacity/);
      assert.equal(await page.evaluate(()=>buildRequest().deadline_at),beforeRecovery.form.deadline_at);
      assert.equal(apiCount(),recoveryCallsBefore,'Recovery cannot initiate matching or source fetches');
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await page.locator('#voice-stop').click();assert.equal(await page.evaluate(()=>recoveryFixture.sockets[0].closed),true);
    }
    assert.deepEqual(sockets,[],'The disabled Live fixture never opens a provider WebSocket');
    assert.deepEqual(errors,[],'Browser JS must not throw');
    console.log('Browser QA passed: 1440/1024/768/390/320, workspace navigation, preserved map/form/result/deadline, recipient assistant suspend/resume, inert/focus/Escape, readable evidence, no extra API calls or overflow, typed correction recovery and terminal Stop.');
  } finally {await browser.close();}
}
if (serveOnly) {
  const port = Number(process.env.QA_PORT || 8769);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('QA_PORT must be a valid local port.');
  server.on('error', error => { console.error(error); process.exitCode = 1; });
  server.listen(port, '127.0.0.1', () => console.log(`Local review · sample data · no live checks: http://127.0.0.1:${port}`));
} else {
  run().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
}
