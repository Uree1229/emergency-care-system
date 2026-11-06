/** Pure console selection/stages and injected snapshot polling. No DOM or HTTP implementation. */
const ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const ACTOR = /^[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}$/;
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const failure = (code, message) => Object.assign(new Error(message), {code});
const positive = value => Number.isSafeInteger(value) && value > 0;
const id = value => typeof value === 'string' && ID.test(value);
const actor = value => typeof value === 'string' && ACTOR.test(value);
const copy = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

export function normalizeSelection(value) {
  if (!plain(value) || !['patient', 'draft', 'new'].includes(value.kind)) throw failure('INVALID_SELECTION', '환자 또는 음성 초안을 선택하세요.');
  const allowed = new Set(['kind', 'id', 'actorId', 'version']);
  if (Object.keys(value).some(key => !allowed.has(key))) throw failure('INVALID_SELECTION', '지원하지 않는 선택 정보입니다.');
  if (value.kind === 'new') {
    if (value.id !== undefined && value.id !== null || value.actorId !== undefined || value.version !== undefined) throw failure('INVALID_SELECTION', '새 기록에 기존 식별자를 넣을 수 없습니다.');
    return {kind: 'new', id: null};
  }
  if (!id(value.id) || value.actorId !== undefined && !actor(value.actorId) || value.version !== undefined && !positive(value.version)) throw failure('INVALID_SELECTION', '기록 식별자와 버전을 확인하세요.');
  return {kind: value.kind, id: value.id, ...(value.actorId === undefined ? {} : {actorId: value.actorId}), ...(value.version === undefined ? {} : {version: value.version})};
}

export function recordKey(selection) {
  const value = normalizeSelection(selection);
  return value.kind === 'new' ? 'new' : `${value.kind}:${value.id}`;
}

function originURL(origin) {
  let base;
  try { base = new URL(origin); }
  catch { throw failure('INVALID_ORIGIN', '현재 서버 주소를 확인하세요.'); }
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.pathname !== '/' || base.search || base.hash) throw failure('INVALID_ORIGIN', '현재 서버의 출처 주소만 사용할 수 있습니다.');
  if (typeof globalThis.location?.origin === 'string' && base.origin !== globalThis.location.origin) throw failure('EXTERNAL_ORIGIN', '현재 서버와 다른 주소는 열지 않습니다.');
  return base;
}

export function buildRoleURL(role, {origin, selection, hospitalId, ownerActorId, voiceSessionId, version} = {}) {
  const base = originURL(origin), chosen = normalizeSelection(selection);
  if (!['patient', 'voice', 'hospital'].includes(role)) throw failure('INVALID_ROLE', '지원하지 않는 역할입니다.');
  if (hospitalId !== undefined && hospitalId !== null && !id(hospitalId)) throw failure('INVALID_HOSPITAL', '병원 식별자를 확인하세요.');
  if (ownerActorId !== undefined && ownerActorId !== null && !actor(ownerActorId)) throw failure('INVALID_ACTOR', '작성자 식별자를 확인하세요.');
  if (voiceSessionId !== undefined && voiceSessionId !== null && !id(voiceSessionId)) throw failure('INVALID_SESSION', '음성 초안 식별자를 확인하세요.');
  if (version !== undefined && !positive(version)) throw failure('INVALID_VERSION', '초안 버전을 확인하세요.');
  if (chosen.actorId && ownerActorId && chosen.actorId !== ownerActorId || chosen.version && version && chosen.version !== version) throw failure('CONTEXT_MISMATCH', '선택한 기록과 연결 정보가 다릅니다.');
  const owner = ownerActorId || chosen.actorId;
  let path, query = new URLSearchParams();
  if (role === 'voice') {
    if (chosen.kind === 'new') { path = '/paramedic/quick-test.html'; query.set('flow', 'field'); }
    else if (chosen.kind === 'patient') {
      if (!owner) throw failure('MISSING_ACTOR', '해당 기록의 작성자를 먼저 확인하세요.');
      path = '/paramedic/'; query.set('view', 'full'); query.set('flow', 'field'); query.set('pid', chosen.id); query.set('actor_id', owner);
    } else {
      const revision = version ?? chosen.version;
      if (!owner || !positive(revision)) throw failure('MISSING_DRAFT_CONTEXT', '초안의 작성자와 버전을 먼저 확인하세요.');
      if (voiceSessionId && voiceSessionId !== chosen.id) throw failure('CONTEXT_MISMATCH', '선택한 음성 초안이 다릅니다.');
      path = '/flow-console-entry.html'; query.set('sid', chosen.id); query.set('actor_id', owner); query.set('version', String(revision));
    }
  } else if (role === 'patient') {
    if (chosen.kind === 'draft') throw failure('NO_PATIENT', '환자 등록 후 이송 화면을 연결합니다.');
    path = '/patient/';
    if (chosen.kind === 'patient') {
      if (!owner) throw failure('MISSING_ACTOR', '해당 기록의 작업자를 먼저 확인하세요.');
      query.set('pid', chosen.id); query.set('actor_id', owner);
    }
  } else {
    if (!hospitalId) throw failure('MISSING_HOSPITAL', '조회할 병원을 선택하세요.');
    path = '/hospital/'; query.set('hid', hospitalId);
    if (chosen.kind === 'patient') query.set('pid', chosen.id);
  }
  const target = new URL(path, base); target.search = query.toString();
  if (target.origin !== base.origin) throw failure('EXTERNAL_ORIGIN', '현재 서버와 다른 주소는 열지 않습니다.');
  return target.pathname + target.search;
}

