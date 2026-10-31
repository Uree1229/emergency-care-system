import test from 'node:test';
import assert from 'node:assert/strict';
import {DRAFT_HANDOFF_KEY, writeDraftHandoff, readDraftHandoff, clearDraftHandoff} from './paramedic-ui/draft-handoff.mjs';

const NOW = 1_800_000_000_000;
const ORIGIN = 'http://127.0.0.1:18042';
const TTL = 600_000;
const input = overrides => ({serverOrigin: ORIGIN, actorId: 'QUICK-VOICE-test', sessionId: 'VS-test', version: 2, unappliedText: '', intent: 'recommend', ...overrides});
function memory(initial = {}) {
  const data = new Map(Object.entries(initial));
  return {data, getItem: key => data.has(key) ? data.get(key) : null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key)};
}
function saved(overrides = {}) {
  return {...input(), createdAt: NOW, expiresAt: NOW + TTL, ...overrides};
}
function storeRaw(value) { return memory({[DRAFT_HANDOFF_KEY]: typeof value === 'string' ? value : JSON.stringify(value)}); }

test('write normalizes a root origin, assigns its own TTL, and returns a fresh object', () => {
  const storage = memory();
  const original = input({serverOrigin: 'HTTP://LOCALHOST:80/'});
  const result = writeDraftHandoff(storage, original, {now: NOW});
  assert.deepEqual(result, {...saved(), serverOrigin: 'http://localhost'});
  assert.notEqual(result, original);
  assert.deepEqual(JSON.parse(storage.getItem(DRAFT_HANDOFF_KEY)), result);
  assert.equal(original.createdAt, undefined);
});

test('an omitted unappliedText becomes empty, while supplied text is preserved exactly', () => {
  const storage = memory();
  const withoutText = input(); delete withoutText.unappliedText;
  assert.equal(writeDraftHandoff(storage, withoutText, {now: NOW}).unappliedText, '');
  const tail = '  synthetic pending line\n\tends here  ';
  writeDraftHandoff(storage, input({unappliedText: tail}), {now: NOW});
  assert.equal(readDraftHandoff(storage, ORIGIN, {now: NOW}).payload.unappliedText, tail);
});

test('4000 pending characters including JSON escapes fit; 4001 fail without overwriting', () => {
  const storage = memory();
  const exact = '\u0000'.repeat(4000);
  writeDraftHandoff(storage, input({unappliedText: exact}), {now: NOW});
  const before = storage.getItem(DRAFT_HANDOFF_KEY);
  assert.equal(readDraftHandoff(storage, ORIGIN, {now: NOW}).payload.unappliedText, exact);
  assert.throws(() => writeDraftHandoff(storage, input({unappliedText: 'x'.repeat(4001)}), {now: NOW}), /4000/);
  assert.equal(storage.getItem(DRAFT_HANDOFF_KEY), before);
});

for (const [field, limit] of [['actorId', 80], ['sessionId', 128]]) {
  test(`${field} accepts its exact ASCII ID limit and rejects oversize or unsafe IDs`, () => {
    const storage = memory();
    writeDraftHandoff(storage, input({[field]: 'a'.repeat(limit)}), {now: NOW});
    for (const value of ['', 'a'.repeat(limit + 1), '한글', 'a b', 'a/b', 'a?b', 'a\nb']) {
      assert.throws(() => writeDraftHandoff(storage, input({[field]: value}), {now: NOW}), /identifier/);
    }
  });
}

test('version is a positive safe integer and intent can only request recommendation preparation', () => {
  for (const version of [0, -1, 1.5, '2', Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => writeDraftHandoff(memory(), input({version}), {now: NOW}), /version/);
  }
  for (const intent of ['', 'submit', 'https://other.test/', null]) {
    assert.throws(() => writeDraftHandoff(memory(), input({intent}), {now: NOW}), /intent/);
  }
});

for (const serverOrigin of ['https://example.test/path', 'https://example.test/?q=1', 'https://example.test/?', 'https://example.test/#x', 'https://example.test/#', 'https://user:password@example.test', 'ftp://example.test', 'javascript:alert(1)', '//example.test', ' https://example.test', 'https://example.test\\path']) {
  test(`rejects non-root or unsafe origin: ${serverOrigin}`, () => {
    assert.throws(() => writeDraftHandoff(memory(), input({serverOrigin}), {now: NOW}), /origin/);
  });
}

test('write rejects unknown/raw clinical fields and caller-supplied lifetime', () => {
  for (const extra of [{rawAudio: 'synthetic'}, {patient: {name: 'synthetic'}}, {transcript: 'synthetic'}, {createdAt: NOW}, {expiresAt: NOW + TTL}]) {
    const storage = memory();
    assert.throws(() => writeDraftHandoff(storage, input(extra), {now: NOW}), /schema/);
    assert.equal(storage.getItem(DRAFT_HANDOFF_KEY), null);
  }
});

test('valid read is non-destructive, independently parsed, and stays valid until the TTL boundary', () => {
  const storage = memory();
  writeDraftHandoff(storage, input(), {now: NOW});
  const before = new Map(storage.data);
  const first = readDraftHandoff(storage, ORIGIN + '/', {now: NOW + TTL - 1});
  assert.equal(first.status, 'valid');
  first.payload.actorId = 'mutated-in-ram';
  assert.equal(readDraftHandoff(storage, ORIGIN, {now: NOW}).payload.actorId, 'QUICK-VOICE-test');
  assert.deepEqual(storage.data, before);
});

