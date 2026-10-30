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
  constructor({server, actorId, fetchImpl = globalThis.fetch, makeId = requestId, beforeWrite = null, onApiResult = null}) {
    this.server = normalizeServer(server); this.actorId = actorId;
    this.fetchImpl = fetchImpl.bind(globalThis); this.makeId = makeId;
    if (beforeWrite !== null && typeof beforeWrite !== 'function') throw new Error('beforeWrite는 동기 함수여야 합니다.');
    if (onApiResult !== null && typeof onApiResult !== 'function') throw new Error('onApiResult는 함수여야 합니다.');
    this.beforeWrite = beforeWrite; this._queuedTasks = 0;
    this.onApiResult = onApiResult;
    this.session = null; this.pending = null; this.queue = Promise.resolve();
  }
  get hasQueuedRequests() { return this._queuedTasks > 0; }
  observeApiResult(method, status, kind) {
    if (!this.onApiResult) return;
    try {
      // Never give diagnostics a request URL, payload, response or identifier.
      // Observation is best-effort and cannot affect API/retry semantics.
      Promise.resolve(this.onApiResult({method, status, kind})).catch(() => {});
    } catch (_) { /* A failed observer must not replace the actual result. */ }
  }
  async api(path, method = 'GET', body) {
    let response, dispatched = false;
    try {
      const options = {method, cache: 'no-store',
        headers: body === undefined ? {} : {'Content-Type': 'application/json'},
        body: body === undefined ? undefined : JSON.stringify(body)};
      dispatched = true;
      response = await this.fetchImpl(this.server + path, options);
    } catch (_) {
      if (dispatched) this.observeApiResult(method, 0, 'network_error');
      throw new ApiError(method === 'GET' ? '서버에 연결하지 못했습니다. 서버 주소와 실행 상태를 확인하세요.' :
        '서버 응답을 받지 못했습니다. 같은 요청 재시도로 결과를 확인하세요.', 0, 'NETWORK', method !== 'GET');
    }
    let result;
    try { result = await response.json(); }
    catch (_) {
      this.observeApiResult(method, response.status, 'invalid_response');
      throw new ApiError('서버 응답 형식을 확인할 수 없습니다.', response.status, 'INVALID_RESPONSE', method !== 'GET');
    }
    this.observeApiResult(method, response.status, response.status >= 500 ? 'service_error' : 'reachable');
    if (!response.ok) throw new ApiError(result.message || result.error || `HTTP ${response.status}`,
      response.status, result.error, method !== 'GET' && response.status >= 500);
    return result;
  }
  serial(task) {
    this._queuedTasks++;
    const run = this.queue.then(task).finally(() => { this._queuedTasks--; });
    this.queue = run.catch(() => {});
    return run;
  }
  async execute(request) {
    this.pending = request;
    try {
      if (this.beforeWrite) {
        try {
          const checkpoint = this.beforeWrite(JSON.parse(JSON.stringify(request)));
          if (checkpoint && typeof checkpoint.then === 'function') {
            // A Promise cannot prove that a synchronous write-ahead checkpoint
            // was durable before dispatch. Consume a rejection, but never send.
            Promise.resolve(checkpoint).catch(() => {});
            throw new Error('beforeWrite must complete synchronously.');
          }
        } catch (_) {
          throw new ApiError('중간 저장에 실패하여 서버 요청을 보내지 않았습니다. 저장 상태를 확인한 뒤 같은 요청으로 재시도하세요.',
            0, 'RECOVERY_CHECKPOINT_FAILED', true);
        }
      }
      const result = await this.api(request.path, request.method, request.body);
      // An idempotent retry can return a historical snapshot. Keep the original
      // request pending until its current draft is verified too: a failed GET
      // must not invite a manual second write for an already committed utterance.
      if (result.duplicate) {
        try {
          if (!result.session?.id) throw new ApiError('최신 초안 응답 형식을 확인할 수 없습니다.');
          const fresh = await this.api(`/api/voice/sessions/${encodeURIComponent(result.session.id)}?actor_id=${encodeURIComponent(this.actorId)}`);
          if (!fresh.session || fresh.session.id !== result.session.id ||
              !Number.isSafeInteger(fresh.session.version) || fresh.session.version <= 0 ||
              !['DRAFT', 'SUBMITTED'].includes(fresh.session.state)) {
            throw new ApiError('최신 초안 응답 형식을 확인할 수 없습니다.');
          }
          this.session = fresh.session;
        } catch (error) {
          throw new ApiError('처리된 요청의 최신 초안을 조회하지 못했습니다. 같은 요청 재시도로 최신 초안을 다시 확인하세요.',
            error.status || 0, 'DUPLICATE_REFRESH_REQUIRED', true);
        }
      } else if (result.session) this.session = result.session;
      this.pending = null;
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
