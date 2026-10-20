const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {bindControls} = require('../agent/ui/cinematic-intro.js');
function fixture() {
  const element = () => ({attributes:{},listeners:{},hidden:false,textContent:'',
    setAttribute(k,v){this.attributes[k]=v;},getAttribute(k){return this.attributes[k]||null;},
    addEventListener(k,fn){this.listeners[k]=fn;},focus(){this.focused=true;},
    emit(k,event={}){this.listeners[k]?.(event);}});
  const nodes={video:element(),panel:element(),toggle:element(),close:element(),status:element()};
  nodes.video.pause=()=>{nodes.video.pauses=(nodes.video.pauses||0)+1;};
  nodes.video.play=()=>{throw Error('Opening the story must not autoplay');};
  return {...nodes,controller:bindControls(nodes)};
}
test('film is initially collapsed and unloaded; opening does not autoplay or gate the app',()=>{
  const f=fixture();
  assert.equal(f.panel.hidden,true);assert.equal(f.toggle.attributes['aria-expanded'],'false');
  assert.equal(f.video.src,undefined);assert.equal(f.video.pauses,undefined);
  f.toggle.emit('click');
  assert.equal(f.panel.hidden,false);assert.equal(f.toggle.attributes['aria-expanded'],'true');
  assert.equal(f.video.src,'/ui/food-equity-story-v1.mp4');assert.equal(f.toggle.focused,undefined);
});
test('close and toggle collapse pause playback; close returns focus to the story button',()=>{
  const f=fixture();f.toggle.emit('click');f.close.emit('click');
  assert.equal(f.panel.hidden,true);assert.equal(f.video.pauses,1);assert.equal(f.toggle.focused,true);
  f.toggle.emit('click');f.toggle.emit('click');
  assert.equal(f.video.pauses,2);assert.equal(f.toggle.attributes['aria-expanded'],'false');
});
test('Escape closes only the story and errors leave the rest of the app usable',()=>{
  const f=fixture();let prevented=false,stopped=false;f.toggle.emit('click');
  f.video.emit('error');assert.match(f.status.textContent,/keep using the workspace/);
  f.panel.emit('keydown',{key:'Escape',preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});
  assert.equal(f.panel.hidden,true);assert.equal(f.video.pauses,1);assert.ok(prevented&&stopped);
});
test('module has no automatic entrance, audio start, storage gate or background mutation',()=>{
  const src=fs.readFileSync(path.join(__dirname,'../agent/ui/cinematic-intro.js'),'utf8');
  assert.doesNotMatch(src,/\.play\(|sessionStorage|aria-modal|setAttribute\('inert'|role.*dialog|getUserMedia|startVoiceSession|new WebSocket|fetch\(/);
  assert.match(src,/video\.controls = true/);assert.match(src,/video\.preload = 'none'/);
});
