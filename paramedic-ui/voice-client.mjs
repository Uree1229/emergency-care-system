/* A serialized, versioned voice API client. Clinical data remains in memory. */
export function requestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  if (globalThis.crypto?.getRandomValues) {
    const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
  }
  throw new Error('안전한 요청 ID를 만들 수 없습니다. localhost 또는 HTTPS로 여세요.');
}

export function normalizeServer(value) {
  const url = new URL(value);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== '/') {
    throw new Error('서버 주소는 http(s)://호스트:포트 형식으로 입력하세요.');
  }
  return url.origin;
}

export class ApiError extends Error {
  constructor(message, status = 0, code = '', uncertain = false) {
    super(message); this.status = status; this.code = code; this.uncertain = uncertain;
  }
}

export class VoiceClient {
  constructor({server, actorId, fetchImpl = globalThis.fetch, makeId = requestId}) {
    this.server = normalizeServer(server); this.actorId = actorId;
    this.fetchImpl = fetchImpl.bind(globalThis); this.makeId = makeId;
    this.session = null; this.pending = null; this.queue = Promise.resolve();
  }
  async api(path, method = 'GET', body) {
    let response;
    try {
      response = await this.fetchImpl(this.server + path, {method, cache: 'no-store',
        headers: body === undefined ? {} : {'Content-Type': 'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body)});
    } catch (_) {
      throw new ApiError(method === 'GET' ? '서버에 연결하지 못했습니다. 서버 주소와 실행 상태를 확인하세요.' :
        '서버 응답을 받지 못했습니다. 같은 요청 재시도로 결과를 확인하세요.', 0, 'NETWORK', method !== 'GET');
    }
    let result;
    try { result = await response.json(); }
    catch (_) { throw new ApiError('서버 응답 형식을 확인할 수 없습니다.', response.status, 'INVALID_RESPONSE', method !== 'GET'); }
    if (!response.ok) throw new ApiError(result.message || result.error || `HTTP ${response.status}`,
      response.status, result.error, method !== 'GET' && response.status >= 500);
    return result;
  }
  serial(task) {
    const run = this.queue.then(task);
    this.queue = run.catch(() => {});
    return run;
  }
  async execute(request) {
    this.pending = request;
    try {
      const result = await this.api(request.path, request.method, request.body);
      this.pending = null;
      if (result.session) {
        // An idempotent retry can return a historical snapshot. Re-read it before editing.
        if (result.duplicate) {
          const fresh = await this.api(`/api/voice/sessions/${encodeURIComponent(result.session.id)}?actor_id=${encodeURIComponent(this.actorId)}`);
          this.session = fresh.session;
        } else this.session = result.session;
      }
      return result;
    } catch (error) {
      if (!error.uncertain) this.pending = null;
      throw error;
    }
  }
  create(patientId) {
    return this.serial(() => {
      if (this.pending) throw new ApiError('먼저 결과가 불확실한 요청을 재시도하세요.', 0, 'PENDING_REQUEST');
      const body = {actor_id: this.actorId, client_request_id: this.makeId()};
      if (patientId) body.patient_id = patientId;
      return this.execute({path: '/api/voice/sessions', method: 'POST', body});
    });
  }
  write(action, extra = {}, method = 'POST') {
    return this.serial(() => {
      if (this.pending) throw new ApiError('먼저 결과가 불확실한 요청을 재시도하세요.', 0, 'PENDING_REQUEST');
      if (!this.session || this.session.state !== 'DRAFT') throw new ApiError('편집할 초안을 먼저 만드세요.');
      const body = {...extra, actor_id: this.actorId, client_request_id: this.makeId(), expected_version: this.session.version};
      return this.execute({path: `/api/voice/sessions/${encodeURIComponent(this.session.id)}/${action}`, method, body});
    });
  }
  retry() {
    return this.serial(() => {
      if (!this.pending) throw new ApiError('재시도할 요청이 없습니다.');
      return this.execute(this.pending);
    });
  }
  async sync(sessionId = this.session?.id) {
    if (!sessionId) throw new ApiError('불러올 세션이 없습니다.');
    const result = await this.api(`/api/voice/sessions/${encodeURIComponent(sessionId)}?actor_id=${encodeURIComponent(this.actorId)}`);
    this.session = result.session;
    return result;
  }
  async readback(field) {
    if (!this.session) throw new ApiError('초안을 먼저 만드세요.');
    const query = new URLSearchParams({actor_id: this.actorId});
    if (field) query.set('field', field);
    return this.api(`/api/voice/sessions/${encodeURIComponent(this.session.id)}/readback?${query}`);
  }
}