const STAGES = [
  ['recording', '기록'], ['registration', '환자 등록'], ['inquiry', '병원 문의'],
  ['acceptance', '수용 응답'], ['confirmation', '목적지 확정'], ['transport', '이송'], ['arrival', '도착'],
];
export function deriveStages(selected) {
  let position = null, arrived = false, stopped = false;
  if (selected?.kind === 'draft' && plain(selected.draft) && id(selected.draft.id)) position = 0;
  else if (selected?.kind === 'patient' && plain(selected.patient) && id(selected.patient.id)) {
    const state = selected.patient.state, inquiries = Array.isArray(selected.inquiries) ? selected.inquiries : [];
    if (state === 'WAITING') position = inquiries.some(item => item?.state === 'ACCEPTED') ? 3 : inquiries.length ? 2 : 1;
    else if (state === 'CONFIRMED') position = 4;
    else if (state === 'IN_TRANSIT') position = 5;
    else if (state === 'ARRIVED' || state === 'COMPLETED') { position = 6; arrived = true; }
    else if (state === 'CANCELLED') { position = null; stopped = true; }
  }
  return {currentStage: position === null ? null : STAGES[position][0],
    stages: STAGES.map(([stageId, label], index) => ({id: stageId, label,
      status: stopped ? 'stopped' : position === null || index > position ? 'pending' : index < position || arrived ? 'done' : 'current'}))};
}

function contextCopy(value) {
  if (value === null) return null;
  if (!plain(value)) throw failure('INVALID_CONTEXT', '조회 문맥은 객체여야 합니다.');
  const seen = new Set();
  function canonical(item) {
    if (item === null || typeof item === 'string' || typeof item === 'boolean' || typeof item === 'number' && Number.isFinite(item)) return item;
    if (typeof item !== 'object' || seen.has(item)) throw failure('INVALID_CONTEXT', '조회 문맥은 JSON 값만 사용할 수 있습니다.');
    seen.add(item);
    const result = Array.isArray(item) ? item.map(canonical) : Object.fromEntries(Object.keys(item).sort().map(key => [key, canonical(item[key])]));
    seen.delete(item); return result;
  }
  return canonical(value);
}

