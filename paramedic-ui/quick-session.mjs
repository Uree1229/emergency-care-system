/** A long utterance is one API operation; partial STT fragments never edit a draft. */
import {requestId} from './voice-client.mjs';

export const FOLLOWUP_PROFILE = 'voice_extended_v1';
const MODES = ['auto', 'dictation', 'command'];
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@-]*$/;
const NUMBERS = Object.freeze({systolic_bp:[1,350,true],diastolic_bp:[1,250,true],pulse:[0,350,true],
  spo2:[0,100,false],temperature:[20,45,false],respiratory_rate:[0,100,true],glucose:[0,1500,false]});
const ENUMS = Object.freeze({consciousness:['alert','impaired','unresponsive','unknown'],breathing:['normal','abnormal','absent','unknown']});
const LISTS = Object.freeze(['symptoms','denied_symptoms','uncertain_symptoms']);
const SYMPTOMS = Object.freeze(['흉통','호흡곤란','복통','두통','어지럼','구토','오심','발열','기침','객혈','출혈','경련','실신','마비','의식저하','설사','요통','발진','외상','부종','심계항진']);
export const CORRECTION_FIELDS = Object.freeze([...Object.keys(NUMBERS),...Object.keys(ENUMS),...LISTS]);
const clone = value => JSON.parse(JSON.stringify(value));
const invalidRecovery = () => new Error('저장된 음성 요청의 복구 정보가 맞지 않습니다. 원문을 확인하세요.');

