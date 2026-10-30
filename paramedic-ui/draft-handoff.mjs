export const DRAFT_HANDOFF_KEY = 'paramedic.voice.handoff.v1';

const TTL_MS = 10 * 60 * 1000;
const MAX_TEXT_LENGTH = 4000;
const MAX_STORED_LENGTH = 32768;
const INPUT_KEYS = new Set(['serverOrigin', 'actorId', 'sessionId', 'version', 'unappliedText', 'intent']);
const STORED_KEYS = new Set([...INPUT_KEYS, 'createdAt', 'expiresAt']);
const ID_PATTERN = /^[A-Za-z0-9._:-]+$/;

function assertTime(now) {
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(now + TTL_MS)) {
    throw new Error('Invalid handoff timestamp.');
  }
}

function normalizeOrigin(value) {
  if (typeof value !== 'string' || !value || value.length > 2048 ||
      /[\s\\?#]/u.test(value) || !/^https?:\/\/[^/]+\/?$/iu.test(value)) {
    throw new Error('A root HTTP(S) server origin is required.');
  }
  let url;
  try { url = new URL(value); }
  catch { throw new Error('Invalid server origin.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password ||
      url.pathname !== '/' || url.search || url.hash || url.origin === 'null') {
    throw new Error('A root HTTP(S) server origin is required.');
  }
  return url.origin;
}

function normalizePayload(payload, stored = false) {
  const allowed = stored ? STORED_KEYS : INPUT_KEYS;
  if (!payload || typeof payload !== 'object' || Array.isArray(payload) ||
      Object.keys(payload).some(key => !allowed.has(key)) ||
      (stored && Object.keys(payload).length !== STORED_KEYS.size)) {
    throw new Error('Invalid handoff payload schema.');
  }
  for (const [key, limit] of [['actorId', 80], ['sessionId', 128]]) {
    if (typeof payload[key] !== 'string' || payload[key].length > limit ||
        !ID_PATTERN.test(payload[key])) {
      throw new Error('Invalid handoff identifier.');
    }
  }
  if (!Number.isSafeInteger(payload.version) || payload.version <= 0 || payload.intent !== 'recommend') {
    throw new Error('Invalid handoff version or intent.');
  }
  const unappliedText = payload.unappliedText === undefined && !stored ? '' : payload.unappliedText;
  if (typeof unappliedText !== 'string' || unappliedText.length > MAX_TEXT_LENGTH) {
    throw new Error('Pending handoff text must be at most 4000 characters.');
  }
  return {
    serverOrigin: normalizeOrigin(payload.serverOrigin),
    actorId: payload.actorId,
    sessionId: payload.sessionId,
    version: payload.version,
    unappliedText,
    intent: 'recommend',
  };
}

/** Store only a draft reference and explicitly preserved, unapplied text. */
export function writeDraftHandoff(storage, payload, {now = Date.now()} = {}) {
  assertTime(now);
  const normalized = {...normalizePayload(payload), createdAt: now, expiresAt: now + TTL_MS};
  const raw = JSON.stringify(normalized);
  try {
    if (!storage || typeof storage.setItem !== 'function') throw new Error('Unavailable storage.');
    storage.setItem(DRAFT_HANDOFF_KEY, raw);
  } catch {
    throw new Error('Draft handoff could not be saved. Keep this page open and try again.');
  }
  return normalized;
}

/** Non-destructive lookup. Restore via the server, then explicitly clear the reference. */
export function readDraftHandoff(storage, expectedOrigin, {now = Date.now()} = {}) {
  const invalid = message => ({status: 'invalid', payload: null, message});
  let origin;
  try { assertTime(now); origin = normalizeOrigin(expectedOrigin); }
  catch { return invalid('Draft handoff origin or timestamp is invalid.'); }
  let raw;
  try {
    if (!storage || typeof storage.getItem !== 'function') throw new Error('Unavailable storage.');
    raw = storage.getItem(DRAFT_HANDOFF_KEY);
  } catch {
    return invalid('Draft handoff storage is unavailable.');
  }
  if (raw === null) return {status: 'absent', payload: null, message: 'No draft handoff is saved.'};
  if (typeof raw !== 'string' || !raw || raw.length > MAX_STORED_LENGTH) {
    return invalid('Saved draft handoff is invalid.');
  }
  let payload;
  try {
    const saved = JSON.parse(raw);
    payload = {...normalizePayload(saved, true), createdAt: saved.createdAt, expiresAt: saved.expiresAt};
    if (!Number.isSafeInteger(payload.createdAt) || payload.createdAt < 0 ||
        !Number.isSafeInteger(payload.expiresAt) ||
        payload.expiresAt !== payload.createdAt + TTL_MS || payload.createdAt > now ||
        payload.serverOrigin !== origin) {
      return invalid('Saved draft handoff origin or lifetime is invalid.');
    }
  } catch {
    return invalid('Saved draft handoff is invalid.');
  }
  if (now >= payload.expiresAt) {
    return {status: 'expired', payload: null, message: 'Saved draft handoff has expired.'};
  }
  return {status: 'valid', payload, message: 'Saved draft is ready for server verification.'};
}

/** Remove only this handoff; never remove the main application's own session keys. */
export function clearDraftHandoff(storage) {
  try {
    if (!storage || typeof storage.removeItem !== 'function') return false;
    storage.removeItem(DRAFT_HANDOFF_KEY);
    return true;
  } catch { return false; }
}
