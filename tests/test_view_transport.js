// Actual WebSocket UI handlers, microphone worklet callbacks and owner state. No provider calls.
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {context,explorer,node,content,city,match,setPayloads,fixedRequest}=require('./test_recipient_explorer.js');
const view=vm.runInContext('ViewContext',context),receipts=vm.runInContext('ViewActionReceipts',context);
const sockets=[],timers=new Map(),audioContexts=[],streams=[],worklets=[],revoked=[];let timerId=0,micRequests=0;
Object.assign(context,{ArrayBuffer,Uint8Array,DataView,Int16Array,Float32Array,Blob,atob});
context.location={protocol:'https:',host:'fixture.invalid'};
context.setTimeout=(fn,ms)=>{timers.set(++timerId,{fn,ms});return timerId;};
context.clearTimeout=id=>timers.delete(id);
context.URL={createObjectURL:()=> 'blob:transport-worklet',revokeObjectURL:url=>revoked.push(url)};
context.window.AudioContext=class {
  constructor(){this.state='running';this.currentTime=0;this.destination={};this.audioWorklet={addModule:async()=>{}};audioContexts.push(this);}
  resume(){return Promise.resolve();} close(){this.state='closed';return Promise.resolve();}
  createMediaStreamSource(){return {connect(){},disconnect(){this.disconnected=true;}};}
  createBuffer(_,n,rate){return {duration:n/rate,getChannelData:()=>new Float32Array(n)};}
  createBufferSource(){this.lastSource={connect(){},disconnect(){},start(){this.started=true;},stop(){this.stopped=true;}};return this.lastSource;}
};
context.AudioWorkletNode=class {constructor(){this.port={};worklets.push(this);}connect(){}disconnect(){this.disconnected=true;}};
context.navigator.mediaDevices={getUserMedia:async()=>{micRequests++;const track={stop(){this.stopped=true;}};
  const stream={track,getTracks:()=>[track]};streams.push(stream);return stream;}};
context.WebSocket=class {
  constructor(url){this.url=url;this.readyState=1;this.bufferedAmount=0;this.sent=[];sockets.push(this);}
  send(raw){this.sent.push(typeof raw==='string'?JSON.parse(raw):raw);}
  emit(message){this.onmessage?.({data:JSON.stringify(message)});}
  close(){this.readyState=3;this.closed=true;}
};
for(const name of ['initMap','loadCityData','loadScenarios','loadVoiceConfig'])context[name]=()=>{};
context.window.onload();context.applyScenario(fixedRequest);
setPayloads(city,{...match,donor:{...match.donor,deadline_at:new Date(Date.now()+3600000).toISOString()}});view.changed('result');
vm.runInContext('voice.enabled=true;voice.config={max_session_seconds:180};',context);
const state=key=>vm.runInContext(`voice.${key}`,context);
const owner=(turn,epoch=state('providerEpoch'))=>{const snapshot=view.snapshot();return {turn_id:turn,provider_epoch:epoch,
  expected_offer_revision:snapshot.offer_revision,expected_view_revision:snapshot.view_revision};};
const tick=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
const note=async text=>{node('voice-text').value=text;await context.sendVoiceText();};
const pulse=(amplitude=2000,count=1)=>{for(let i=0;i<count;i++)worklets.at(-1).port.onmessage?.({data:new Int16Array(320).fill(amplitude).buffer});};
const audio=(turn,value='AAAAAA==')=>({type:'audio',...turn,pcm:value});
const startTurn=(socket,id)=>{const expected=state('expectedStart');const turn={...owner(id),[expected?.kind==='audio'?'activity_id':'request_id']:expected?.id};socket.emit({type:'turn_started',...turn});return turn;};
const recover=(socket,epoch,reason='interrupted')=>socket.emit({type:'recovering',reason,provider_epoch:epoch,recovery_count:epoch-1});
const resume=(socket,epoch)=>socket.emit({type:'resumed',provider_epoch:epoch,recovery_count:epoch-1});
const read=(turn,call='read-one')=>({type:'action_result',...turn,call_id:call,outcome:'applied',effect:'read_only',
  resulting_revision:turn.expected_view_revision,payload:{authority:'untrusted_browser_projection',reported_result:{winner_id:'1',status:'success'}}});
