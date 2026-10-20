// Offline tests use browser-native response streams; no network or model calls.
const assert = require('node:assert/strict');
const test = require('node:test');
const {OfferClock, readMatchStream} = require('../agent/ui/offer-workflow.js');

const encoder = new TextEncoder();
const start = Date.parse('2026-10-20T12:00:00Z');
const hour = 3600000;
const result = {status: 'draft', winner: {name: 'Café 食 🍎'}, requires_confirmation: true};

function record(sequence, fields, runId = 'match-1') {
  return {run_id: runId, sequence, ...fields};
}
function line(sequence, fields, runId) {
  return JSON.stringify(record(sequence, fields, runId)) + '\n';
}
function responseFromChunks(chunks, {keepOpen = false, contentType = 'application/x-ndjson'} = {}) {
  let next = 0, cancellations = 0;
  const body = new ReadableStream({
    pull(controller) {
      if (next < chunks.length) {
        const chunk = chunks[next++];
        controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk);
      } else if (!keepOpen) {
        controller.close();
      }
    },
    cancel() { cancellations += 1; },
  });
  return {
    response: new Response(body, {headers: {'content-type': contentType}}),
    get cancellations() { return cancellations; },
  };
}
function eventsResponse(events, options) {
  return responseFromChunks(events.map(event => JSON.stringify(event) + '\n'), options);
}

test('retrying an offer preserves its original deadline as time elapses', () => {
  let now = start;
  const clock = new OfferClock(() => now);
  assert.equal(clock.forOffer(6), '2026-10-20T18:00:00.000Z');
  now += 2 * hour;
  assert.equal(clock.forOffer(6), '2026-10-20T18:00:00.000Z');
  assert.throws(() => clock.forOffer(8), /Confirm the changed pickup window/);
  assert.equal(clock.forOffer(6), '2026-10-20T18:00:00.000Z');
});

test('only an explicit reset starts a fresh typed pickup window', () => {
  let now = start;
  const clock = new OfferClock(() => now);
  clock.forOffer(6);
  now += 2 * hour;
  clock.reset();
  assert.equal(clock.forOffer(3), '2026-10-20T17:00:00.000Z');
  now += hour;
  assert.equal(clock.forOffer(3), '2026-10-20T17:00:00.000Z');
});

test('a reviewed voice offer retains its exact timezone-qualified deadline', () => {
  let now = start;
  const clock = new OfferClock(() => now);
  clock.forOffer(6);
  const voiceDeadline = '2026-10-20T13:45:30.250-04:00';
  clock.apply(voiceDeadline, 5.75);
  assert.equal(clock.forOffer(5.75), voiceDeadline);
  now += hour;
  assert.equal(clock.forOffer(5.75), voiceDeadline);
  assert.throws(() => clock.forOffer(6), /Confirm the changed pickup window/);
});

test('expiry blocks retry without silently extending the donor deadline', () => {
  let now = start;
  const clock = new OfferClock(() => now);
  const fixed = clock.forOffer(1);
  now += hour;
  assert.throws(() => clock.forOffer(1), /deadline has passed/);
  assert.equal(clock.deadline, fixed);
  clock.reset();
  assert.equal(clock.forOffer(1), '2026-10-20T14:00:00.000Z');
});

test('invalid voice deadlines cannot replace the existing offer clock', () => {
  const clock = new OfferClock(() => start);
  const fixed = clock.forOffer(6);
  for (const deadline of [
    '2026-10-20T14:00:00', 'not-a-date', '2026-10-20T12:00:00Z',
    '2026-10-19T12:00:00Z', '2026-10-27T12:00:00.001Z', null,
  ]) {
    assert.throws(() => clock.apply(deadline, 6), /future pickup deadline with its timezone/);
    assert.equal(clock.forOffer(6), fixed);
  }
  clock.apply('2026-10-27T12:00:00Z', 168);
  assert.equal(clock.forOffer(168), '2026-10-27T12:00:00Z');
});

test('typed windows reject nonnumeric, nonfinite and out-of-range hours', () => {
  const clock = new OfferClock(() => start);
  for (const hours of [0, -1, 168.01, NaN, Infinity, '6', null]) {
    assert.throws(() => clock.forOffer(hours), /greater than zero and at most 168 hours/);
    assert.equal(clock.deadline, null);
  }
  assert.equal(clock.forOffer(0.25), '2026-10-20T12:15:00.000Z');
});

