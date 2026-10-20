// Optional real-browser QA: NODE_PATH=<playwright package root> node this-file.cjs
// Uses an isolated browser and local fixture server. Never calls the live broker.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.join(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'agent/api.py'), 'utf8');
const html = source.split('html_content = r"""')[1].split('"""')[0];
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
  if(req.url === '/') {res.setHeader('content-type','text/html');return res.end(html);}
  if(req.url.startsWith('/ui/')) {
    const name=req.url.slice('/ui/'.length);
    if(!['recipient-explorer.js','recipient-explorer.css','offer-workflow.js','source-state.js','view-context.js'].includes(name)) {res.writeHead(404);return res.end();}
    res.setHeader('content-type',name.endsWith('.js')?'text/javascript':'text/css');
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
        donor:{name:offer.donor_name,latitude:offer.donor_lat,longitude:offer.donor_lon,
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
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch(process.env.CHROME_PATH ? {executablePath:process.env.CHROME_PATH} : {});
  try {
    const page=await browser.newPage();
    const errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    const output=process.env.QA_OUTPUT_DIR;
    if(output) fs.mkdirSync(output,{recursive:true});
    for(const width of [1440,390,320]) {
      await page.setViewportSize({width,height:900});
      await page.goto(`http://127.0.0.1:${server.address().port}/`);
      await page.waitForFunction(()=>document.getElementById('recipient-list').textContent.includes('Harbor Pantry'));
      await page.waitForFunction(()=>typeof L !== 'undefined' && document.querySelector('.leaflet-container'));
      await page.getByRole('button',{name:/Bakery surplus/}).click();
      assert.equal(await page.locator('#input-pallets').inputValue(),'1');
      const countBefore=matchCalls;
      const first=page.locator('#recipient-list button').filter({hasText:'Harbor Pantry'}).first();
      await first.click();
      await page.locator('#recipient-detail').waitFor({state:'visible'});
      assert.match(await page.locator('#recipient-detail-content').innerText(),/generated|Generated/);
      assert.match(await page.locator('#recipient-detail-content').innerText(),/Unknown/);
      assert.match(await page.locator('#recipient-detail-content').innerText(),/Profile evidence: ready.*operations unconfirmed/s);
      assert.match(await page.locator('#source-state-summary').innerText(),/Recipient dataset: ready/);
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
      await page.locator('#btn-match').click();
      await page.locator('#brief-container').waitFor({state:'visible'});
      await page.locator('#inspect-winner').click();
      const detail=await page.locator('#recipient-detail-content').innerText();
      assert.match(detail,/0\.6475|64\.75/);
      assert.match(detail,/15 min|0\.25/);
      await page.getByText('How this offer-fit score is built',{exact:true}).click();
      assert.match(await page.locator('#recipient-detail-content').innerText(),/rounded|rounding/i);
      assert(await page.locator('#recipient-detail').evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Drawer must not overflow horizontally');
      if(width<=390) {
        assert.equal(await page.locator('#recipient-detail').getAttribute('aria-modal'),'true');
        await page.locator('#recipient-close').focus();
        await page.keyboard.press('Shift+Tab');
        assert(await page.locator('#recipient-detail').evaluate(el=>el.contains(document.activeElement)),'Mobile sheet traps keyboard focus');
      }
      await page.locator('#recipient-detail').evaluate(el=>el.scrollTop=0);
      if(output) await page.screenshot({path:path.join(output,`profile-${width}.png`),fullPage:false});
      await page.locator('#recipient-close').click();
      await page.locator('#recipient-compare summary').click();
      const compareButton=page.locator('#recipient-compare button').nth(1);
      await compareButton.click();
      assert.equal(await page.locator('#recipient-detail-title').innerText(),'Community Kitchen');
      await page.locator('#recipient-close').click();
      assert(await compareButton.evaluate(el=>el===document.activeElement),'Closing comparison profile restores its opener');
      await page.locator('#outcome-filter').selectOption('needs_confirmation');
      assert.equal(await page.locator('#recipient-list button').count(),1);
      assert.equal(await page.locator('#map [role="button"][aria-controls="recipient-detail"]').count(),1,'Evaluated map pins obey outcome filters');
      await page.locator('#recipient-list button').first().click();
      assert.match(await page.locator('#recipient-detail-content').innerText(),/acceptance_confirmation_required/);
      assert.doesNotMatch(await page.locator('#recipient-detail-content').innerText(),/0\.9900|99\.00/);
      await page.locator('#recipient-close').click();
      await page.locator('#outcome-filter').selectOption('all');
      await page.locator('#fit-recipient-view').click();
      if(output) {
        await page.locator('#map-panel').scrollIntoViewIfNeeded();
        await page.locator('.recipient-browser').screenshot({path:path.join(output,`directory-${width}.png`)});
      }
      assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`No page overflow at ${width}px`);
      const apiCallsBefore=requests.filter(url=>url.startsWith('/api/')).length;
      await page.locator('#recipient-list button').filter({hasText:'Community Kitchen'}).first().click();
      assert.equal(requests.filter(url=>url.startsWith('/api/')).length,apiCallsBefore,'Profile selection must reuse loaded data');
      await page.locator('#recipient-close').click();
      const beforeRead=await page.evaluate(()=>buildRequest().deadline_at);
      await page.evaluate(()=>{
        const snapshot=ViewContext.snapshot();
        const owner={turn_id:'browser-fixture',expected_offer_revision:snapshot.offer_revision,expected_view_revision:snapshot.view_revision};
        ViewActionReceipts.begin(owner);
        ViewActionReceipts.accept({type:'action_result',...owner,call_id:'fixture-read',
          outcome:'applied',effect:'read_only',resulting_revision:snapshot.view_revision,
          payload:{authority:'untrusted_browser_projection',reported_result:snapshot.result,sources:snapshot.sources}});
        ViewActionReceipts.finish(owner);
        document.getElementById('voice-panel').open=true;
      });
      const receipt=page.locator('#voice-action-receipt');
      assert.match(await receipt.innerText(),/Reported winner: Harbor Pantry/);
      assert.match(await receipt.innerText(),/availability unconfirmed/);
      assert.match(await receipt.innerText(),/Confirm current capacity and recipient acceptance/);
      assert.match(await receipt.innerText(),/Source status: partial/);
      assert.equal(await receipt.locator('details').last().getAttribute('open'),null,'Technical JSON is optional, not the primary answer');
      assert.equal(await page.evaluate(()=>buildRequest().deadline_at),beforeRead,'Evidence reads never extend the donor clock');
      assert.equal(requests.filter(url=>url.startsWith('/api/')).length,apiCallsBefore,'Evidence reads reuse loaded data');
      await receipt.scrollIntoViewIfNeeded();
      assert(await receipt.evaluate(el=>el.scrollWidth<=el.clientWidth+1),'Evidence receipt fits the phone viewport');
      if(output) await page.screenshot({path:path.join(output,`evidence-${width}.png`),fullPage:false});
      await page.locator('#input-pallets').fill('2');
      await page.locator('#input-pallets').dispatchEvent('input');
      assert.equal(await page.evaluate(()=>buildRequest().deadline_at),beforeRead,'A quantity edit invalidates fit without extending the donor deadline');
      assert.match(await page.locator('#recipient-list').innerText(),/Not evaluated/i);
    }
    assert.deepEqual(errors,[],'Browser JS must not throw');
    console.log('Browser QA passed: desktop 1440, phone 390/320, linked list/profile, filters, focus/Escape, mobile trap, offer invalidation, no extra API calls, no overflow.');
  } finally {await browser.close();}
}
run().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>server.close());