test('expiry at exactly ten minutes gives no payload and does not consume the saved reference', () => {
  const storage = storeRaw(saved({unappliedText: 'synthetic pending'}));
  const before = storage.getItem(DRAFT_HANDOFF_KEY);
  assert.deepEqual(readDraftHandoff(storage, ORIGIN, {now: NOW + TTL}), {status: 'expired', payload: null, message: 'Saved draft handoff has expired.'});
  assert.equal(storage.getItem(DRAFT_HANDOFF_KEY), before);
});

test('read requires the same normalized origin including protocol and port', () => {
  const storage = storeRaw(saved());
  for (const origin of ['http://127.0.0.1:18043', 'https://127.0.0.1:18042', 'http://other.test:18042', ORIGIN + '/redirect']) {
    const result = readDraftHandoff(storage, origin, {now: NOW});
    assert.equal(result.status, 'invalid'); assert.equal(result.payload, null);
  }
});

test('read rejects future creation, extended or inconsistent TTL, and invalid timestamps', () => {
  for (const patch of [{createdAt: NOW + 1, expiresAt: NOW + TTL + 1}, {expiresAt: NOW + TTL + 1}, {createdAt: -1, expiresAt: TTL - 1}, {createdAt: '0'}, {expiresAt: null}]) {
    const result = readDraftHandoff(storeRaw(saved(patch)), ORIGIN, {now: NOW});
    assert.equal(result.status, 'invalid'); assert.equal(result.payload, null);
  }
});

test('malformed, oversize and invalid stored schemas return no clinical payload', () => {
  const missing = saved(); delete missing.unappliedText;
  for (const raw of ['', '{broken', 'null', '[]', '0', 'x'.repeat(32769), missing, saved({unappliedText: 'x'.repeat(4001)}), saved({patient: 'synthetic'}), saved({intent: 'submit'}), saved({serverOrigin: 'http://other.test'})]) {
    const result = readDraftHandoff(storeRaw(raw), ORIGIN, {now: NOW});
    assert.equal(result.status, 'invalid'); assert.equal(result.payload, null);
  }
});

test('only a truly missing entry is absent; unavailable storage is explicitly invalid', () => {
  assert.equal(readDraftHandoff(memory(), ORIGIN, {now: NOW}).status, 'absent');
  for (const storage of [null, {}, {getItem() { throw new Error('denied'); }}, {getItem() { return undefined; }}]) {
    const result = readDraftHandoff(storage, ORIGIN, {now: NOW});
    assert.equal(result.status, 'invalid'); assert.equal(result.payload, null);
  }
});

test('write storage errors are explicit errors, not apparent success', () => {
  for (const storage of [null, {}, {setItem() { throw new Error('QuotaExceededError'); }}, {setItem() { throw new Error('SecurityError'); }}]) {
    assert.throws(() => writeDraftHandoff(storage, input(), {now: NOW}), /could not be saved/);
  }
});

test('write, read and explicit clear preserve actor, session, server and unrelated keys', () => {
  const other = {'paramedic.voice.actor': 'main-actor', 'paramedic.voice.session': 'main-session', 'paramedic.voice.server': 'http://main.test', 'unrelated-key': 'keep'};
  const storage = memory(other);
  writeDraftHandoff(storage, input(), {now: NOW});
  readDraftHandoff(storage, ORIGIN, {now: NOW});
  assert.equal(storage.data.size, 5);
  assert.equal(clearDraftHandoff(storage), true);
  assert.deepEqual(Object.fromEntries(storage.data), other);
  assert.equal(readDraftHandoff(storage, ORIGIN, {now: NOW}).status, 'absent');
});

test('clear is safe when missing, unavailable or blocked and never calls storage.clear', () => {
  assert.equal(clearDraftHandoff(memory()), true);
  assert.equal(clearDraftHandoff(null), false);
  assert.equal(clearDraftHandoff({}), false);
  assert.equal(clearDraftHandoff({removeItem() { throw new Error('denied'); }}), false);
  let destructiveClearCalled = false;
  const storage = memory(); storage.clear = () => { destructiveClearCalled = true; };
  assert.equal(clearDraftHandoff(storage), true);
  assert.equal(destructiveClearCalled, false);
});

test('invalid caller clocks cannot create or restore a handoff', () => {
  for (const now of [-1, NaN, Infinity, '0', Number.MAX_SAFE_INTEGER]) {
    assert.throws(() => writeDraftHandoff(memory(), input(), {now}), /timestamp/);
    const result = readDraftHandoff(storeRaw(saved()), ORIGIN, {now});
    assert.equal(result.status, 'invalid'); assert.equal(result.payload, null);
  }
});

test('error messages never echo synthetic pending text or identifiers', () => {
  const secret = 'SYNTHETIC_PRIVATE_PENDING_TEXT';
  const result = readDraftHandoff(storeRaw(saved({unappliedText: secret, serverOrigin: 'http://other.test'})), ORIGIN, {now: NOW});
  assert.equal(result.payload, null);
  assert.equal(result.message.includes(secret), false);
  assert.equal(result.message.includes('QUICK-VOICE-test'), false);
});
