/* A presentation companion for the existing reviewed Live workflow. No new agent state. */
(function (root) {
  'use strict';
  function project(state) {
    return {
      mode: state.microphone ? 'microphone' : state.review ? 'review' : state.active ? 'session' : 'idle',
      title: state.microphone ? 'Microphone on' : state.review ? 'Changes ready to review' : state.active ? 'Session open' : 'Your rescue companion',
      canTalk: !state.startDisabled,
      canStop: !state.stopDisabled,
      canReview: state.review,
    };
  }
  function act(action, api) {
    if (action === 'stop') { api.stop(); return; }
    api.open();
    if (action === 'talk') api.start(true);
    else if (action === 'review') api.review();
    else api.focusText();
  }
  function init() {
    const doc=root.document, $=id=>doc.getElementById(id);
    if (!doc || $('food-companion') || !root.FoodWorkspace) return;
    const dock=doc.createElement('section');dock.id='food-companion';dock.className='food-companion';
    dock.setAttribute('aria-label','Food rescue companion');
    const figure=doc.createElement('div');figure.className='companion-character';figure.setAttribute('aria-hidden','true');
    // Original CSS character: leaf crest, rounded body and two expressive eyes.
    for (const name of ['leaf','face','eye eye-left','eye eye-right']) {
      const part=doc.createElement('span');part.className='companion-'+name;figure.appendChild(part);
    }
    dock.appendChild(figure);
    const body=doc.createElement('div');body.className='companion-content';dock.appendChild(body);
    const title=doc.createElement('strong');title.setAttribute('role','status');title.setAttribute('aria-live','polite');body.appendChild(title);
    const status=doc.createElement('p');status.className='companion-status';body.appendChild(status);
    const controls=doc.createElement('div');controls.className='companion-controls';body.appendChild(controls);
    const buttons={};
    const api={open:()=>root.FoodWorkspace.openAssistant(buttons.text),start:on=>root.startVoiceSession?.(on),
      stop:()=>root.stopVoiceSession?.('Session stopped. Microphone and playback are off; your reviewed form is unchanged.'),
      focusText:()=>$('voice-text')?.focus(),review:()=>{$('voice-review')?.scrollIntoView({block:'nearest'});$('voice-apply')?.focus();}};
    for (const [action,label] of [['talk','Talk to agent'],['text','Type a note'],['review','Review changes'],['stop','Stop']]) {
      const button=doc.createElement('button');button.type='button';button.textContent=label;button.dataset.companionAction=action;
      if (action==='talk') button.setAttribute('aria-label','Talk to agent — start microphone');
      if (action==='stop') button.setAttribute('aria-label','Stop session and microphone');
      button.addEventListener('click',()=>act(action,api));controls.appendChild(button);buttons[action]=button;
    }
    doc.body.appendChild(dock);doc.body.classList.add('has-food-companion');
    function sync() {
      const start=$('voice-start'),stop=$('voice-stop'),review=$('voice-review'),apply=$('voice-apply'),drawer=$('assistant-drawer');
      const current=project({microphone:start?.textContent==='Microphone on',active:stop?.disabled===false,
        startDisabled:start?.disabled!==false,stopDisabled:stop?.disabled!==false,
        review:!!review&&!review.classList.contains('hidden')&&apply?.disabled===false});
      dock.dataset.state=current.mode;if(title.textContent!==current.title)title.textContent=current.title;
      status.textContent=($('voice-status')?.textContent||'Talk or type; review changes before applying.').slice(0,180);
      buttons.talk.disabled=!current.canTalk;buttons.stop.hidden=!current.canStop;buttons.review.hidden=!current.canReview;
      // Keep controls within the active dialog's focus trap while retaining one dock.
      const host=drawer&&!drawer.hidden ? drawer : doc.body;
      const profile=$('recipient-detail');
      dock.hidden=host===doc.body&&(doc.body.classList.contains('workspace-nav-open')||
        (profile?.getAttribute?.('aria-modal')==='true'&&!profile.hidden&&!profile.classList.contains('hidden')));
      if(dock.parentElement!==host)host.appendChild(dock);
      dock.classList.toggle('companion-in-dialog',host===drawer);
    }
    const observer=new root.MutationObserver(sync);
    for(const id of ['voice-start','voice-stop','voice-apply','voice-review','voice-status'])
      if($(id))observer.observe($(id),{attributes:true,childList:true,subtree:true,characterData:true});
    if($('assistant-drawer'))observer.observe($('assistant-drawer'),{attributes:true,attributeFilter:['hidden']});
    observer.observe(doc.body,{attributes:true,attributeFilter:['class']});
    if($('recipient-detail'))observer.observe($('recipient-detail'),{attributes:true,attributeFilter:['class','hidden','aria-modal']});
    sync();
    root.addEventListener('pagehide',()=>{observer.disconnect();root.stopVoiceSession?.('Session closed.');},{once:true});
    return {sync};
  }
  const api={project,act,init};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;
  else {root.FoodCompanion=api;if(root.document.readyState==='loading')root.document.addEventListener('DOMContentLoaded',init,{once:true});else init();}
})(typeof window==='undefined'?globalThis:window);
