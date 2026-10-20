// Exercise the actual WebSocket UI handlers with owned fixture messages. No provider calls.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {context,explorer,node,content,city,match,setPayloads,fixedRequest}=require('./test_recipient_explorer.js');
const view=vm.runInContext('ViewContext',context);
const sockets=[];
context.location={protocol:'https:',host:'fixture.invalid'};
context.WebSocket=class {
  constructor(url){this.url=url;this.readyState=1;this.sent=[];sockets.push(this);}
  send(raw){this.sent.push(typeof raw==='string'?JSON.parse(raw):raw);}
  emit(message){this.onmessage?.({data:JSON.stringify(message)});}
  close(){this.readyState=3;this.closed=true;}
};
// Install the real onChange handler without running startup data fetches.
for(const name of ['initMap','loadCityData','loadScenarios','loadVoiceConfig'])context[name]=()=>{};
context.window.onload();
context.applyScenario(fixedRequest);
setPayloads(city,{...match,donor:{...match.donor,deadline_at:new Date(Date.now()+3600000).toISOString()}});
view.changed('result');
vm.runInContext('voice.enabled=true;voice.config={max_session_seconds:180};',context);
const owner=turn=>{const snapshot=view.snapshot();return {turn_id:turn,
  expected_offer_revision:snapshot.offer_revision,expected_view_revision:snapshot.view_revision};};
async function connect() {
  const pending=context.startVoiceSession(false);
  const socket=sockets.at(-1);
  socket.emit({type:'ready'});
  assert.equal(await pending,true);
  assert.deepEqual(socket.sent.slice(0,2).map(message=>message.type),['view_context','context']);
  return socket;
}
async function run() {
  const original=context.buildRequest();
  let socket=await connect();
  const baselineView=socket.sent[0].context;
  assert.equal(baselineView.result.winner_id,'1');
  assert.equal(socket.sent[1].fields.deadline_at,original.deadline_at);
  socket.emit({type:'view_context_ack',status:'accepted',
    offer_revision:baselineView.offer_revision,view_revision:baselineView.view_revision});
  const initialDraft={type:'draft',context_only:true,draft_id:'transport-draft',draft_version:0,
    ready_for_review:true,missing_fields:[],fields:{...original}};
  socket.emit(initialDraft);
  socket.emit({type:'turn_complete',context_only:true});
  node('voice-text').value='Why this recipient?';
  await context.sendVoiceText();
  assert.equal(socket.sent.at(-1).type,'text');
  assert.equal(node('voice-send').disabled,true,'Send is disabled while awaiting owned output');
  socket.emit({type:'turn_complete',context_only:true});
  assert.equal(node('voice-send').disabled,true,'A context-only receipt cannot complete a text turn');
  let turn=owner('turn-one');
  socket.emit({type:'turn_started',...turn});
  socket.emit({type:'transcript',role:'model',text:'Unowned text must be ignored'});
  assert.doesNotMatch(content(node('voice-transcript')),/Unowned/);
  socket.emit({type:'transcript',role:'model',text:'Commentary only.',...turn});
  assert.match(content(node('voice-transcript')),/Commentary only/);
  socket.emit({type:'action_result',call_id:'read-transport',...turn,outcome:'applied',effect:'read_only',
    resulting_revision:turn.expected_view_revision,payload:{authority:'untrusted_browser_projection',
      reported_result:{winner_id:'1',status:'success'}}});
  assert.match(content(node('voice-action-receipt')),/Display evidence read/);
  assert.equal(context.buildRequest().deadline_at,original.deadline_at);
  const sends=socket.sent.length;
  node('voice-text').value='Second simultaneous turn';
  await context.sendVoiceText();
  assert.equal(socket.sent.length,sends,'The client cannot overlap text turns');
  socket.emit({type:'turn_complete',...turn});
  assert.equal(node('voice-send').disabled,false);
  socket.emit({type:'transcript',role:'model',text:'Late completed-turn text',...turn});
  assert.doesNotMatch(content(node('voice-transcript')),/Late completed/);

  node('input-pallets').value='2';
  context.offerChanged();
  assert.equal(socket.closed,true,'Editing the offer closes even an idle Live session');
  assert.equal(node('voice-apply').disabled,true,'An offer edit invalidates the old draft proposal');
  socket=await connect();
  assert.equal(socket.sent[1].fields.pallet_count,2,'Restart captures the updated visible form');
  assert.equal(socket.sent[1].fields.deadline_at,original.deadline_at,'Quantity edits do not restart the pickup clock');
  node('voice-text').value='Explain the profile';
  await context.sendVoiceText();
  turn=owner('turn-two');
  socket.emit({type:'turn_started',...turn});
  const oldHandler=socket.onmessage;
  explorer.select('2');
  assert.equal(socket.closed,true,'Changing the selected view cancels a live turn');
  assert.match(node('voice-status').textContent,/view changed/);
  oldHandler({data:JSON.stringify({type:'action_result',call_id:'too-late',...turn,outcome:'applied',
    effect:'draft_proposed',resulting_revision:2,payload:{draft:{...initialDraft,draft_version:2,
      fields:{...original,donor_name:'Must not apply'}}}})});
  assert.notEqual(node('input-donor').value,'Must not apply');
  assert.equal(context.buildRequest().deadline_at,original.deadline_at);
  assert.equal(node('voice-send').disabled,false,'Typed fallback remains available after stopping');

  socket=await connect();
  const secondView=socket.sent[0].context;
  assert.equal(secondView.selected_recipient_id,'2');
  node('voice-text').value='Read the new profile';
  await context.sendVoiceText();
  turn=owner('turn-three');
  socket.emit({type:'turn_started',...turn});
  socket.emit({type:'interrupted',...turn});
  assert.equal(socket.closed,true,'Interruption releases the session and requires a fresh turn');
  assert.match(node('voice-status').textContent,/interrupted/);
  view.dispose();
  console.log('View transport: context ordering, owned commentary/actions, no overlapping sends, view-change cancellation, late-output rejection and unchanged exact deadline passed.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
