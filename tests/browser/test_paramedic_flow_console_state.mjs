import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizeSelection, recordKey, buildRoleURL, deriveStages, SnapshotPoller} from '../../server/static/flow-console-state.mjs';

const origin = 'http://127.0.0.1:18033';
const patient = {kind: 'patient', id: 'P-1', actorId: 'crew:1'};
const draft = {kind: 'draft', id: 'VS-1', actorId: 'crew:1', version: 3};
const fresh = {kind: 'new', id: null};
const url = (role, selection, extra = {}) => buildRoleURL(role, {origin, selection, ...extra});
const code = (fn, expected) => assert.throws(fn, error => error.code === expected);
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return {promise, resolve, reject}; };
const flush = async () => { for (let i = 0; i < 12; ++i) await Promise.resolve(); };

function fixture(options = {}) {
  let timerId = 0, now = 0;
  const timers = new Map(), requests = [], snapshots = [], errors = [];
  const poller = new SnapshotPoller({
    delay: 100,
    setTimeoutFn: (fn, ms) => { const timer = ++timerId; timers.set(timer, {at: now + ms, fn}); return timer; },
    clearTimeoutFn: timer => timers.delete(timer),
    fetchSnapshot: (context, {signal}) => { const job = deferred(); requests.push({context, signal, ...job}); return job.promise; },
    onSnapshot: (value, context) => snapshots.push({value, context}),
    onError: (error, context) => errors.push({error, context}),
    ...options,
  });
  const tick = async ms => {
    const target = now + ms;
    for (;;) {
      const next = [...timers].filter(([, t]) => t.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush();
    }
    now = target;
  };
  return {poller, requests, snapshots, errors, timers, tick};
}

test('selection normalization is detached and record keys do not mix patients and drafts', () => {
  for (const value of [patient, draft, fresh]) { const normalized = normalizeSelection(value); assert.deepEqual(normalized, value); assert.notEqual(normalized, value); }
  assert.deepEqual(normalizeSelection({kind: 'new'}), fresh);
  assert.equal(recordKey(patient), 'patient:P-1'); assert.equal(recordKey(draft), 'draft:VS-1'); assert.equal(recordKey(fresh), 'new');
});
test('selection rejects invalid kinds, URL identifiers, negative/string versions and untrusted actor ids', () => {
  for (const value of [null, [], {}, {kind: 'unknown', id: 'P1'}, {kind: 'patient', id: ''}, {kind: 'patient', id: '../P1'}, {kind: 'patient', id: 'https://other/P1'}, {kind: 'patient', id: 'P?x=1'}, {kind: 'draft', id: 'VS1', version: -1}, {kind: 'draft', id: 'VS1', version: '3'}, {kind: 'draft', id: 'VS1', actorId: 'bad actor'}, {kind: 'new', id: 'P1'}, {kind: 'new', actorId: 'a'}, {kind: 'patient', id: 'P1', surprise: true}]) code(() => normalizeSelection(value), 'INVALID_SELECTION');
});
test('role URL builder returns only relative allowlisted same-origin URLs', () => {
  assert.equal(url('patient', patient), '/patient/?pid=P-1&actor_id=crew%3A1');
  assert.equal(url('voice', draft), '/flow-console-entry.html?sid=VS-1&actor_id=crew%3A1&version=3');
  assert.equal(url('voice', fresh), '/paramedic/quick-test.html?flow=field');
  assert.equal(url('hospital', patient, {hospitalId: 'H01'}), '/hospital/?hid=H01&pid=P-1');
  for (const role of ['patient', 'voice', 'hospital']) { const result = url(role, patient, {hospitalId: 'H01'}); assert.equal(new URL(result, origin).origin, origin); assert.ok(result.startsWith('/')); }
});
test('registered patient voice URL uses the existing full UI, never a submitted draft bridge', () => {
  assert.equal(url('voice', patient, {voiceSessionId: 'VS-SUBMITTED', version: 9}), '/paramedic/?view=full&flow=field&pid=P-1&actor_id=crew%3A1');
});
test('draft voice URL accepts the exact explicit owner/session/version fallback', () => {
  assert.equal(url('voice', {kind: 'draft', id: 'VS-2'}, {ownerActorId: 'crew-2', voiceSessionId: 'VS-2', version: 4}), '/flow-console-entry.html?sid=VS-2&actor_id=crew-2&version=4');
});
test('role URLs never invent a registered patient for a draft or fresh record', () => {
  code(() => url('patient', draft), 'NO_PATIENT'); assert.equal(url('patient', fresh), '/patient/');
  assert.equal(url('hospital', fresh, {hospitalId: 'H01'}), '/hospital/?hid=H01');
  assert.equal(url('hospital', draft, {hospitalId: 'H01'}), '/hospital/?hid=H01');
});
test('missing owner, draft version and hospital are explicit errors instead of example defaults', () => {
  code(() => url('voice', {kind: 'patient', id: 'P1'}), 'MISSING_ACTOR');
  code(() => url('patient', {kind: 'patient', id: 'P1'}), 'MISSING_ACTOR');
  code(() => url('voice', {kind: 'draft', id: 'VS1'}), 'MISSING_DRAFT_CONTEXT');
  code(() => url('hospital', patient), 'MISSING_HOSPITAL');
});
test('inconsistent scope ids and versions cannot send a draft to another owner or session', () => {
  for (const extra of [{ownerActorId: 'other'}, {voiceSessionId: 'VS-OTHER'}, {version: 4}]) code(() => url('voice', draft, extra), 'CONTEXT_MISMATCH');
  code(() => url('voice', patient, {ownerActorId: 'other'}), 'CONTEXT_MISMATCH');
});
test('invalid role, origin, credentials, paths and external URL-shaped ids are rejected', () => {
  code(() => url('https://other.example/', patient), 'INVALID_ROLE');
  for (const bad of ['not a url', 'file:///tmp/x', 'javascript:alert(1)', 'http://user:pass@127.0.0.1/', 'http://127.0.0.1/path', 'http://127.0.0.1/?x=1', 'http://127.0.0.1/#x']) code(() => buildRoleURL('voice', {origin: bad, selection: fresh}), 'INVALID_ORIGIN');
  code(() => url('hospital', patient, {hospitalId: '//other.example'}), 'INVALID_HOSPITAL');
  code(() => url('voice', draft, {ownerActorId: 'a&b=1'}), 'INVALID_ACTOR');
  code(() => url('voice', draft, {voiceSessionId: '../VS1'}), 'INVALID_SESSION');
  for (const version of [0, -1, 2.5, '2', NaN]) code(() => url('voice', draft, {version}), 'INVALID_VERSION');
});
test('browser origin mismatch is rejected even for a syntactically valid external origin', () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'location');
  try { Object.defineProperty(globalThis, 'location', {configurable: true, value: {origin}}); code(() => buildRoleURL('voice', {origin: 'https://other.example', selection: fresh}), 'EXTERNAL_ORIGIN'); }
  finally { if (previous) Object.defineProperty(globalThis, 'location', previous); else delete globalThis.location; }
});