// Validate the small JSON recovery contract rather than serializing arbitrary
// client data. Audio, clinical confirmation and operational endpoints are absent.
function shape(value, required, optional = []) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw invalidRecovery();
  const prototype = Object.getPrototypeOf(value);
  const constructor = prototype && Object.getOwnPropertyDescriptor(prototype, 'constructor');
  if (prototype !== null && !(Object.getPrototypeOf(prototype) === null &&
      typeof constructor?.value === 'function' && constructor.value.name === 'Object')) throw invalidRecovery();
  const keys = Reflect.ownKeys(value), allowed = new Set([...required, ...optional]);
  if (required.some(key => !Object.hasOwn(value, key)) || keys.some(key => !allowed.has(key) ||
      !Object.getOwnPropertyDescriptor(value, key)?.enumerable ||
      !Object.hasOwn(Object.getOwnPropertyDescriptor(value, key), 'value'))) throw invalidRecovery();
}
function identifier(value, limit = 128) {
  if (typeof value !== 'string' || value.length > limit || !IDENTIFIER.test(value)) throw invalidRecovery();
}
function positiveVersion(value) {
  if (!Number.isSafeInteger(value) || value <= 0) throw invalidRecovery();
}
function recoveryText(value) {
  if (typeof value !== 'string' || !value.trim() || value.length > 4000) throw invalidRecovery();
}
function stringArray(value, allowed, maximum) {
  if (!Array.isArray(value) || value.length > maximum || Reflect.ownKeys(value).length !== value.length + 1) throw invalidRecovery();
  const seen = new Set();
  for (let i=0;i<value.length;i++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
    if (!descriptor?.enumerable || !Object.hasOwn(descriptor, 'value') ||
        typeof descriptor.value !== 'string' || !allowed.includes(descriptor.value) || seen.has(descriptor.value)) throw invalidRecovery();
    seen.add(descriptor.value);
  }
}
/** Strict, non-coercing subset of the server's manual correction contract. */
export function normalizeCorrection(input = {}) {
  try {
    shape(input, [], ['changes','unset_fields']);
    const changes = Object.hasOwn(input,'changes') ? input.changes : {};
    const unsetFields = Object.hasOwn(input,'unset_fields') ? input.unset_fields : [];
    shape(changes, [], CORRECTION_FIELDS);
    stringArray(unsetFields, CORRECTION_FIELDS, CORRECTION_FIELDS.length);
    const changed = Object.keys(changes);
    if (!changed.length && !unsetFields.length || changed.some(key=>unsetFields.includes(key))) throw invalidRecovery();
    for (const key of changed) {
      const value = changes[key];
      if (Object.hasOwn(NUMBERS,key)) {
        const [minimum,maximum,integer] = NUMBERS[key];
        if (typeof value !== 'number' || !Number.isFinite(value) || value < minimum || value > maximum ||
            integer && !Number.isInteger(value)) throw invalidRecovery();
      } else if (Object.hasOwn(ENUMS,key)) {
        if (!ENUMS[key].includes(value)) throw invalidRecovery();
      } else stringArray(value, SYMPTOMS, SYMPTOMS.length);
    }
    if (Object.hasOwn(changes,'systolic_bp') && Object.hasOwn(changes,'diastolic_bp') &&
        changes.systolic_bp <= changes.diastolic_bp) throw invalidRecovery();
    const observed = new Set();
    for (const key of LISTS)for (const symptom of changes[key] || []) {
      if (observed.has(symptom)) throw invalidRecovery();
      observed.add(symptom);
    }
    return {changes:clone(changes),unsetFields:clone(unsetFields)};
  } catch (_) {
    throw new Error('수정할 항목이나 값의 형식이 올바르지 않습니다. 입력 범위와 증상 분류를 확인하세요.');
  }
}
function validateRecovery(snapshot, client) {
  shape(snapshot, ['pending', 'job']);
  const {pending, job} = snapshot;
  if (pending === null && job === null) return;
  if (!pending || !job) throw invalidRecovery();
  shape(pending, ['path', 'method', 'body']);
  identifier(client.actorId, 80);
  const answer = job.phase === 'answer', correct = job.phase === 'correct';
  shape(job, correct ? ['phase','changes','unsetFields'] : answer ? ['text', 'questionId', 'utteranceId', 'phase'] : ['text', 'mode', 'utteranceId', 'phase']);
  if (!['create', 'write', 'answer','correct'].includes(job.phase) || pending.method !== (correct ? 'PATCH' : 'POST')) throw invalidRecovery();
  if (correct) normalizeCorrection({changes:job.changes,unset_fields:job.unsetFields});
  else {
    recoveryText(job.text);identifier(job.utteranceId);
    if (answer) identifier(job.questionId);
    else if (!MODES.includes(job.mode)) throw invalidRecovery();
  }
  const common = ['actor_id', 'client_request_id'];
  const body = pending.body;
  if (job.phase === 'create') {
    shape(body, common);
    if (pending.path !== '/api/voice/sessions' || client.session !== null || job.mode === 'command') throw invalidRecovery();
  } else {
    const session = client.session;
    if (!session || !['DRAFT', 'SUBMITTED'].includes(session.state)) throw invalidRecovery();
    identifier(session.id);positiveVersion(session.version);
    if (session.actor_id !== undefined && session.actor_id !== client.actorId) throw invalidRecovery();
    const suffix = correct ? 'fields' : answer ? 'followup/answers' : 'transcripts';
    if (pending.path !== `/api/voice/sessions/${encodeURIComponent(session.id)}/${suffix}`) throw invalidRecovery();
    shape(body, correct ? [...common,'expected_version','changes','unset_fields','change_kind'] : [...common, 'expected_version', 'text', 'utterance_id',
      ...(answer ? ['question_id', 'profile'] : ['mode', 'is_final'])], answer ? ['unavailable'] : []);
    positiveVersion(body.expected_version);
    // A retry may use an older version because idempotency is checked before
    // version conflicts. Never replace it with the freshly loaded version.
    if (body.expected_version > session.version) throw invalidRecovery();
    if (correct) {
      normalizeCorrection({changes:body.changes,unset_fields:body.unset_fields});
      if (body.change_kind !== 'correction' || JSON.stringify(body.changes) !== JSON.stringify(job.changes) ||
          JSON.stringify(body.unset_fields) !== JSON.stringify(job.unsetFields)) throw invalidRecovery();
    } else if (body.text !== job.text || body.utterance_id !== job.utteranceId) throw invalidRecovery();
    if (answer) {
      if (body.question_id !== job.questionId || body.profile !== FOLLOWUP_PROFILE ||
          (Object.hasOwn(body, 'unavailable') && body.unavailable !== true)) throw invalidRecovery();
    } else if (!correct && (body.mode !== job.mode || body.is_final !== true)) throw invalidRecovery();
  }
  identifier(body.actor_id, 80);identifier(body.client_request_id);
  if (body.actor_id !== client.actorId) throw invalidRecovery();
}

