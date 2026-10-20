(function (root) {
  'use strict';
  function create({snapshot, show, apply, request = (...args) => fetch(...args)}) {
    let generation = 0, controller = null, draft = null, anchored = null, anchoredPosting = null;
    function invalidate(resetClock = false) {
      generation++;
      controller?.abort();
      controller = null;
      draft = null;
      if (resetClock || snapshot().posting_text !== anchoredPosting) {
        anchored = null;
        anchoredPosting = null;
      }
      show(null, '');
    }
    async function analyze() {
      const input = snapshot();
      if (input.locked) return;
      if (input.posting_text !== anchoredPosting) {
        anchored = null;
        anchoredPosting = null;
      }
      if (input.posting_text.trim().length < 5) {
        show(null, 'Describe the available food first.');
        return;
      }
      const owned = ++generation;
      controller?.abort();
      const active = new AbortController();
      controller = active;
      draft = null;
      show(null, 'Gemini is extracting the declared details…');
      const timer = setTimeout(() => active.abort(), 30000);
      try {
        const existing = [input.deadline_at, anchored].filter(Boolean)
          .sort((a,b)=>Date.parse(a)-Date.parse(b))[0] || null;
        const response = await request('/api/intake/parse', {
          method:'POST', headers:{'Content-Type':'application/json'}, cache:'no-store',
          body:JSON.stringify({posting_text:input.posting_text, deadline_at:existing}),
          signal:active.signal,
        });
        const text = await response.text();
        if (text.length > 24000) throw new Error('oversized');
        const data = JSON.parse(text);
        if (owned !== generation || active.signal.aborted || snapshot().posting_text !== input.posting_text) return;
        if (!response.ok || data.status !== 'review_required' || data.requires_review !== true
            || data.fields?.posting_text !== input.posting_text || !Array.isArray(data.questions)) throw new Error('invalid');
        draft = data;
        if (data.fields.deadline_at && (!anchored || Date.parse(data.fields.deadline_at) < Date.parse(anchored))) {
          anchored = data.fields.deadline_at;
          anchoredPosting = input.posting_text;
        }
        show(data, 'Review the extracted details. Nothing has been applied or sent.');
      } catch (_) {
        if (owned === generation) show(null, 'Extraction did not complete. Your form is unchanged; retry or enter the details directly.');
      } finally {
        clearTimeout(timer);
        if (owned === generation) controller = null;
      }
    }
    function applyReviewed() {
      if (!draft || snapshot().locked || snapshot().posting_text !== draft.fields.posting_text) return false;
      const selected = draft;
      draft = null;
      apply(selected);
      return true;
    }
    return {analyze, applyReviewed, invalidate};
  }
  root.AlertIntake = {create};
  if (typeof module === 'object' && module.exports) module.exports = {create};
})(typeof globalThis !== 'undefined' ? globalThis : this);
