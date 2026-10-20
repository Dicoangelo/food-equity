const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {project,act}=require('../agent/ui/companion.js');

test('companion projection reflects existing controls, never invents agent work',()=>{
  assert.deepEqual(project({startDisabled:true,stopDisabled:true}),{mode:'idle',title:'Your rescue companion',canTalk:false,canStop:false,canReview:undefined});
  assert.equal(project({microphone:true,active:true,review:true,startDisabled:true,stopDisabled:false}).title,'Microphone on');
  const ready=project({review:true,active:false,startDisabled:false,stopDisabled:true});
  assert.equal(ready.mode,'review');assert.equal(ready.canReview,true);assert.equal(ready.canStop,false);
  assert.equal(project({active:true,startDisabled:false,stopDisabled:false}).title,'Session open');
});
test('only explicit Talk requests microphone; typing and review never apply or match',()=>{
  const calls=[];const api={open:()=>calls.push('open'),start:value=>calls.push(['start',value]),stop:()=>calls.push('stop'),review:()=>calls.push('review'),focusText:()=>calls.push('text')};
  assert.deepEqual(calls,[]);
  act('text',api);assert.deepEqual(calls,['open','text']);calls.length=0;
  act('review',api);assert.deepEqual(calls,['open','review']);calls.length=0;
  act('talk',api);assert.deepEqual(calls,['open',['start',true]]);calls.length=0;
  act('stop',api);assert.deepEqual(calls,['stop']);
});
test('loading and observation never start capture; modal keeps the same dock within focus trap',()=>{
  const source=fs.readFileSync(path.join(__dirname,'../agent/ui/companion.js'),'utf8');
  assert.match(source,/MutationObserver\(sync\)/);
  assert.match(source,/host\.appendChild\(dock\)/);
  assert.match(source,/observer\.disconnect\(\)/);
  assert.doesNotMatch(source,/fetch\(|new WebSocket|innerHTML|setInterval|applyVoiceDraft|runMatching/);
  assert.equal((source.match(/api\.start\(true\)/g)||[]).length,1);
});
test('mobile dock reserves content space, supports safe areas and reduced motion',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../agent/ui/companion.css'),'utf8');
  assert.match(css,/min-width:44px;min-height:44px/);
  assert.match(css,/safe-area-inset-bottom/);
  assert.match(css,/@media\(max-width:600px\)/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
  assert.match(css,/padding-bottom:210px!important/);
  assert.match(css,/companion-in-dialog \{position:static;flex:0 0 auto/);
  assert.doesNotMatch(css,/#assistant-drawer:has/);
});
test('real initializer observes existing state and binds explicit controls without microphone startup',()=>{
  const calls=[], nodes=new Map(), listeners={},documentListeners={};let observer,actualOpener;
  class Element {
    constructor(){this.children=[];this.dataset={};this.attrs={};this.hidden=false;this.textContent='';this.disabled=false;this.events={};this.classes=new Set();this.classList={add:x=>this.classes.add(x),contains:x=>this.classes.has(x),toggle:(x,on)=>on?this.classes.add(x):this.classes.delete(x)};}
    set id(value){this._id=value;nodes.set(value,this);}get id(){return this._id;}
    appendChild(child){if(child.parentElement)child.parentElement.children=child.parentElement.children.filter(c=>c!==child);child.parentElement=this;this.children.push(child);return child;}
    setAttribute(k,v){this.attrs[k]=v;}getAttribute(k){return this.attrs[k];}addEventListener(k,fn){this.events[k]=fn;}
    focus(){calls.push('focus');}scrollIntoView(){calls.push('scroll');}
  }
  const make=id=>{const e=new Element();e.id=id;return e;};
  for(const id of ['voice-start','voice-stop','voice-apply','voice-review','voice-status','voice-text','assistant-drawer'])make(id);
  nodes.get('voice-stop').disabled=true;nodes.get('voice-apply').disabled=true;nodes.get('voice-review').classes.add('hidden');nodes.get('assistant-drawer').hidden=true;
  const saved={};for(const key of ['document','MutationObserver','FoodWorkspace','startVoiceSession','stopVoiceSession','addEventListener'])saved[key]=globalThis[key];
  try {
    globalThis.document={body:new Element(),createElement:()=>new Element(),getElementById:id=>nodes.get(id),addEventListener:(kind,fn)=>documentListeners[kind]=fn,removeEventListener:kind=>delete documentListeners[kind]};
    globalThis.MutationObserver=class{constructor(fn){this.sync=fn;observer=this;}observe(){}disconnect(){calls.push('disconnect');}};
    globalThis.FoodWorkspace={openAssistant(opener){actualOpener=opener;calls.push('open');nodes.get('assistant-drawer').hidden=false;}};
    globalThis.startVoiceSession=value=>calls.push(['mic',value]);globalThis.stopVoiceSession=()=>calls.push('stop');globalThis.addEventListener=(name,fn)=>listeners[name]=fn;
    require('../agent/ui/companion.js').init();assert.deepEqual(calls,[],'Rendering and observing never request capture');
    const dock=nodes.get('food-companion'),buttons=dock.children[1].children[2].children;
    globalThis.document.body.classes.add('workspace-nav-open');observer.sync();assert.equal(dock.hidden,true,'Unrelated navigation modal keeps companion controls out of its focus boundary');
    globalThis.document.body.classes.delete('workspace-nav-open');observer.sync();assert.equal(dock.hidden,false);
    const button=action=>buttons.find(b=>b.dataset.companionAction===action);
    button('text').events.click();assert.deepEqual(calls,['open','focus']);assert.equal(actualOpener,button('text'));observer.sync();assert.equal(dock.parentElement,nodes.get('assistant-drawer'));
    documentListeners.keydown({type:'keydown',key:'Escape'});assert.equal(dock.parentElement,globalThis.document.body,'Dock is visible before workspace restores focus to its actual opener');
    nodes.get('assistant-drawer').hidden=true;observer.sync();
    calls.length=0;button('talk').events.click();assert.deepEqual(calls,['open',['mic',true]]);assert.equal(actualOpener,button('talk'));observer.sync();
    documentListeners.click({type:'click',target:{closest:()=>true}});assert.equal(dock.parentElement,globalThis.document.body);nodes.get('assistant-drawer').hidden=true;observer.sync();
    button('review').events.click();assert.equal(actualOpener,button('review'));observer.sync();
    nodes.get('voice-start').textContent='Microphone on';nodes.get('voice-start').disabled=true;nodes.get('voice-stop').disabled=false;observer.sync();assert.equal(dock.dataset.state,'microphone');assert.equal(button('stop').hidden,false);
    calls.length=0;button('stop').events.click();assert.deepEqual(calls,['stop']);
    calls.length=0;listeners.pagehide();assert.deepEqual(calls,['disconnect','stop']);
  } finally {for(const [key,value]of Object.entries(saved)){if(value===undefined)delete globalThis[key];else globalThis[key]=value;}}
});