function checkText(text) {
  if (typeof text !== 'string' || !text.trim() || text.length > 4000) throw new Error('최종 인식문은 1~4000자여야 합니다.');
}

export class QuickSession {
  constructor(client, makeId = requestId) {
    this.client = client;
    this.makeId = makeId;
    this.job = null;
  }
  /** A detached, text-only checkpoint. The caller owns its durable storage. */
  exportRecovery() {
    const snapshot = {pending: this.client.pending, job: this.job};
    validateRecovery(snapshot, this.client);
    return clone(snapshot);
  }
  /** Restore memory only. The caller must explicitly retry the saved request. */
  restoreRecovery(snapshot) {
    if (this.job || this.client.pending || this.client.hasQueuedRequests) throw new Error('진행 중인 요청이 있어 복구할 수 없습니다.');
    validateRecovery(snapshot, this.client);
    const restored = clone(snapshot);
    this.client.pending = restored.pending;
    this.job = restored.job;
    return clone(restored);
  }
  async apply(text, mode = 'dictation') {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!MODES.includes(mode)) throw new Error('지원하지 않는 말하기 모드입니다.');
    checkText(text);
    if (mode === 'command' && !this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    this.job = {text, mode, utteranceId: this.makeId(), phase: this.client.session ? 'write' : 'create'};
    try {
      if (this.job.phase === 'create') await this.client.create();
      return await this._write();
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
  async _write() {
    this.job.phase = 'write';
    const {text, mode, utteranceId} = this.job;
    // Even submit commands only reach the transcript endpoint, never /submit.
    const result = await this.client.write('transcripts', {
      text, mode, utterance_id: utteranceId, is_final: true,
    });
    this.job = null;
    return result;
  }
  /** Answer only the question the server offered; the server re-checks it against the current version. */
  async answer(question, text, {unavailable = false} = {}) {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    if (!question?.id) throw new Error('답할 확인 질문이 없습니다.');
    checkText(text);
    this.job = {text, questionId: question.id, utteranceId: this.makeId(), phase: 'answer'};
    try {
      const result = await this.client.write('followup/answers', {
        question_id: question.id, profile: FOLLOWUP_PROFILE, utterance_id: this.job.utteranceId, text,
        ...(unavailable ? {unavailable: true} : {}),
      });
      this.job = null;
      return result;
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
  /** An explicit reviewed correction updates only the draft, in one PATCH. */
  async correct(input) {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!this.client.session || this.client.session.state !== 'DRAFT') throw new Error('수정할 초안을 먼저 불러오세요.');
    const {changes,unsetFields} = normalizeCorrection(input);
    this.job = {phase:'correct',changes,unsetFields};
    try {
      const result = await this.client.write('fields',{changes,unset_fields:unsetFields,change_kind:'correction'},'PATCH');
      this.job = null;
      return result;
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
  async retry() {
    if (!this.job || !this.client.pending) throw new Error('재시도할 요청이 없습니다.');
    const phase = this.job.phase;
    try {
      const result = await this.client.retry();
      if (phase === 'create') return await this._write();
      this.job = null;
      return result;
    } catch (error) {
      if (!this.client.pending) this.job = null;
      throw error;
    }
  }
}
