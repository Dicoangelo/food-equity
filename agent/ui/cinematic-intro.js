/* Optional session entrance. Media never delays or changes the working app. */
(function (root) {
  'use strict';
  const KEY = 'food-equity-intro-v1';
  function shouldShow(storage) {
    try { return storage.getItem(KEY) !== 'seen'; } catch (_) { return true; }
  }
  function init() {
    const doc = root.document;
    if (!doc || doc.getElementById('food-cinematic-intro')) return;
    let storage;
    try { storage = root.sessionStorage; } catch (_) { storage = null; }
    if (!shouldShow(storage)) return;
    try { storage.setItem(KEY, 'seen'); } catch (_) { /* Storage can be disabled. */ }
    const make = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text) node.textContent = text;
      return node;
    };
    const overlay = make('section', 'food-cinematic-intro');
    overlay.id = 'food-cinematic-intro';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-labelledby', 'food-intro-title');
    const card = make('div', 'food-intro-card');
    const header = make('header', 'food-intro-header');
    const heading = make('div');
    heading.append(make('p', 'food-intro-eyebrow', 'Surplus to community'));
    const title = make('h1', '', 'Food Equity'); title.id = 'food-intro-title'; heading.append(title);
    const skip = make('button', 'food-intro-skip', 'Skip intro'); skip.type = 'button';
    header.append(heading, skip);
    const video = make('video', 'food-intro-video');
    video.src = '/ui/food-equity-story-v1.mp4';
    video.muted = true; video.defaultMuted = true; video.playsInline = true; video.preload = 'metadata';
    video.setAttribute('playsinline', ''); video.setAttribute('muted', '');
    video.setAttribute('aria-label', 'Food Equity introduction film');
    const footer = make('div', 'food-intro-footer');
    const controls = make('div', 'food-intro-controls');
    const play = make('button', '', 'Play film'), sound = make('button', '', 'Turn sound on');
    play.type = sound.type = 'button';
    sound.setAttribute('aria-pressed', 'false');
    const enter = make('button', 'food-intro-enter', 'Enter workspace'); enter.type = 'button';
    controls.append(play, sound);
    footer.append(controls, enter);
    const status = make('p', 'food-intro-status', 'A short introduction. You can enter the workspace at any time.');
    status.setAttribute('role', 'status');
    card.append(header, video, footer, status); overlay.append(card);
    const previousFocus = doc.activeElement, background = new Map();
    const shield = () => {
      for (const element of doc.body.children) {
        if (element === overlay || background.has(element)) continue;
        background.set(element, element.hasAttribute('inert'));
        element.setAttribute('inert', '');
      }
    };
    doc.body.append(overlay); doc.body.classList.add('food-intro-open'); shield();
    const observer = new root.MutationObserver(shield); observer.observe(doc.body, {childList: true});
    let closed = false;
    const refresh = () => {
      play.textContent = video.paused || video.ended ? 'Play film' : 'Pause film';
      sound.textContent = video.muted ? 'Turn sound on' : 'Mute sound';
      sound.setAttribute('aria-pressed', String(!video.muted));
    };
    const close = () => {
      if (closed) return; closed = true;
      observer.disconnect(); video.pause(); video.removeAttribute('src'); video.load();
      overlay.remove(); doc.body.classList.remove('food-intro-open');
      for (const [element, wasInert] of background) if (!wasInert) element.removeAttribute('inert');
      const restorePrevious = previousFocus && previousFocus !== doc.body && previousFocus.isConnected && !previousFocus.closest('[inert]');
      const target = restorePrevious
        ? previousFocus : doc.getElementById('workspace-view-title');
      if (target) { if (!restorePrevious && !target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1'); target.focus({preventScroll: true}); }
    };
    const start = async () => {
      if (closed) return;
      try { await video.play(); if (!closed) refresh(); }
      catch (_) { if (!closed) { status.textContent = 'Press Play film to watch, or enter the workspace now.'; refresh(); } }
    };
    play.addEventListener('click', () => { if (video.paused || video.ended) start(); else video.pause(); });
    sound.addEventListener('click', () => { video.muted = !video.muted; refresh(); });
    for (const event of ['play', 'pause', 'volumechange']) video.addEventListener(event, refresh);
    video.addEventListener('ended', () => { refresh(); status.textContent = 'Ready when you are. Enter the workspace to start.'; });
    video.addEventListener('error', () => {
      if (closed) return;
      status.textContent = 'The film could not load. The workspace is ready to use.';
      play.disabled = sound.disabled = true;
    });
    skip.addEventListener('click', close); enter.addEventListener('click', close);
    overlay.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
      if (event.key !== 'Tab') return;
      const items = [...overlay.querySelectorAll('button')].filter(node => !node.disabled);
      const first = items[0], last = items[items.length - 1];
      if (event.shiftKey && doc.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && doc.activeElement === last) { event.preventDefault(); first.focus(); }
    });
    enter.focus({preventScroll: true});
    if (!root.matchMedia?.('(prefers-reduced-motion: reduce)').matches) start();
    root.addEventListener('pagehide', close, {once: true});
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {shouldShow, init};
  else if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})(typeof window === 'undefined' ? globalThis : window);