export class SnapshotPoller {
  constructor({fetchSnapshot, onSnapshot = () => {}, onError = () => {}, delay = 3000,
    setTimeoutFn = globalThis.setTimeout, clearTimeoutFn = globalThis.clearTimeout,
    AbortControllerRef = globalThis.AbortController} = {}) {
    if (typeof fetchSnapshot !== 'function' || typeof onSnapshot !== 'function' || typeof onError !== 'function' ||
        typeof setTimeoutFn !== 'function' || typeof clearTimeoutFn !== 'function' || !Number.isFinite(delay) || delay <= 0) throw failure('INVALID_OPTIONS', '조회 설정을 확인하세요.');
    this._fetch = fetchSnapshot; this._onSnapshot = onSnapshot; this._onError = onError;
    this._delay = delay;
    // Window timer methods require the browser receiver, never this poller.
    this._later = (...args) => setTimeoutFn.call(globalThis, ...args);
    this._cancelLater = (...args) => clearTimeoutFn.call(globalThis, ...args);
    this._Abort = AbortControllerRef;
    this._context = null; this._generation = 0; this._running = false; this._visible = true;
    this._snapshot = null; this._inflight = null; this._queued = null; this._timer = null;
  }
  get snapshot() { return copy(this._snapshot); }
  get context() { return copy(this._context); }
  get running() { return this._running; }
  get busy() { return this._inflight !== null; }
  _clearTimer() { if (this._timer !== null) this._cancelLater(this._timer); this._timer = null; }
  _invalidate() {
    ++this._generation; this._clearTimer();
    if (this._queued) { this._queued.resolve(null); this._queued = null; }
    try { this._inflight?.controller?.abort(); } catch { /* Generation still discards late responses. */ }
  }
  _current(operation) { return operation.generation === this._generation && this._visible && this._context !== null; }
  _schedule() {
    this._clearTimer();
    if (this._running && this._visible && this._context !== null && !this._inflight) this._timer = this._later(() => { this._timer = null; void this.refresh(); }, this._delay);
  }
  start(context = this._context ?? {}) {
    this.setContext(context); this._running = true; return this.refresh();
  }
  stop() { this._running = false; this._invalidate(); }
  setContext(value) {
    const context = contextCopy(value);
    if (JSON.stringify(context) === JSON.stringify(this._context)) return Promise.resolve(this.snapshot);
    this._invalidate(); this._context = context; this._snapshot = null;
    return this._running && this._visible ? this.refresh() : Promise.resolve(null);
  }
  setVisible(value) {
    if (typeof value !== 'boolean') throw failure('INVALID_VISIBILITY', '표시 상태를 확인하세요.');
    if (value === this._visible) return Promise.resolve(this.snapshot);
    this._visible = value;
    if (!value) { this._invalidate(); return Promise.resolve(null); }
    return this._running ? this.refresh() : Promise.resolve(this.snapshot);
  }
  refresh() {
    this._clearTimer();
    if (!this._visible || this._context === null) return Promise.resolve(null);
    if (this._inflight) {
      if (this._inflight.generation === this._generation) return this._inflight.promise;
      if (!this._queued) {
        let resolve; const promise = new Promise(complete => { resolve = complete; });
        this._queued = {generation: this._generation, promise, resolve};
      }
      return this._queued.promise;
    }
    let controller = null;
    try { if (typeof this._Abort === 'function') controller = new this._Abort(); } catch { /* Generation protection works without AbortController. */ }
    const operation = {generation: this._generation, context: this.context, controller, promise: null};
    this._inflight = operation;
    operation.promise = Promise.resolve().then(() => this._current(operation) ? this._fetch(copy(operation.context), {signal: controller?.signal}) : null).then(value => {
      if (!this._current(operation)) return null;
      if (!plain(value)) throw failure('INVALID_SNAPSHOT', '서버 현황 응답을 확인하세요.');
      const snapshot = copy(value); this._snapshot = snapshot;
      try { this._onSnapshot(copy(snapshot), copy(operation.context)); } catch { /* A display callback cannot change request ownership. */ }
      return copy(snapshot);
    }).catch(error => {
      if (this._current(operation)) try { this._onError(error, copy(operation.context)); } catch { /* Preserve the last successful data. */ }
      return null;
    }).finally(() => {
      if (this._inflight !== operation) return;
      this._inflight = null;
      const queued = this._queued; this._queued = null;
      if (queued && queued.generation === this._generation && this._visible && this._context !== null) queued.resolve(this.refresh());
      else { queued?.resolve(null); this._schedule(); }
    });
    return operation.promise;
  }
}
