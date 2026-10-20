// Offline DOM simulation of the actual served script; no network or browser automation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../agent/api.py'), 'utf8');
const script = source.split('  <!-- Leaflet & App JS -->')[1].split('<script>')[1].split('</script>')[0];
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.value=''; this.textContent=''; this.className=''; this.classList={add(){},remove(){}}; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children=children; }
  set innerHTML(_) { throw new Error('Dynamic innerHTML is forbidden'); }
  get innerText() { return this.textContent; }
  removeAttribute(name) { delete this[name]; }
}
const nodes = new Map();
const context = vm.createContext({
  document: {createElement: tag => new Element(tag), getElementById: id => {if(!nodes.has(id)) nodes.set(id,new Element('div')); return nodes.get(id);}},
  window: {}, console, Date, Number, JSON, navigator: {},
});
vm.runInContext(script, context);
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
  layerGroup:()=>({addTo(){return this;}}),
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