test('NDJSON decoding preserves Unicode split across individual byte chunks', async () => {
  const runId = 'match-é-🍎';
  const raw = line(1, {type: 'progress', stage: 'accepted'}, runId)
    + line(2, {type: 'result', data: result}, runId);
  const bytes = encoder.encode(raw);
  const chunks = Array.from(bytes, byte => Uint8Array.of(byte));
  const transport = responseFromChunks(chunks, {keepOpen: true});
  const progress = [];
  assert.deepEqual(await readMatchStream(
    transport.response, (stage, details) => progress.push({stage, details})
  ), result);
  assert.deepEqual(progress, [{stage: 'accepted', details: {candidate_count: null}}]);
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('real progress is delivered while the terminal result is still pending', async () => {
  let controller;
  const stream = new ReadableStream({start(value) { controller = value; }});
  const response = new Response(stream, {headers: {'content-type': 'application/x-ndjson'}});
  const receivedProgress = Promise.withResolvers();
  let resolved = false;
  const pending = readMatchStream(response, (stage, details) => {
    receivedProgress.resolve({stage, details});
  }).then(value => { resolved = true; return value; });
  controller.enqueue(encoder.encode(line(1, {
    type: 'progress', stage: 'checking_constraints', candidate_count: 4,
  })));
  assert.deepEqual(await receivedProgress.promise, {
    stage: 'checking_constraints', details: {candidate_count: 4},
  });
  assert.equal(resolved, false, 'The UI must not wait for the final result to show progress');
  controller.enqueue(encoder.encode(line(2, {type: 'result', data: result})));
  assert.deepEqual(await pending, result);
  assert.equal(response.body.locked, false);
});

test('heartbeat and unknown progress fields cannot become UI instructions', async () => {
  const transport = eventsResponse([
    record(1, {type: 'heartbeat', message: '<script>untrusted</script>'}),
    record(2, {type: 'progress', stage: 'invented_stage', candidate_count: 3}),
    record(3, {type: 'progress', stage: 'retrieving', candidate_count: 7, message: 'private'}),
    record(4, {type: 'result', data: result}),
  ]);
  const progress = [];
  assert.deepEqual(await readMatchStream(
    transport.response, (stage, details) => progress.push({stage, details})
  ), result);
  assert.deepEqual(progress, [{stage: 'retrieving', details: {candidate_count: 7}}]);
});

test('progress accepts bounded integer counts and rejects malformed counts', async () => {
  const counts = [0, 20, 1000000, 1000001, -1, 0.5, '4', null];
  const transport = eventsResponse([
    ...counts.map((candidate_count, index) => record(index + 1, {
      type: 'progress', stage: 'checking_constraints', candidate_count,
    })),
    record(counts.length + 1, {type: 'result', data: result}),
  ]);
  const received = [];
  await readMatchStream(transport.response, (_, details) => received.push(details.candidate_count));
  assert.deepEqual(received, [0, 20, 1000000, null, null, null, null, null]);
});

test('a complete final record is accepted without a trailing newline', async () => {
  const transport = responseFromChunks([
    '\r\n' + line(1, {type: 'progress', stage: 'accepted'}).replace('\n', '\r\n'),
    JSON.stringify(record(2, {type: 'result', data: result})),
  ]);
  assert.deepEqual(await readMatchStream(transport.response, () => {}), result);
});

test('mismatched run IDs and invalid sequence numbers are rejected', async t => {
  for (const [name, invalid] of [
    ['another run', record(2, {type: 'result', data: result}, 'other-run')],
    ['missing run', {sequence: 2, type: 'result', data: result}],
    ['empty run', record(2, {type: 'result', data: result}, '')],
    ['non-string run', record(2, {type: 'result', data: result}, 123)],
    ['duplicate sequence', record(1, {type: 'result', data: result})],
    ['reversed sequence', record(0, {type: 'result', data: result})],
    ['fractional sequence', record(1.5, {type: 'result', data: result})],
    ['string sequence', record('2', {type: 'result', data: result})],
    ['unsafe sequence', record(Number.MAX_SAFE_INTEGER + 1, {type: 'result', data: result})],
    ['non-object event', null],
  ]) {
    await t.test(name, async () => {
      const transport = eventsResponse([
        record(1, {type: 'progress', stage: 'accepted'}), invalid,
      ], {keepOpen: true});
      await assert.rejects(
        readMatchStream(transport.response, () => {}), /could not be verified/
      );
      assert.equal(transport.cancellations, 1);
      assert.equal(transport.response.body.locked, false);
    });
  }
});

test('malformed JSON and invalid terminal data are rejected', async t => {
  await t.test('truncated JSON', async () => {
    const transport = responseFromChunks(['{"run_id":"match-1",']);
    await assert.rejects(readMatchStream(transport.response, () => {}), /response was incomplete/);
    assert.equal(transport.response.body.locked, false);
  });
  for (const data of [null, [], 'success', 1]) {
    await t.test(`invalid result ${JSON.stringify(data)}`, async () => {
      const transport = eventsResponse([record(1, {type: 'result', data})]);
      await assert.rejects(readMatchStream(transport.response, () => {}), /invalid result/);
    });
  }
});

test('a stream that ends without a terminal result preserves retry deadline guidance', async () => {
  const transport = eventsResponse([
    record(1, {type: 'progress', stage: 'accepted'}),
    record(2, {type: 'heartbeat'}),
  ]);
  await assert.rejects(
    readMatchStream(transport.response, () => {}),
    /closed before a result arrived.*same pickup deadline/
  );
  assert.equal(transport.response.body.locked, false);
});

test('match_timeout uses the server code and never displays an untrusted message', async () => {
  const transport = eventsResponse([record(1, {
    type: 'error', code: 'match_timeout', message: 'private provider token=SECRET',
  })]);
  await assert.rejects(readMatchStream(transport.response, () => {}), error => {
    assert.match(error.message, /time limit.*same pickup deadline/);
    assert.doesNotMatch(error.message, /private|SECRET/);
    return true;
  });
});

test('broker failure messages are sanitized at the browser boundary', async () => {
  const transport = eventsResponse([record(1, {
    type: 'error', code: 'match_failed', message: 'database URI and private offer',
    traceback: 'provider internals',
  })], {keepOpen: true});
  await assert.rejects(readMatchStream(transport.response, () => {}), error => {
    assert.equal(error.message, 'The live broker could not complete this check. Please retry.');
    return true;
  });
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('abort cancels a pending native reader and releases its lock', async () => {
  const transport = responseFromChunks([], {keepOpen: true});
  const abort = new AbortController();
  const pending = readMatchStream(transport.response, () => {}, abort.signal);
  abort.abort();
  await assert.rejects(pending, {name: 'AbortError', message: 'Check canceled.'});
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('an already aborted request cannot consume a queued result', async () => {
  const transport = eventsResponse([record(1, {type: 'result', data: result})], {keepOpen: true});
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(
    readMatchStream(transport.response, () => {}, abort.signal), {name: 'AbortError'}
  );
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('delayed underlying cleanup cannot hold a completed result or reader lock', async () => {
  const cleanupStarted = Promise.withResolvers();
  const finishCleanup = Promise.withResolvers();
  const response = new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(line(1, {type: 'result', data: result})));
    },
    cancel() {
      cleanupStarted.resolve();
      return finishCleanup.promise;
    },
  }), {headers: {'content-type': 'application/x-ndjson'}});
  let settled = false;
  const pending = readMatchStream(response, () => {}).then(value => {
    settled = true;
    return value;
  });
  try {
    await cleanupStarted.promise;
    await new Promise(setImmediate);
    assert.equal(settled, true, 'A result must not await advisory source cleanup');
    assert.equal(response.body.locked, false);
    assert.deepEqual(await pending, result);
  } finally {
    // A failed cleanup must be observed without becoming an unhandled rejection.
    finishCleanup.reject(new Error('private cleanup failure'));
    await pending;
    await new Promise(setImmediate);
  }
});

test('delayed underlying cleanup cannot hold caller cancellation', async () => {
  const cleanupStarted = Promise.withResolvers();
  const finishCleanup = Promise.withResolvers();
  const response = new Response(new ReadableStream({
    cancel() {
      cleanupStarted.resolve();
      return finishCleanup.promise;
    },
  }), {headers: {'content-type': 'application/x-ndjson'}});
  const abort = new AbortController();
  let rejected;
  const pending = readMatchStream(response, () => {}, abort.signal).catch(error => {
    rejected = error;
  });
  try {
    abort.abort();
    await cleanupStarted.promise;
    await new Promise(setImmediate);
    assert.equal(rejected?.name, 'AbortError');
    assert.equal(response.body.locked, false);
  } finally {
    finishCleanup.resolve();
    await pending;
  }
});

test('cancellation observed during final cleanup discards the parsed result', async () => {
  const abort = new AbortController();
  const response = new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(line(1, {type: 'result', data: result})));
    },
    cancel() { abort.abort(); },
  }), {headers: {'content-type': 'application/x-ndjson'}});
  await assert.rejects(
    readMatchStream(response, () => {}, abort.signal), {name: 'AbortError'}
  );
  assert.equal(response.body.locked, false);
});

test('oversized unterminated records are rejected and cancel the reader', async () => {
  const transport = responseFromChunks([' '.repeat(2 * 1024 * 1024 + 1)], {keepOpen: true});
  await assert.rejects(readMatchStream(transport.response, () => {}), /exceeded its size limit/);
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('total byte limit applies across multiple individually valid records', async () => {
  const chunks = Array.from({length: 9}, (_, index) => line(index + 1, {
    type: 'heartbeat', padding: 'x'.repeat(512 * 1024),
  }));
  chunks.push(line(10, {type: 'result', data: result}));
  const transport = responseFromChunks(chunks, {keepOpen: true});
  await assert.rejects(readMatchStream(transport.response, () => {}), /exceeded its size limit/);
  assert.equal(transport.cancellations, 1);
  assert.equal(transport.response.body.locked, false);
});

test('a nonstream response cannot be mistaken for a live progress connection', async () => {
  const transport = responseFromChunks([JSON.stringify(result)], {contentType: 'application/json'});
  await assert.rejects(readMatchStream(transport.response, () => {}), /progress connection is unavailable/);
  assert.equal(transport.response.body.locked, false);
});
