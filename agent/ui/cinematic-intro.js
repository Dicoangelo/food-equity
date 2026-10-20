/* Optional sidebar film. The working app always opens immediately. */
(function (root) {
  'use strict';
  function bindControls({video, panel, toggle, close, status}) {
    const hide = (restoreFocus = false) => {
      video.pause(); panel.hidden = true;
      toggle.setAttribute('aria-expanded', 'false');
      toggle.textContent = 'Watch the story · 40 sec';
      if (restoreFocus) toggle.focus({preventScroll: true});
    };
    panel.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.addEventListener('click', () => {
      if (!panel.hidden) { hide(); return; }
      // Only an explicit open loads media. Playback remains a native user action.
      if (!video.getAttribute('src')) video.src = '/ui/food-equity-story-v1.mp4';
      panel.hidden = false;
      toggle.setAttribute('aria-expanded', 'true');
      toggle.textContent = 'Hide the story';
    });
    close.addEventListener('click', () => hide(true));
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); hide(true); }
    });
    video.addEventListener('error', () => {
      status.textContent = 'The film could not load. You can keep using the workspace.';
    });
    return {hide, pause: () => video.pause()};
  }
  function init() {
    const doc = root.document;
    if (!doc || doc.getElementById('food-story')) return;
    const host = doc.getElementById('workspace-navigation');
    if (!host) return;
    const make = (tag, className, text) => {
      const node = doc.createElement(tag);
      if (className) node.className = className;
      if (text) node.textContent = text;
      return node;
    };
    const story = make('section', 'food-story'); story.id = 'food-story';
    story.setAttribute('aria-label', 'Food Equity story');
    const toggle = make('button', 'food-story-toggle', 'Watch the story · 40 sec');
    toggle.type = 'button'; toggle.setAttribute('aria-controls', 'food-story-panel');
    const panel = make('div', 'food-story-panel'); panel.id = 'food-story-panel';
    const video = make('video', 'food-story-video');
    video.controls = true; video.playsInline = true; video.preload = 'none';
    video.setAttribute('playsinline', '');
    video.setAttribute('aria-label', 'Food Equity, a 40-second introduction');
    const status = make('p', 'food-story-caption', 'A short introduction. Press play when you are ready.');
    status.setAttribute('role', 'status');
    const close = make('button', 'food-story-close', 'Close story'); close.type = 'button';
    panel.append(video, status, close); story.append(toggle, panel);
    host.insertBefore(story, host.querySelector('.workspace-note'));
    const controls = bindControls({video, panel, toggle, close, status});
    // Pause when mobile navigation is hidden or another existing modal owns focus.
    const pauseWhenHidden = () => {
      if (doc.hidden || host.hasAttribute('inert') || root.getComputedStyle(host).visibility === 'hidden') controls.pause();
    };
    const observer = new root.MutationObserver(pauseWhenHidden);
    observer.observe(doc.body, {attributes: true, attributeFilter: ['class']});
    observer.observe(host, {attributes: true, attributeFilter: ['inert']});
    doc.addEventListener('visibilitychange', pauseWhenHidden);
    root.addEventListener('resize', pauseWhenHidden);
    root.addEventListener('pagehide', () => {controls.pause(); observer.disconnect();}, {once: true});
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {bindControls, init};
  else if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})(typeof window === 'undefined' ? globalThis : window);
