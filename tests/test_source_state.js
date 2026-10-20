const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const context=vm.createContext({Date,Number,console});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../agent/ui/source-state.js'),'utf8'),context);
const source=vm.runInContext('SourceStateUI',context);
const states=['ready','partial','stale','unavailable'];
for(const first of states) for(const second of states) {
  const expected=first==='stale'||second==='stale'?'stale':first==='unavailable'&&second==='unavailable'?'unavailable':first==='ready'&&second==='ready'?'ready':'partial';
  assert.equal(source.aggregate([first,second]),expected,`${first}+${second}`);
}
assert.equal(source.aggregate([]),'unavailable');
assert.equal(source.aggregate(['ready','unsupported']),'partial');
const raw={source_id:'city:recipients',source_kind:'city_recipients',status:'ready',provenance:'generated',
  fetched_at:'2026-10-20T12:00:00Z',expires_at:'2026-10-20T12:05:00Z',cache_state:'hit',
  source_observed_at:'2026-10-19T12:00:00Z',operational_confirmed_at:'2026-10-20T12:00:00Z',
  coverage:{returned:119,matching_source_rows:119,row_cap:250,truncated:false,skipped_invalid_or_duplicate:0}};
const fresh=source.normalize(raw,Date.parse('2026-10-20T12:04:00Z'));
assert.equal(fresh.status,'ready');
assert.equal(fresh.source_observed_at,null,'Browser timestamps cannot assert source observation');
assert.equal(fresh.operational_confirmed_at,null,'A fresh fetch cannot assert operational confirmation');
assert.equal(fresh.provenance,'generated');
const stale=source.normalize(raw,Date.parse('2026-10-20T12:05:00Z'));
assert.equal(stale.status,'stale');
assert.equal(stale.cache_state,'expired');
assert.equal(stale.error_code,'snapshot_expired');
assert.equal(source.normalize({...raw,status:'unavailable'},Date.parse('2026-10-20T13:00:00Z')).status,'unavailable');
assert.equal(source.normalize({...raw,provenance:'verified_source'}).provenance,'unknown');
assert.equal(source.normalize({...raw,error_code:'SECRET_RAW_ERROR'},Date.parse('2026-10-20T12:04:00Z')).error_code,null);
assert.equal(source.cityStates({sources:Array(20).fill(raw)}).length,8);
assert.equal(source.cityStates({status:'ready',recipients:[{}]}).length,0,'No invented source envelope for legacy data');
assert.match(source.describe(stale),/stale.*operations unconfirmed/);
assert.match(source.describe({...raw,source_kind:'match_result'}),/Evaluation validity ends at the donor deadline.*not a source observation timestamp/);
assert.equal(raw.status,'ready','Normalization must not mutate its source input');
console.log('Source state: exhaustive mixed states, expiry, provenance, bounded coverage and unknown observation/confirmation passed.');