async function connect(microphone=false) {
  const pending=context.startVoiceSession(microphone);await tick();const socket=sockets.at(-1);
  socket.emit({type:'ready',provider_epoch:1,audio_activity:true,input_correlation:true,max_recoveries:3});
  assert.equal(await pending,true);
  assert.deepEqual(socket.sent.slice(0,2).map(message=>message.type),['view_context','context']);return socket;
}
async function run() {
  const original=context.buildRequest();let socket=await connect();
  assert.equal(socket.sent[0].context.result.winner_id,'1');assert.equal(socket.sent[1].fields.deadline_at,original.deadline_at);
  const initialDraft={type:'draft',context_only:true,draft_id:'transport-draft',draft_version:0,
    ready_for_review:true,missing_fields:[],fields:{...original}};
  socket.emit(initialDraft);
  await note('Why this recipient?');assert.equal(node('voice-send').disabled,false,'Corrections remain available during a turn');
  const turnA=startTurn(socket,'turn-A');
  socket.emit({type:'transcript',role:'model',text:'Unowned text must be ignored'});
  assert.doesNotMatch(content(node('voice-transcript')),/Unowned/);
  socket.emit({type:'transcript',role:'model',text:'Commentary A.',...turnA});socket.emit(audio(turnA));
  assert.match(content(node('voice-transcript')),/Commentary A/);
  const oldAudio=audioContexts.at(-1).lastSource;assert(oldAudio.started);
  socket.emit(read(turnA));assert.match(content(node('voice-action-receipt')),/Here is what the current view shows/);
  const beforeReceipt=content(node('voice-action-receipt'));

  // Typed B interrupts A immediately. Old callbacks are inert even before server recovery arrives.
  await note('Actually explain the selected profile.');assert.equal(socket.closed,undefined);assert(oldAudio.stopped);
  socket.emit({type:'transcript',...turnA,role:'model',text:'Late A before recovery'});
  socket.emit({type:'turn_complete',...turnA});assert.doesNotMatch(content(node('voice-transcript')),/Late A/);
  recover(socket,2,'text_correction');
  await note('First queued correction');await note('Latest queued correction');
  assert.equal(state('pendingText'),'Latest queued correction');
  const submittedB=socket.sent.filter(item=>item.type==='text').at(-1).request_id;
  const sendCount=socket.sent.length;resume(socket,2);
  const supersededB={...owner('superseded-B'),request_id:submittedB};
  socket.emit({type:'turn_started',...supersededB});socket.emit({type:'transcript',...supersededB,role:'model',text:'Superseded queued successor'});
  assert.doesNotMatch(content(node('voice-transcript')),/Superseded queued successor/);
  assert.equal(socket.sent.filter(item=>item.type==='text'&&item.text==='Latest queued correction').length,1);
  assert(!socket.sent.some(item=>item.text==='First queued correction'));
  assert(socket.sent.length>sendCount);const turnB=startTurn(socket,'turn-B');
  // Duplicate ID registry survives epoch replacement, including altered payloads.
  socket.emit(read(turnB));assert.equal(content(node('voice-action-receipt')),beforeReceipt);
  socket.emit({...read(turnB),payload:{authority:'untrusted_browser_projection',reported_recipient:{name:'Changed duplicate'}}});
  assert.doesNotMatch(content(node('voice-action-receipt')),/Changed duplicate/);
  socket.emit({type:'transcript',...turnA,role:'model',text:'Late A after B'});socket.emit(audio(turnA));
  socket.emit({type:'turn_complete',...turnA});assert(receipts.current(),'Late A cannot finish B');
  socket.emit({...read(turnA,'stale-draft'),effect:'draft_proposed',resulting_revision:2,payload:{draft:{...initialDraft,draft_version:2,fields:{...original,donor_name:'Stale donor'}}}});
  assert.equal(state('draftVersion'),0);assert.equal(node('input-donor').value,original.donor_name);
  socket.emit({type:'transcript',...turnB,role:'model',text:'Current B commentary.'});assert.match(content(node('voice-transcript')),/Current B/);
  socket.emit({type:'turn_complete',...turnB});assert.equal(receipts.busy(),false);
  assert.equal(context.buildRequest().deadline_at,original.deadline_at);

  // Map selection retires active work without closing the browser connection.
  await note('Explain the profile again');const turnC=startTurn(socket,'turn-C');
  explorer.select('2');assert.equal(socket.closed,undefined);assert.equal(state('awaitingRecovery'),true);
  context.flushVoiceView();assert.equal(socket.sent.at(-1).context.selected_recipient_id,'2');
  recover(socket,3,'view_changed');resume(socket,3);await note('Explain the new selected profile');const turnD=startTurn(socket,'turn-D');
  socket.emit({type:'transcript',...turnC,role:'model',text:'Old selected profile'});
  assert.doesNotMatch(content(node('voice-transcript')),/Old selected profile/);
  socket.emit(read(turnD,'selected-new'));socket.emit({type:'turn_complete',...turnD});
  // Provider interruption retires content before same-envelope siblings could be forwarded.
  await note('Next question');const turnE=startTurn(socket,'turn-E');
  socket.emit({type:'interrupted',...turnE});socket.emit(read(turnE,'same-envelope-tool'));
  socket.emit({type:'transcript',...turnE,role:'model',text:'Interrupted envelope text'});socket.emit(audio(turnE));
  assert.doesNotMatch(content(node('voice-transcript')),/Interrupted envelope/);
  recover(socket,4);resume(socket,4);await note('Valid next turn');const turnF=startTurn(socket,'turn-F');
  socket.emit({type:'transcript',...turnF,role:'model',text:'Valid next response'});assert.match(content(node('voice-transcript')),/Valid next response/);
  socket.emit({type:'turn_complete',...turnF});
  const lastHandler=socket.onmessage;
  node('input-pallets').value='2';context.offerChanged();assert.equal(socket.closed,true);
  assert.equal(node('voice-apply').disabled,true);lastHandler({data:JSON.stringify(read(turnF,'old-callback'))});
  assert.equal(context.buildRequest().deadline_at,original.deadline_at);

  // Completion won the server race: B is valid in the same epoch, with its own input token.
  socket=await connect();await note('Initial pending A');
  const pendingA={...owner('pending-A'),request_id:socket.sent.at(-1).request_id};
  await note('Correction B before A started');
  socket.emit({type:'turn_started',...pendingA});socket.emit({type:'transcript',...pendingA,role:'model',text:'Unseen old A start'});
  assert.doesNotMatch(content(node('voice-transcript')),/Unseen old A/);
  const sameEpochB=startTurn(socket,'same-epoch-B');assert.equal(state('awaitingRecovery'),false);
  socket.emit({type:'transcript',...sameEpochB,role:'model',text:'Same epoch valid B'});
  socket.emit({type:'interrupted',...pendingA});assert(receipts.current(),'A stale same-epoch interruption cannot cancel B');
  socket.emit({type:'turn_complete',...sameEpochB});assert.equal(receipts.busy(),false);
  await note('New A before completion race');startTurn(socket,'second-race-A');
  await note('New B already sent');await note('Newest C queued locally');
  const submittedSameEpochB=startTurn(socket,'second-race-B');
  assert.equal(socket.sent.at(-1).text,'Newest C queued locally','Queued C is released when same-epoch B proves no recovery was needed');
  assert.equal(state('pendingText'),null);assert.equal(state('awaitingRecovery'),true);
  socket.emit({type:'transcript',...submittedSameEpochB,role:'model',text:'Queued B must not appear'});
  assert.doesNotMatch(content(node('voice-transcript')),/Queued B must not appear/);
  recover(socket,2,'text_correction');resume(socket,2);const latestC=startTurn(socket,'latest-C');
  socket.emit({type:'turn_complete',...latestC});
  context.stopVoiceSession();

  // A view edit races server completion: accepted ACK must release only the local view wait.
  socket=await connect();await note('Read before view change');const viewRaceA=startTurn(socket,'view-race-A');
  socket.emit(read(viewRaceA,'view-receipt'));assert.match(content(node('voice-action-receipt')),/Here is what/);
  explorer.select('1');assert.match(content(node('voice-action-receipt')),/workspace changed.*current view/);
  const pendingRevision=state('pendingViewRevision');assert(Number.isInteger(pendingRevision));
  await note('Explain the newly selected profile');assert(state('pendingText'));
  socket.emit({type:'view_context_ack',status:'accepted',view_revision:pendingRevision-1,recovery_required:false});
  assert.equal(state('awaitingRecovery'),true,'An older ACK cannot release the current view retirement');
  socket.emit({type:'view_context_ack',status:'accepted',view_revision:pendingRevision,recovery_required:false});
  assert.equal(state('awaitingRecovery'),false);assert.equal(socket.sent.at(-1).text,'Explain the newly selected profile');
  const viewRaceB=startTurn(socket,'view-race-B');socket.emit({type:'turn_complete',...viewRaceA});assert(receipts.current());
  socket.emit({type:'turn_complete',...viewRaceB});context.stopVoiceSession();
  assert.match(content(node('voice-action-receipt')),/Session ended/);

  // One permission grant survives audio interruption and a second spoken turn.
  socket=await connect(true);assert.equal(micRequests,1);
  assert.equal(socket.sent[1].fields.pallet_count,2);assert.equal(socket.sent[1].fields.deadline_at,original.deadline_at);
  const lifetimeTimer=state('timer'),base=socket.sent.length;
  pulse(0,100);assert.equal(socket.sent.length,base,'Silence cannot start or overlap a provider turn');
  pulse(2000,3);let start=socket.sent.at(-1);assert.equal(start.type,'audio_start');assert.equal(start.activity_id,1);
  const speechA=startTurn(socket,'speech-A');socket.emit({type:'audio_ready',...speechA,activity_id:1});
  let packets=socket.sent.filter(item=>item instanceof ArrayBuffer);assert.equal(packets.length,3);
  const packet=new DataView(packets[0]);assert.equal(packet.getUint32(0),1);assert.equal(packet.getUint32(4),1);assert.equal(packets[0].byteLength,648);
  pulse(0,25);assert.equal(socket.sent.at(-1).type,'audio_end');
  socket.emit(audio(speechA));const previousOutput=audioContexts.at(-2).lastSource;
  // Spoken correction buffers onset while the server changes epochs.
  pulse(2000,3);start=socket.sent.at(-1);assert.equal(start.activity_id,2);assert(previousOutput.stopped);
  recover(socket,2,'audio_correction');pulse(2000,4);resume(socket,2);
  const speechB=startTurn(socket,'speech-B');socket.emit({type:'audio_ready',...speechB,activity_id:2});
  packets=socket.sent.filter(item=>item instanceof ArrayBuffer);assert.equal(new DataView(packets.at(-1)).getUint32(0),2);
  assert.equal(new DataView(packets.at(-1)).getUint32(4),2);
  const sentBytes=state('sentBytes');assert(sentBytes>3*640);assert.equal(state('timer'),lifetimeTimer,'Recovery does not reset lifetime');
  assert.equal(micRequests,1);assert(!streams.at(-1).track.stopped);
  socket.emit({type:'interrupted',...speechB});recover(socket,3);
  pulse(2000,50);resume(socket,3);pulse(2000,10);
  const activityCount=socket.sent.filter(item=>item.type==='audio_start').length;assert.equal(activityCount,2,'Leftover speech cannot silently begin another turn');
  pulse(0,25);pulse(2000,3);assert.equal(socket.sent.at(-1).activity_id,3);
  const speechC=startTurn(socket,'speech-C');socket.emit({type:'audio_ready',...speechC,activity_id:3});
  assert(state('sentBytes')>sentBytes);assert.equal(state('timer'),lifetimeTimer);assert.equal(micRequests,1);
  // Model completion may arrive before scheduled playback ends; a new onset still stops it.
  pulse(0,25);socket.emit(audio(speechC));const trailingAudio=audioContexts.at(-2).lastSource;
  socket.emit({type:'turn_complete',...speechC});assert.equal(receipts.busy(),false);
  pulse(2000,3);assert(trailingAudio.stopped,'New speech stops completed-turn playback too');
  const speechD=startTurn(socket,'speech-D');socket.emit({type:'audio_ready',...speechD,activity_id:4});
  const savedCallback=worklets.at(-1).port.onmessage, savedHandler=socket.onmessage;
  context.stopVoiceSession();assert(socket.closed);assert(streams.every(stream=>stream.track.stopped));
  assert(worklets.every(worklet=>worklet.disconnected));assert(audioContexts.every(audio=>audio.state==='closed'));assert(revoked.length>0);
  const stoppedSends=socket.sent.length;savedCallback({data:new Int16Array(320).fill(2000).buffer});savedHandler({data:JSON.stringify(audio(speechC))});
  assert.equal(socket.sent.length,stoppedSends);assert.equal(state('pendingText'),null);assert.equal(state('activity'),null);
  assert.equal(context.buildRequest().deadline_at,original.deadline_at);
  // Slow audio_ready is bounded to two seconds; discarded speech must not become a provider request.
  socket=await connect(true);pulse(2000,120);
  assert.equal(state('activity').frames.length,0);assert.equal(state('activity').discarded,true);
  assert.equal(socket.sent.filter(item=>item instanceof ArrayBuffer).length,0);
  const slowTurn=startTurn(socket,'slow-onset');socket.emit({type:'audio_ready',...slowTurn,activity_id:1});
  assert.equal(socket.sent.at(-1).type,'interrupt');assert.equal(state('activity'),null);
  assert.equal(socket.sent.filter(item=>item instanceof ArrayBuffer).length,0);
  recover(socket,2);await note('Use this latest typed correction instead');
  assert.equal(state('pendingText'),'Use this latest typed correction instead');
  socket.emit({type:'error',code:'recovery_failed',message:'Live intake could not resume. Use the offer form.'});
  assert(socket.closed);assert.equal(state('pendingText'),null);assert(streams.every(stream=>stream.track.stopped));
  assert(audioContexts.every(audio=>audio.state==='closed'));assert(worklets.every(worklet=>worklet.disconnected));
  assert.equal(node('voice-send').disabled,false);assert.match(node('voice-status').textContent,/could not resume/);
  view.dispose();assert.equal(timers.size,0);
  console.log('View transport: typed and spoken recovery, epoch-owned audio, queued corrections, duplicate/stale refusal, selection recovery, fixed lifetime/deadline and terminal cleanup passed.');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
