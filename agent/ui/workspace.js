/* Food Equity workspace presentation. Existing broker and voice code own all actions. */
(() => {
  'use strict';

  const views = {
    match: {
      id: 'match-workspace',
      title: 'Match workspace',
      description: 'Build an offer, check recipient fit and review the proposed match.',
    },
    recipients: {
      id: 'recipients-workspace',
      title: 'Recipient directory',
      description: 'Explore recipient profiles, source status and fit for the current offer.',
    },
    evidence: {
      id: 'evidence-workspace',
      title: 'Evidence & guidance',
      description: 'Review source evidence, exclusions and guidance before arranging pickup.',
    },
  };
  const fieldIds = new Set(['input-pallets', 'input-storage', 'input-hours']);
  const $ = id => document.getElementById(id);
  let initialized = false;
  let currentView = 'match';
  let navOpener = null;
  let assistantOpener = null;
  let navProfileSuspended = false;
  let assistantProfileSuspended = false;
  let navModalAttributes = null;
  let metricFrame = null;
  let resizeFrame = null;
  const navInert = new Map();
  const assistantInert = new Map();

  function visible(element) {
    return Boolean(element && element.isConnected && !element.disabled &&
      !element.closest('[hidden], [inert], .hidden') && element.getClientRects().length);
  }

  function focus(element) {
    if (!visible(element) || typeof element.focus !== 'function') return false;
    element.focus({preventScroll: true});
    return document.activeElement === element;
  }

  function focusable(container) {
    return container ? [...container.querySelectorAll(
      'button, a[href], input, select, textarea, summary, [tabindex]:not([tabindex="-1"])',
    )].filter(visible) : [];
  }

  function focusWorkspace() {
    const target = $('workspace-view-title') || $(views[currentView].id);
    if (target && !target.hasAttribute('tabindex')) target.setAttribute('tabindex', '-1');
    focus(target);
  }

  function updateText(id, value) {
    const target = $(id);
    if (target && target.textContent !== value) target.textContent = value;
  }

  function numericValue(id) {
    const field = $(id);
    const raw = field?.value?.trim();
    if (!raw || field.validity?.badInput) return null;
    const number = Number(raw);
    const min = Number(field.min);
    const max = Number(field.max);
    if (!Number.isFinite(number) || number <= 0 ||
        (field.min && Number.isFinite(min) && number < min) ||
        (field.max && Number.isFinite(max) && number > max)) return null;
    return number;
  }

  function numberLabel(value) {
    return value.toLocaleString('en-US', {maximumFractionDigits: 20});
  }

  function updateMetrics() {
    const quantity = numericValue('input-pallets');
    updateText('workspace-quantity', quantity === null
      ? 'Not specified'
      : `${numberLabel(quantity)} ${quantity === 1 ? 'pallet' : 'pallets'}`);

    const storage = $('input-storage');
    const selectedStorage = storage?.selectedOptions?.[0]?.textContent?.trim();
    updateText('workspace-storage', selectedStorage || 'Not specified');

    const clock = $('review-clock');
    const clockActive = clock && !clock.hidden && !clock.classList.contains('hidden');
    const deadline = clockActive ? $('review-deadline')?.textContent?.trim() : null;
    if (deadline && !/^(?:—|-|not set|not specified|pending)$/i.test(deadline)) {
      updateText('workspace-clock', /deadline/i.test(deadline) ? deadline : `Deadline · ${deadline}`);
    } else {
      const hours = numericValue('input-hours');
      updateText('workspace-clock', hours === null
        ? 'Pickup window not specified'
        : `${numberLabel(hours)}h pickup window`);
    }

    updateText('workspace-check', $('runtime-status')?.textContent?.trim() || 'Not run yet');
  }

  function scheduleMetrics() {
    if (metricFrame !== null) return;
    metricFrame = window.requestAnimationFrame(() => {
      metricFrame = null;
      updateMetrics();
    });
  }

  function redrawMap() {
    if (resizeFrame !== null) window.cancelAnimationFrame(resizeFrame);
    resizeFrame = null;
    const hasVisibleSize = () => {
      const canvas = $('map');
      return Boolean(canvas && !canvas.closest('[hidden], .hidden') &&
        canvas.clientWidth > 0 && canvas.clientHeight > 0);
    };
    if (!hasVisibleSize()) return;
    resizeFrame = window.requestAnimationFrame(() => {
      resizeFrame = null;
      // A view can change again before the animation frame. Hidden canvases
      // must never receive a zero-sized Leaflet/heat redraw.
      if (hasVisibleSize()) window.dispatchEvent(new Event('resize'));
    });
  }

  function navigationPanel() {
    return $('workspace-navigation') || document.querySelector('.workspace-sidebar');
  }

  function recipientExplorer() {
    return typeof RecipientExplorer !== 'undefined' ? RecipientExplorer : window.RecipientExplorer;
  }

  function inertBackground(records, selector) {
    document.querySelectorAll(selector).forEach(node => {
      if (!records.has(node)) records.set(node, {
        attribute: node.getAttribute('inert'), property: Boolean(node.inert),
      });
      node.inert = true;
      node.setAttribute('inert', '');
    });
  }

  function restoreBackground(records) {
    records.forEach((previous, node) => {
      if (previous.attribute === null) node.removeAttribute('inert');
      else node.setAttribute('inert', previous.attribute);
      node.inert = previous.property;
    });
    records.clear();
  }

  function assistantTriggers() {
    return document.querySelectorAll('[data-open-assistant], #recipient-ask-assistant');
  }

  function updateAssistantContext() {
    const explorer = recipientExplorer();
    const selected = explorer?.selectedId?.();
    const row = selected == null ? null
      : explorer?.mergedRecipients?.().find(recipient => String(recipient.recipient_id) === String(selected));
    updateText('assistant-context-name', selected == null
      ? 'Current offer and displayed evidence' : row?.name || 'Selected recipient');
  }

  function closeNavigation({restoreFocus = true, resumeProfile = true} = {}) {
    const wasOpen = document.body.classList.contains('workspace-nav-open');
    const hadProfile = navProfileSuspended;
    document.body.classList.remove('workspace-nav-open');
    $('workspace-nav-toggle')?.setAttribute('aria-expanded', 'false');
    if ($('workspace-nav-scrim')) $('workspace-nav-scrim').hidden = true;
    if (navModalAttributes) {
      const panel = navigationPanel();
      Object.entries(navModalAttributes).forEach(([name, value]) => {
        if (value === null) panel?.removeAttribute(name);
        else panel?.setAttribute(name, value);
      });
      navModalAttributes = null;
    }
    restoreBackground(navInert);
    const resumed = resumeProfile && hadProfile && recipientExplorer()?.resume?.();
    if (wasOpen && restoreFocus && !resumed) {
      if (!focus(navOpener) && !focus($('workspace-nav-toggle'))) focusWorkspace();
    }
    navOpener = null;
    navProfileSuspended = false;
    return hadProfile;
  }

  function openNavigation(opener) {
    if (!navigationPanel()) return;
    const requestedOpener = opener || document.activeElement;
    const inheritedProfile = assistantIsOpen()
      ? closeAssistant({restoreFocus: false, resumeProfile: false}) : false;
    navProfileSuspended = inheritedProfile || Boolean(recipientExplorer()?.suspend?.());
    navOpener = requestedOpener;
    document.body.classList.add('workspace-nav-open');
    $('workspace-nav-toggle')?.setAttribute('aria-expanded', 'true');
    if ($('workspace-nav-scrim')) $('workspace-nav-scrim').hidden = false;
    inertBackground(navInert, '.page-shell, .skip-link, #recipient-detail');
    const panel = navigationPanel();
    navModalAttributes = {
      role: panel.getAttribute('role'), 'aria-modal': panel.getAttribute('aria-modal'),
    };
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-modal', 'true');
    focus(panel.querySelector('[aria-current="page"]')) || focus(focusable(panel)[0]);
  }

  function assistantIsOpen() {
    const drawer = $('assistant-drawer');
    return Boolean(drawer && !drawer.hidden);
  }

  function prepareWorkspaceArtwork() {
    const note = document.querySelector('.workspace-note');
    const review = $('review-panel');
    const mount = (host, className, source, decorate) => {
      if (!host || host.querySelector(`.${className}`)) return;
      const art = document.createElement('img');
      art.className = className;
      art.alt = '';
      art.setAttribute('aria-hidden', 'true');
      art.decoding = 'async';
      art.onload = () => { if (decorate) host.classList.add('has-review-art'); };
      art.onerror = () => { art.remove(); if (decorate) host.classList.remove('has-review-art'); };
      art.src = source;
      host.prepend(art);
    };
    mount(note, 'workspace-network-art', '/ui/food-rescue-network.png', false);
    mount(review, 'workspace-review-art', '/ui/review-crate-spot.png', true);
  }

  function prepareAssistantHelp() {
    const panel = $('voice-panel');
    const introduction = panel?.querySelector(':scope > p:first-of-type');
    if (!introduction || panel.querySelector('.assistant-help')) return;
    // Keep the full limitations available without hiding the audio consent cue
    // or pushing the controls below a phone's first screen.
    const cue = document.createElement('p');
    cue.className = 'assistant-consent';
    cue.textContent = 'Speak or type a note. Audio is sent only after Start microphone. Nothing is dispatched.';
    const help = document.createElement('details');
    help.className = 'assistant-help';
    const summary = document.createElement('summary');
    summary.textContent = 'How the assistant works';
    introduction.before(cue, help);
    help.append(summary, introduction);
  }

  function focusAssistant() {
    // Opening a phone drawer should not immediately open the software keyboard.
    // Desktop keeps the direct typing shortcut, while Tab follows natural order.
    const target = window.innerWidth <= 640 ? $('assistant-close') : $('voice-text');
    focus(target) || focus($('assistant-close'));
  }

  function openAssistant(opener) {
    const drawer = $('assistant-drawer');
    if (!drawer) return false;
    if (assistantIsOpen()) {
      focusAssistant();
      return true;
    }
    // Transfer modal ownership without changing selected-recipient evidence.
    const requestedOpener = opener || document.activeElement;
    const inheritedProfile = closeNavigation({restoreFocus: false, resumeProfile: false});
    assistantProfileSuspended = inheritedProfile || Boolean(recipientExplorer()?.suspend?.());
    assistantOpener = requestedOpener;
    drawer.hidden = false;
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-modal', 'true');
    if ($('assistant-scrim')) $('assistant-scrim').hidden = false;
    document.body.classList.add('workspace-assistant-open');
    inertBackground(assistantInert, '.page-shell, .workspace-sidebar, .skip-link, #recipient-detail');
    assistantTriggers().forEach(button => {
      button.setAttribute('aria-expanded', 'true');
      button.setAttribute('aria-controls', 'assistant-drawer');
    });
    if ($('voice-panel')) $('voice-panel').open = true;
    updateAssistantContext();
    focusAssistant();
    return true;
  }

  function closeAssistant({restoreFocus = true, resumeProfile = true} = {}) {
    const drawer = $('assistant-drawer');
    if (!drawer || drawer.hidden) return false;
    const hadProfile = assistantProfileSuspended;
    // A close is terminal for the current live session, but opening never starts audio.
    if (typeof window.stopVoiceSession === 'function') window.stopVoiceSession();
    drawer.hidden = true;
    if ($('assistant-scrim')) $('assistant-scrim').hidden = true;
    document.body.classList.remove('workspace-assistant-open');
    restoreBackground(assistantInert);
    assistantTriggers().forEach(button => {
      button.setAttribute('aria-expanded', 'false');
    });
    const resumed = resumeProfile && hadProfile && recipientExplorer()?.resume?.(assistantOpener);
    if (restoreFocus && !resumed) {
      const fallback = [...assistantTriggers()].find(visible);
      if (!focus(assistantOpener) && !focus(fallback)) focusWorkspace();
    }
    assistantOpener = null;
    assistantProfileSuspended = false;
    scheduleMetrics();
    return hadProfile;
  }

  function showView(name) {
    if (!Object.prototype.hasOwnProperty.call(views, name)) return false;
    const destination = $(views[name].id);
    if (!destination) return false;
    const activeView = document.activeElement?.closest('.workspace-view');
    const movesFocusedContent = activeView && activeView !== destination;
    if (assistantIsOpen()) closeAssistant();
    currentView = name;
    Object.entries(views).forEach(([key, view]) => {
      const panel = $(view.id);
      if (panel) panel.hidden = key !== name;
    });
    document.querySelectorAll('[data-workspace-view]').forEach(button => {
      if (button.dataset.workspaceView === name) button.setAttribute('aria-current', 'page');
      else button.removeAttribute('aria-current');
    });
    updateText('workspace-view-title', views[name].title);
    updateText('workspace-view-description', views[name].description);

    const map = $('map-panel');
    const slot = name === 'recipients' ? $('recipient-view-slot') : $('match-map-slot');
    if (name !== 'evidence' && map && slot && map.parentNode !== slot) slot.appendChild(map);
    const sourceSummary = $('source-state-summary');
    const sourceSlot = name === 'evidence' ? $('evidence-source-slot') : $('map-source-slot');
    if (sourceSummary && sourceSlot && sourceSummary.parentNode !== sourceSlot) sourceSlot.appendChild(sourceSummary);
    closeNavigation();
    if (movesFocusedContent) focusWorkspace();
    scheduleMetrics();
    redrawMap();
    return true;
  }

  function reveal(id) {
    if (id === 'voice-panel') return openAssistant();
    let view = null;
    if (['offer-panel', 'review-panel', 'handoff-panel', 'brief-container', 'scenario-panel'].includes(id)) view = 'match';
    else if (['foodkeeper-panel', 'foodkeeper', 'exclusions-panel', 'exclusions', 'run-evidence'].includes(id)) view = 'evidence';
    else if (id === 'map-panel') view = 'recipients';
    else {
      const hostId = $(id)?.closest('.workspace-view')?.id;
      view = Object.keys(views).find(key => views[key].id === hostId);
    }
    return view ? showView(view) : false;
  }

  function trapFocus(event, container) {
    const controls = focusable(container);
    if (!controls.length) {
      event.preventDefault();
      if (container && !container.hasAttribute('tabindex')) container.setAttribute('tabindex', '-1');
      focus(container);
      return;
    }
    const first = controls[0], last = controls[controls.length - 1];
    const outside = !container.contains(document.activeElement);
    if (event.shiftKey && (document.activeElement === first || outside)) {
      event.preventDefault();
      focus(last);
    } else if (!event.shiftKey && (document.activeElement === last || outside)) {
      event.preventDefault();
      focus(first);
    }
  }

  function init() {
    if (initialized || !document.body) return;
    initialized = true;
    prepareAssistantHelp();
    prepareWorkspaceArtwork();
    document.addEventListener('click', event => {
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (!target) return;
      const viewButton = target.closest('[data-workspace-view]');
      const sectionLink = target.closest('[data-workspace-target]');
      const assistantButton = target.closest('[data-open-assistant]');
      if (viewButton) {
        event.preventDefault();
        showView(viewButton.dataset.workspaceView);
      } else if (sectionLink) {
        const id = sectionLink.dataset.workspaceTarget;
        event.preventDefault();
        reveal(id);
        const section = $(id);
        section?.scrollIntoView?.({block: 'start', behavior: 'auto'});
        if (section && !section.hasAttribute('tabindex')) section.setAttribute('tabindex', '-1');
        focus(section);
      } else if (assistantButton) {
        event.preventDefault();
        openAssistant(assistantButton);
      }
      if (target.closest('#scenario-buttons, #scenario-panel')) scheduleMetrics();
    });
    $('workspace-nav-toggle')?.addEventListener('click', event => {
      if (document.body.classList.contains('workspace-nav-open')) closeNavigation();
      else openNavigation(event.currentTarget);
    });
    $('workspace-nav-scrim')?.addEventListener('click', () => closeNavigation());
    $('workspace-nav-close')?.addEventListener('click', () => closeNavigation());
    $('assistant-close')?.addEventListener('click', () => closeAssistant());
    $('assistant-scrim')?.addEventListener('click', () => closeAssistant());
    window.addEventListener('resize', () => {
      if (window.innerWidth >= 1000 && document.body.classList.contains('workspace-nav-open')) closeNavigation();
    });

    document.addEventListener('keydown', event => {
      if (assistantIsOpen()) {
        if (event.key === 'Escape') {
          event.preventDefault();
          closeAssistant();
        } else if (event.key === 'Tab') trapFocus(event, $('assistant-drawer'));
      } else if (document.body.classList.contains('workspace-nav-open')) {
        if (event.key === 'Escape') {
          event.preventDefault();
          closeNavigation();
        } else if (event.key === 'Tab') trapFocus(event, navigationPanel());
      }
    });
    ['input', 'change'].forEach(type => document.addEventListener(type, event => {
      if (fieldIds.has(event.target?.id)) scheduleMetrics();
    }));
    if (typeof MutationObserver === 'function') {
      const observer = new MutationObserver(scheduleMetrics);
      ['review-clock', 'review-deadline', 'runtime-status'].forEach(id => {
        if ($(id)) observer.observe($(id), {subtree: true, childList: true, characterData: true});
      });
    }
    assistantTriggers().forEach(button => {
      button.setAttribute('aria-expanded', String(assistantIsOpen()));
      button.setAttribute('aria-controls', 'assistant-drawer');
    });
    const initialView = document.querySelector('[data-workspace-view][aria-current="page"]')?.dataset.workspaceView;
    showView(initialView || 'match');
    updateMetrics();
  }

  window.FoodWorkspace = {init, showView, reveal, openAssistant, closeAssistant, updateMetrics};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, {once: true});
  else init();
})();
