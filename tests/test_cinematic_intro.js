const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {shouldShow} = require('../agent/ui/cinematic-intro.js');
test('entrance is once per session with a safe storage-disabled fallback', () => {
  assert.equal(shouldShow({getItem:()=>null}), true);
  assert.equal(shouldShow({getItem:()=>'seen'}), false);
  assert.equal(shouldShow({getItem:()=>{throw Error('disabled');}}), true);
});
test('film has explicit exits and audio controls without runtime or microphone coupling', () => {
  const src = fs.readFileSync(path.join(__dirname,'../agent/ui/cinematic-intro.js'),'utf8');
  assert.match(src,/video\.muted = true/);
  assert.match(src,/prefers-reduced-motion: reduce/);
  assert.match(src,/Skip intro/); assert.match(src,/Enter workspace/);
  assert.match(src,/video\.pause\(\); video\.removeAttribute\('src'\); video\.load\(\)/);
  assert.doesNotMatch(src,/getUserMedia|startVoiceSession|new WebSocket|fetch\(|setTimeout|innerHTML/);
});