const stages = (state, inquiries = [], rest = {}) => deriveStages({kind: 'patient', patient: {id: 'P1', state, ...rest}, inquiries});
test('empty/unknown data reports no completed pipeline or fabricated counts', () => {
  for (const selected of [null, undefined, {}, {kind: 'patient', patient: null}, {kind: 'patient', patient: {id: 'P1', state: 'UNKNOWN', departed_at: 1, arrived_at: 2}}, {kind: 'draft', draft: null}]) {
    const value = deriveStages(selected); assert.equal(value.currentStage, null); assert.equal(value.stages.length, 7); assert.ok(value.stages.every(stage => stage.status === 'pending')); assert.deepEqual(Object.keys(value).sort(), ['currentStage', 'stages']);
  }
});
test('a draft remains recording and does not claim review, registration or arrival completion', () => {
  const value = deriveStages({kind: 'draft', draft: {id: 'VS1', actor_id: 'a', version: 2}});
  assert.equal(value.currentStage, 'recording'); assert.equal(value.stages[0].status, 'current'); assert.ok(value.stages.slice(1).every(stage => stage.status === 'pending'));
});
test('registered WAITING without inquiries remains at registration', () => {
  const value = stages('WAITING'); assert.equal(value.currentStage, 'registration'); assert.equal(value.stages[0].status, 'done'); assert.equal(value.stages[1].status, 'current');
});
test('inquiries show the inquiry stage while accepted responses do not confirm a destination', () => {
  assert.equal(stages('WAITING', [{state: 'PENDING'}]).currentStage, 'inquiry');
  const value = stages('WAITING', [{state: 'ACCEPTED'}]); assert.equal(value.currentStage, 'acceptance'); assert.equal(value.stages[4].status, 'pending'); assert.equal(value.stages[5].status, 'pending'); assert.equal(value.stages[6].status, 'pending');
});
test('patient lifecycle controls confirmation, transport and arrival instead of inquiry claims or timestamps', () => {
  assert.equal(stages('WAITING', [{state: 'CONFIRMED'}], {departed_at: 1, arrived_at: 2}).currentStage, 'inquiry');
  assert.equal(stages('CONFIRMED').currentStage, 'confirmation'); assert.equal(stages('IN_TRANSIT', [], {arrived_at: 2}).currentStage, 'transport');
  assert.equal(stages('IN_TRANSIT').stages[6].status, 'pending');
});
test('ARRIVED and COMPLETED finish arrival while cancellation does not imply arrival', () => {
  for (const state of ['ARRIVED', 'COMPLETED']) { const value = stages(state); assert.equal(value.currentStage, 'arrival'); assert.ok(value.stages.every(stage => stage.status === 'done')); }
  const cancelled = stages('CANCELLED'); assert.equal(cancelled.currentStage, null); assert.ok(cancelled.stages.every(stage => stage.status === 'stopped'));
});
test('stage derivation does not mutate data or reuse mutable returned stages', () => {
  const selected = {kind: 'patient', patient: {id: 'P1', state: 'WAITING'}, inquiries: [{state: 'ACCEPTED'}]}, before = structuredClone(selected);
  const value = deriveStages(selected); value.stages[0].label = 'changed'; assert.deepEqual(selected, before); assert.equal(deriveStages(selected).stages[0].label, '기록');
});

