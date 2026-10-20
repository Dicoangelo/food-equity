const {test} = require('node:test');
const assert = require('node:assert/strict');
const {create} = require('../agent/ui/alert-intake.js');

test('late extraction cannot overwrite edited form; apply is explicit and single use', async () => {
  let text='2 pallets spinach pickup within 3 hours', respond, shown, applied=0;
  const c=create({
    snapshot:()=>({posting_text:text,locked:false,deadline_at:null}),
    request:()=>new Promise(resolve=>{respond=resolve;}),
    show:(draft)=>{shown=draft;}, apply:()=>{applied++;},
  });
  const result=()=>({ok:true,text:async()=>JSON.stringify({
    status:'review_required',requires_review:true,fields:{posting_text:text},questions:[],
  })});
  const pending=c.analyze();
  c.invalidate();
  text='new offer';
  respond(result());
  await pending;
  assert.equal(shown,null);
  assert.equal(c.applyReviewed(),false);
  const second=c.analyze();
  respond(result());
  await second;
  assert(shown);
  assert.equal(applied,0);
  assert.equal(c.applyReviewed(),true);
  assert.equal(c.applyReviewed(),false);
  assert.equal(applied,1);
});

test('repeated extraction preserves earliest proposed clock; failure leaves form unchanged',async()=>{
  const deadline=new Date(Date.now()+3600000).toISOString();
  const requests=[];
  let applied=0, count=0, shown;
  const c=create({
    snapshot:()=>({posting_text:'spinach for pickup',locked:false,deadline_at:null}),
    show:(draft,message)=>{shown={draft,message};}, apply:()=>{applied++;},
    request:async(_,options)=>{
      requests.push(JSON.parse(options.body));
      if(++count===3) throw new Error('private details');
      return {ok:true,text:async()=>JSON.stringify({status:'review_required',requires_review:true,
        fields:{posting_text:'spinach for pickup',deadline_at:deadline},questions:[]})};
    },
  });
  await c.analyze();
  c.invalidate(); // A non-clock form edit must retain this posting's pending anchor.
  await c.analyze();
  assert.equal(requests[1].deadline_at,deadline);
  await c.analyze();
  assert.equal(shown.draft,null);
  assert(!shown.message.includes('private'));
  assert.equal(applied,0);
});

test('missing quantities remain review data and parsing never calls matching', async () => {
  let shown, applied, locked=false;
  const requested=[];
  const c=create({snapshot:()=>({posting_text:'Some spinach is available',locked,deadline_at:null}),
    show:value=>{shown=value;},apply:value=>{applied=value;},
    request:async(url,options)=>{requested.push({url,body:JSON.parse(options.body)});return {ok:true,text:async()=>JSON.stringify({
      status:'review_required',requires_review:true,fields:{posting_text:'Some spinach is available'},
      questions:['Confirm pallet quantity and storage.'],food_terms:['spinach'],
    })};},
  });
  await c.analyze();
  assert.equal(applied,undefined);assert.equal(shown.fields.pallet_count,undefined);assert.equal(shown.fields.required_storage,undefined);
  assert.deepEqual(requested.map(item=>item.url),['/api/intake/parse']);
  assert(c.applyReviewed());assert.equal(applied.fields.pallet_count,undefined);
  locked=true;await c.analyze();assert.equal(requested.length,1,'Matching locks out new extraction requests');
});

test('a response arriving during matching cannot apply or start another match', async () => {
  let locked=false,respond,applied=0,shown;
  const c=create({snapshot:()=>({posting_text:'Two pallets of chilled spinach',locked,deadline_at:null}),
    request:()=>new Promise(resolve=>{respond=resolve;}),show:value=>{shown=value;},apply:()=>applied++});
  const pending=c.analyze();locked=true;
  respond({ok:true,text:async()=>JSON.stringify({status:'review_required',requires_review:true,
    fields:{posting_text:'Two pallets of chilled spinach',required_storage:'cold',pallet_count:2},questions:[]})});
  await pending;assert(shown);assert.equal(c.applyReviewed(),false);assert.equal(applied,0);
  locked=false;assert.equal(applied,0,'Unlocking never applies a pending result automatically');
  assert.equal(c.applyReviewed(),true);assert.equal(applied,1);
});

test('edited and superseded extraction aborts transport and cannot overwrite newer review',async()=>{
  let text='Original spinach offer',shown,applied=0;
  const waiting=[];
  const c=create({snapshot:()=>({posting_text:text,locked:false,deadline_at:null}),show:value=>{shown=value;},apply:()=>applied++,
    request:(_,options)=>new Promise(resolve=>waiting.push({options,resolve}))});
  const old=c.analyze();text='Replacement bread offer';c.invalidate();
  assert(waiting[0].options.signal.aborted);
  const current=c.analyze();
  const response=posting_text=>({ok:true,text:async()=>JSON.stringify({status:'review_required',requires_review:true,fields:{posting_text},questions:[]})});
  waiting[1].resolve(response(text));await current;assert.equal(shown.fields.posting_text,text);
  waiting[0].resolve(response('Original spinach offer'));await old;assert.equal(shown.fields.posting_text,text);
  assert.equal(applied,0);
});

test('earliest form or pending deadline is forwarded and explicit clock reset releases it',async()=>{
  const early=new Date(Date.now()+3600000).toISOString(),later=new Date(Date.now()+7200000).toISOString();
  let formClock=later;
  const inputs=[];
  const c=create({snapshot:()=>({posting_text:'Two pallets spinach pickup in one hour',locked:false,deadline_at:formClock}),
    show(){},apply(){},request:async(_,options)=>{inputs.push(JSON.parse(options.body));return {ok:true,text:async()=>JSON.stringify({
      status:'review_required',requires_review:true,fields:{posting_text:'Two pallets spinach pickup in one hour',deadline_at:early},questions:[]})};}});
  await c.analyze();assert.equal(inputs[0].deadline_at,later);
  c.invalidate();await c.analyze();assert.equal(inputs[1].deadline_at,early);
  c.invalidate(true);formClock=null;await c.analyze();assert.equal(inputs[2].deadline_at,null);
});

test('programmatic posting replacement cannot carry a previous draft clock into a new scenario',async()=>{
  let posting='Original chilled spinach',formClock=null;
  const deadline=new Date(Date.now()+600000).toISOString(),inputs=[];
  const c=create({snapshot:()=>({posting_text:posting,locked:false,deadline_at:formClock}),show(){},apply(){},
    request:async(_,options)=>{const input=JSON.parse(options.body);inputs.push(input);return {ok:true,text:async()=>JSON.stringify({
      status:'review_required',requires_review:true,fields:{posting_text:input.posting_text,...(inputs.length===1?{deadline_at:deadline}:{})},questions:[]})};}});
  await c.analyze();
  c.invalidate(); // A scenario may invalidate its old display before replacing form text.
  posting='New ambient bread scenario';
  await c.analyze();
  assert.equal(inputs[1].deadline_at,null,'A pending deadline belongs only to the alert that established it');
});
