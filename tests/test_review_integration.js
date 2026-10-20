// Execute the actual application adapter with the real review/intake/clock modules offline.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../agent/api.py'),'utf8');
const script=source.split('  <!-- Leaflet & App JS -->')[1].split('<script>')[1].split('</script>')[0];
class Element {
  constructor(){this.children=[];this.value='';this.textContent='';this.attributes={};const set=new Set();this.classList={add:x=>set.add(x),remove:x=>set.delete(x),contains:x=>set.has(x),toggle:(x,on)=>on?set.add(x):set.delete(x)};}
  appendChild(x){this.children.push(x);return x;} replaceChildren(...x){this.children=x;}
  setAttribute(k,v){this.attributes[k]=v;}getAttribute(k){return this.attributes[k];}removeAttribute(k){delete this.attributes[k];}
  addEventListener(){} contains(x){return this===x||this.children.some(n=>n.contains(x));}querySelectorAll(){return [];}
  focus(){} scrollIntoView(){}
}
const nodes=new Map(),copies=[];
const document={createElement:()=>new Element(),getElementById:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);}};
const context=vm.createContext({document,window:{},console,Date,Number,navigator:{clipboard:{writeText:async text=>copies.push(text)}},TextDecoder,AbortController,setTimeout,clearTimeout,Response});
for(const name of ['offer-workflow','alert-intake','intake-review'])vm.runInContext(fs.readFileSync(path.join(__dirname,'../agent/ui',name+'.js'),'utf8'),context);
vm.runInContext(script,context);
const run=code=>vm.runInContext(code,context),field=(name,value)=>document.getElementById('input-'+name).value=value;
async function main(){
  field('donor','Market');field('text','3 pallets cold milk, collect in 2 hours');field('pallets','9');field('storage','ambient');field('hours','24');
  context.initializeFoodReview();context.refreshDeadlineDisplay();
  assert.equal(run('offerClock.deadline'),null,'Creating and refreshing review never establishes a clock');
  const deadline=new Date(Date.now()+7200000).toISOString();
  const draft={status:'review_required',requires_review:true,fields:{posting_text:nodes.get('input-text').value,pallet_count:3,required_storage:'cold',deadline_at:deadline},food_terms:['milk'],questions:[],quoted_evidence:{pallet_count:'3 pallets',required_storage:'cold',pickup_deadline:'2 hours'}};
  context.fetch=async()=>new Response(JSON.stringify(draft),{status:200});
  await context.analyzeSurplusAlert();
  assert.equal(run('foodReview.state().pending'),true,'Loading clear must preserve the extraction ticket');
  assert.equal(context.applySurplusAlert(),false,'Supported donor statements require explicit review');
  assert.equal(nodes.get('input-pallets').value,'9');
  for(const id of ['food_terms:0','pallet_count','required_storage','deadline_at'])run(`foodReview.acknowledgeFact(${JSON.stringify(id)},true)`);
  assert.equal(context.applySurplusAlert(),true);
  assert.equal(run('offerClock.deadline'),deadline,'Apply retains the exact donor clock');
  assert.equal(Number(nodes.get('input-pallets').value),3);
  assert(run('foodReview.state().appliedFieldIds').includes('deadline_at'),'Receipt survives host offerChanged during apply');
  run('matchInFlight=true');context.lockOffer(true);assert.equal(run('foodReview.canApply()'),false);run('matchInFlight=false');context.lockOffer(false);
  context.payload={status:'success',generation_mode:'adk_gemini',donor:{deadline_at:deadline},winner:{recipient_id:1,name:'Test Pantry',constraint_status:'eligible',profile_source:'generated'},runners_up:[],match_brief:{drafted_sms_outreach:'Please confirm pickup.',drafted_email_outreach:'Unsent email.'}};
  run('currentPayload=JSON.parse(JSON.stringify(payload)); currentPayload.donor={...reviewSnapshot().offer,name:reviewSnapshot().offer.donor_name,latitude:reviewSnapshot().offer.donor_lat,longitude:reviewSnapshot().offer.donor_lon}');context.refreshFoodReview();
  assert.equal(await context.copyMatchBrief(),false);assert.equal(copies.length,0,'Unreviewed handoff cannot reach clipboard');
  assert.equal(run('foodReview.reviewResult(true)'),true);assert.equal(await context.copyOutreach(),true);
  assert.match(copies[0],/UNSENT HANDOFF DRAFT/);assert.match(copies[0],/Please confirm pickup/);assert.match(copies[0],/Available capacity: Unconfirmed/);
  field('donor','Changed donor');context.offerChanged();assert.equal(await context.copyEmail(),false);assert.equal(copies.length,1,'An offer edit invalidates every legacy copy entry');
  // Unknown-only extraction must clear example defaults without inventing fields.
  field('text','Food available, please ask me');field('pallets','8');field('storage','cold');context.offerChanged('hours');
  const unknown={status:'review_required',requires_review:true,fields:{posting_text:nodes.get('input-text').value,pallet_count:null,required_storage:null,deadline_at:null},food_terms:[],questions:['How many pallets?'],quoted_evidence:{}};
  context.fetch=async()=>new Response(JSON.stringify(unknown),{status:200});await context.analyzeSurplusAlert();
  assert.equal(context.applySurplusAlert(),true);assert.equal(nodes.get('input-pallets').value,'');assert.equal(nodes.get('input-storage').value,'');assert.equal(run('offerClock.deadline'),null);
  // Review refresh is composed before the Live callback's early return.
  assert.match(script,/ViewContext\.onChange\(\(\) => \{\s*refreshFoodReview\(\);\s*if \(!voice\.ready/);
  assert.match(source,/@app.get\("\/ui\/intake-review.js"/);assert.match(source,/<script src="\/ui\/intake-review.js"><\/script>/);
  console.log('Review integration: pure clock reads, extraction ownership, explicit review, post-apply receipt, lock, stale copy guards and unknown-only clearing passed.');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