test('poller constructor and pre-start context changes do not schedule or request anything', async () => {
  const f = fixture(); assert.equal(f.poller.running, false); assert.equal(f.poller.busy, false); assert.equal(f.poller.snapshot, null);
  await f.poller.setContext({selection: patient}); await f.tick(10000); assert.equal(f.requests.length, 0); assert.equal(f.timers.size, 0);
});
test('start explicitly fetches once and repeated refreshes coalesce without overlap', async () => {
  const f = fixture(), first = f.poller.start({selection: patient}); await flush();
  const repeated = f.poller.refresh(); assert.equal(f.requests.length, 1); assert.equal(f.poller.busy, true);
  f.requests[0].resolve({patients: []}); assert.deepEqual(await first, {patients: []}); assert.deepEqual(await repeated, {patients: []});
  assert.equal(f.snapshots.length, 1); assert.equal(f.poller.busy, false); assert.equal(f.timers.size, 1); f.poller.stop();
});
test('the timer begins only after the previous fetch finishes', async () => {
  const f = fixture(), p = f.poller.start({id: 'P1'}); await flush(); await f.tick(10000); assert.equal(f.requests.length, 1); assert.equal(f.timers.size, 0);
  f.requests[0].resolve({patients: []}); await p; await f.tick(99); assert.equal(f.requests.length, 1); await f.tick(1); assert.equal(f.requests.length, 2); f.poller.stop(); f.requests[1].resolve({}); await flush();
});
test('rapid patient and hospital changes abort old scope and fetch only the latest after its settlement', async () => {
  const f = fixture(), a = f.poller.start({pid: 'A', hid: 'H01'}); await flush();
  const b = f.poller.setContext({pid: 'B', hid: 'H01'}), c = f.poller.setContext({pid: 'B', hid: 'H02'});
  assert.equal(await b, null); assert.equal(f.requests.length, 1); assert.equal(f.requests[0].signal.aborted, true); assert.equal(f.poller.snapshot, null);
  f.requests[0].resolve({selected: {patient: {id: 'A'}}}); assert.equal(await a, null); await flush(); assert.equal(f.requests.length, 2); assert.deepEqual(f.requests[1].context, {hid: 'H02', pid: 'B'});
  f.requests[1].resolve({selected: {patient: {id: 'B'}}}); await c; assert.equal(f.snapshots.length, 1); assert.equal(f.snapshots[0].value.selected.patient.id, 'B'); f.poller.stop();
});
test('late errors from a previous patient never replace the new patient error/status', async () => {
  const f = fixture(), a = f.poller.start({pid: 'A'}); await flush(); const b = f.poller.setContext({pid: 'B'});
  f.requests[0].reject(new Error('old patient')); await a; await flush(); assert.equal(f.errors.length, 0);
  f.requests[1].resolve({selected: {patient: {id: 'B'}}}); await b; assert.equal(f.errors.length, 0); f.poller.stop();
});
test('same-context failures report error and preserve the last successful snapshot', async () => {
  const f = fixture(), a = f.poller.start({pid: 'A'}); await flush(); f.requests[0].resolve({selected: {patient: {id: 'A', state: 'WAITING'}}}); await a;
  const previous = f.poller.snapshot, b = f.poller.refresh(); await flush(); f.requests[1].reject(new Error('offline')); assert.equal(await b, null);
  assert.deepEqual(f.poller.snapshot, previous); assert.equal(f.errors.length, 1); assert.deepEqual(f.errors[0].context, {pid: 'A'}); assert.equal(f.timers.size, 1); f.poller.stop();
});
test('changing context clears the old displayed snapshot instead of leaking another patient', async () => {
  const f = fixture(), p = f.poller.start({pid: 'A'}); await flush(); f.requests[0].resolve({selected: {patient: {id: 'A'}}}); await p;
  const q = f.poller.setContext({pid: 'B'}); assert.equal(f.poller.snapshot, null); await flush(); f.requests[1].reject(new Error('B offline')); await q; assert.equal(f.poller.snapshot, null); f.poller.stop();
});
test('stop invalidates callbacks, aborts in-flight work and cancels future timer polling', async () => {
  const f = fixture(), p = f.poller.start({pid: 'A'}); await flush(); f.poller.stop(); assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve({selected: {patient: {id: 'A'}}}); assert.equal(await p, null); await f.tick(10000); assert.equal(f.snapshots.length, 0); assert.equal(f.requests.length, 1); assert.equal(f.poller.running, false);
});
test('stop immediately after start prevents even the queued fetch invocation', async () => {
  const f = fixture(), p = f.poller.start({pid: 'A'}); f.poller.stop(); await p; assert.equal(f.requests.length, 0); assert.equal(f.timers.size, 0);
});
test('hidden state pauses reads and discards in-flight completion until explicit visible resume', async () => {
  const f = fixture(), a = f.poller.start({pid: 'A'}); await flush(); await f.poller.setVisible(false); assert.equal(f.requests[0].signal.aborted, true);
  f.requests[0].resolve({patients: ['old']}); await a; await f.tick(10000); assert.equal(await f.poller.refresh(), null); assert.equal(f.requests.length, 1); assert.equal(f.snapshots.length, 0);
  const b = f.poller.setVisible(true); await flush(); assert.equal(f.requests.length, 2); f.requests[1].resolve({patients: []}); await b; assert.equal(f.snapshots.length, 1); f.poller.stop();
});
test('without AbortController, generation invalidation still discards old responses and avoids overlap', async () => {
  const f = fixture({AbortControllerRef: null}), a = f.poller.start({pid: 'A'}); await flush(); assert.equal(f.requests[0].signal, undefined);
  const b = f.poller.setContext({pid: 'B'}); assert.equal(f.requests.length, 1); f.requests[0].resolve({old: true}); await a; await flush(); f.requests[1].resolve({current: true}); await b; assert.deepEqual(f.poller.snapshot, {current: true}); f.poller.stop();
});
test('canonical equivalent contexts do not trigger another request or invalidate the current result', async () => {
  const f = fixture(), a = f.poller.start({pid: 'A', nested: {b: 2, a: 1}}); await flush();
  await f.poller.setContext({nested: {a: 1, b: 2}, pid: 'A'}); assert.equal(f.requests[0].signal.aborted, false); assert.equal(f.requests.length, 1);
  f.requests[0].resolve({ok: true}); await a; assert.equal(f.snapshots.length, 1); f.poller.stop();
});
test('contexts, snapshots and callback projections cannot mutate retained data', async () => {
  const context = {pid: 'A', nested: {x: 1}}, f = fixture(), a = f.poller.start(context); context.nested.x = 2; await flush(); assert.equal(f.requests[0].context.nested.x, 1);
  const serverValue = {selected: {patient: {id: 'A'}}}; f.requests[0].resolve(serverValue); await a; serverValue.selected.patient.id = 'X';
  f.snapshots[0].value.selected.patient.id = 'Y'; const observed = f.poller.snapshot; observed.selected.patient.id = 'Z'; assert.equal(f.poller.snapshot.selected.patient.id, 'A');
  const observedContext = f.poller.context; observedContext.nested.x = 99; assert.equal(f.poller.context.nested.x, 1); f.poller.stop();
});
test('malformed snapshots preserve last good data and report an error', async () => {
  const f = fixture(), a = f.poller.start({}); await flush(); f.requests[0].resolve({ok: true}); await a;
  for (const value of [null, [], undefined, 'bad']) { const p = f.poller.refresh(); await flush(); f.requests.at(-1).resolve(value); assert.equal(await p, null); assert.deepEqual(f.poller.snapshot, {ok: true}); }
  assert.equal(f.errors.length, 4); f.poller.stop();
});
test('synchronous fetch and rendering errors cannot strand the single-flight lock', async () => {
  let calls = 0; const errors = [];
  const f = fixture({fetchSnapshot: () => { if (++calls === 1) throw new Error('sync'); return {ok: true}; }, onSnapshot: () => { throw new Error('render'); }, onError: error => errors.push(error)});
  assert.equal(await f.poller.start({}), null); assert.equal(f.poller.busy, false); assert.equal(errors.length, 1);
  assert.deepEqual(await f.poller.refresh(), {ok: true}); assert.equal(f.poller.busy, false); f.poller.stop();
});
test('null context stops scheduling and never produces a request for an invented record', async () => {
  const f = fixture(), p = f.poller.start({pid: 'A'}); await flush(); f.requests[0].resolve({ok: true}); await p;
  await f.poller.setContext(null); await f.tick(10000); assert.equal(f.poller.snapshot, null); assert.equal(f.requests.length, 1); assert.equal(await f.poller.refresh(), null); f.poller.stop();
});
test('invalid polling configuration and context fail before scheduling requests', async () => {
  assert.throws(() => new SnapshotPoller({}), /조회 설정/);
  for (const delay of [0, -1, NaN, Infinity]) assert.throws(() => fixture({delay}), /조회 설정/);
  const f = fixture(); for (const context of [undefined, 'A', [], {x: NaN}, {fn: () => {}}, {x: undefined}]) code(() => f.poller.setContext(context), 'INVALID_CONTEXT');
  const cycle = {}; cycle.self = cycle; code(() => f.poller.setContext(cycle), 'INVALID_CONTEXT'); assert.equal(f.requests.length + f.timers.size, 0);
});
test('the state module itself performs no fetch, POST, storage or DOM action', async () => {
  const before = globalThis.fetch; let calls = 0;
  try { globalThis.fetch = () => { ++calls; throw new Error('global IO forbidden'); }; const f = fixture(); const p = f.poller.start({selection: fresh}); await flush(); f.requests[0].resolve({patients: [], drafts: [], hospitals: [], selected: null}); await p; f.poller.stop(); assert.equal(calls, 0); }
  finally { globalThis.fetch = before; }
});

test('browser receiver-sensitive timer methods are invoked with globalThis instead of the poller', async () => {
  let scheduled = 0, cancelled = 0;
  const f = fixture({
    setTimeoutFn: function(callback, delay) { assert.equal(this, globalThis); assert.equal(typeof callback, 'function'); assert.equal(delay, 100); return ++scheduled; },
    clearTimeoutFn: function(timer) { assert.equal(this, globalThis); assert.equal(timer, 1); ++cancelled; },
  });
  const p = f.poller.start({pid: 'A'}); await flush(); f.requests[0].resolve({ok: true}); await p;
  assert.equal(scheduled, 1); f.poller.stop(); assert.equal(cancelled, 1);
});
