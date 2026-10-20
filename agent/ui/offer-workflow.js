/* Shared clock and bounded transport for typed and reviewed voice offers. */
(function (root) {
  'use strict';
  class OfferClock {
    constructor(now = () => Date.now()) {
      this.now = now;
      this.reset();
    }
    reset() { this.deadline = null; this.hours = null; }
    apply(deadline, hours) {
      const instant = Date.parse(deadline);
      if (typeof deadline !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/i.test(deadline)
          || !Number.isFinite(instant) || instant <= this.now()
          || instant > this.now() + 168 * 3600000) {
        throw new Error('Confirm a future pickup deadline with its timezone.');
      }
      this.deadline = deadline;
      this.hours = hours;
    }
    forOffer(hours) {
      if (!Number.isFinite(hours) || hours <= 0 || hours > 168) {
        throw new Error('Enter a pickup window greater than zero and at most 168 hours.');
      }
      // Only reset() for an explicit hours edit/new scenario or apply() for a
      // reviewed voice draft may replace an existing clock.
      if (!this.deadline) {
        this.deadline = new Date(this.now() + hours * 3600000).toISOString();
        this.hours = hours;
      }
      if (this.hours !== hours) {
        throw new Error('Confirm the changed pickup window before matching.');
      }
      if (Date.parse(this.deadline) <= this.now()) {
        throw new Error('The pickup deadline has passed. Confirm a new window with the donor.');
      }
      return this.deadline;
    }
  }

  const stages = {
    accepted: 'Offer received. Starting the live check…',
    connecting: 'Connecting to the recipient data source…',
    retrieving: 'Finding candidate recipients…',
    checking_constraints: 'Checking storage, capacity and the pickup deadline…',
    preparing_brief: 'Preparing the proposed match brief…',
    finalizing: 'Rechecking the deadline before returning the result…',
  };

  async function readMatchStream(response, onProgress, signal) {
    if (!response.ok) {
      let data = {};
      try { data = await response.json(); } catch (_) {}
      const detail = Array.isArray(data.detail)
        ? data.detail.map(item => item.msg).join('; ') : data.detail;
      throw new Error(data.message || detail || `Request failed (${response.status}).`);
    }
    if (!response.headers?.get('content-type')?.includes('application/x-ndjson') || !response.body?.getReader) {
      throw new Error('The live progress connection is unavailable. Retry after the service is ready.');
    }
    const reader = response.body.getReader(), decoder = new TextDecoder();
    let buffer = '', received = 0, runId = null, lastSequence = 0;
    const abort = () => { reader.cancel().catch(() => {}); };
    signal?.addEventListener('abort', abort, {once: true});
    function checkAborted() {
      if (signal?.aborted) {
        const error = new Error('Check canceled.');
        error.name = 'AbortError';
        throw error;
      }
    }
    function parse(line) {
      if (!line.trim()) return null;
      let event;
      try { event = JSON.parse(line); } catch (_) { throw new Error('The live progress response was incomplete. Please retry.'); }
      if (!event || typeof event !== 'object' || typeof event.run_id !== 'string'
          || !event.run_id || !Number.isSafeInteger(event.sequence) || event.sequence <= lastSequence
          || (runId !== null && event.run_id !== runId)) {
        throw new Error('The live progress response could not be verified. Please retry.');
      }
      runId = event.run_id;
      lastSequence = event.sequence;
      if (event.type === 'progress' && Object.hasOwn(stages, event.stage)) {
        onProgress(event.stage, {
          candidate_count: Number.isSafeInteger(event.candidate_count) && event.candidate_count >= 0 && event.candidate_count <= 1000000
            ? event.candidate_count : null,
        });
      } else if (event.type === 'result') {
        if (!event.data || typeof event.data !== 'object' || Array.isArray(event.data)) {
          throw new Error('The live check returned an invalid result. Please retry.');
        }
        return event.data;
      } else if (event.type === 'error') {
        throw new Error(event.code === 'match_timeout'
          ? 'The live check reached its time limit. Retry uses the same pickup deadline.'
          : 'The live broker could not complete this check. Please retry.');
      }
      return null;
    }
    try {
      checkAborted();
      while (true) {
        const {value, done} = await reader.read();
        checkAborted();
        received += value?.byteLength || 0;
        if (received > 4 * 1024 * 1024) throw new Error('The live response exceeded its size limit.');
        buffer += decoder.decode(value, {stream: !done});
        if (buffer.length > 2 * 1024 * 1024) throw new Error('The live response exceeded its size limit.');
        let newline;
        while ((newline = buffer.indexOf('\n')) >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          const result = parse(line);
          if (result !== null) return result;
        }
        if (done) {
          if (buffer.trim()) {
            const result = parse(buffer);
            if (result !== null) return result;
          }
          throw new Error('The live connection closed before a result arrived. Retry uses the same pickup deadline.');
        }
      }
    } finally {
      signal?.removeEventListener('abort', abort);
      // Native cancellation closes the reader immediately; the underlying
      // cleanup promise is advisory and must not hold the UI open.
      try { reader.cancel().catch(() => {}); } catch (_) {}
      reader.releaseLock();
      checkAborted();
    }
  }
  const api = {OfferClock, readMatchStream, stages};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.OfferWorkflow = api;
})(typeof globalThis === 'undefined' ? this : globalThis);
