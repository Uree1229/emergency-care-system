/** A long utterance is one API operation; partial STT fragments never edit a draft. */
import {requestId} from './voice-client.mjs';
import {normalizeRecognitionAlternatives} from './recognition-context.mjs';

export const FOLLOWUP_PROFILE = 'voice_extended_v1';
const MODES = ['auto', 'dictation', 'command'];
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:@-]*$/;
const NUMBERS = Object.freeze({systolic_bp:[1,350,true],diastolic_bp:[1,250,true],pulse:[0,350,true],
  spo2:[0,100,false],temperature:[20,45,false],respiratory_rate:[0,100,true],glucose:[0,1500,false]});
const ENUMS = Object.freeze({consciousness:['alert','impaired','unresponsive','unknown'],breathing:['normal','abnormal','absent','unknown']});
const LISTS = Object.freeze(['symptoms','denied_symptoms','uncertain_symptoms']);
const SYMPTOMS = Object.freeze(['흉통','호흡곤란','복통','두통','어지럼','구토','오심','발열','기침','객혈','출혈','경련','실신','마비','의식저하','설사','요통','발진','외상','부종','심계항진']);
export const CORRECTION_FIELDS = Object.freeze([...Object.keys(NUMBERS),...Object.keys(ENUMS),...LISTS]);
const REVIEW_NUMBERS=Object.freeze({...NUMBERS,age:[0,130,true],onset_at:[0,Number.MAX_SAFE_INTEGER,true],pregnancy_weeks:[4,45,true],pain_score:[0,10,true],gcs:[3,15,true]});
const REVIEW_ENUMS=Object.freeze({...ENUMS,sex:['male','female','unknown'],avpu:['A','V','P','U','unknown'],
  chief_complaint:['cardiac','stroke','altered_seizure','dyspnea','trauma','bleeding','abdominal','obstetric','toxic_env','other'],
  age_category:['adult','child','neonate','pregnant','unknown'],pre_ktas:[1,2,3,4,5,'unclassified'],stemi_suspected:['yes','no','unmeasured'],
  injury_region:['head','torso','limb','multiple'],labor:['yes','no'],seizure_ongoing:['yes','no'],bleeding_risk:['yes','no'],high_risk_mechanism:['yes','no']});
export const REVIEW_FIELDS=Object.freeze([...Object.keys(REVIEW_NUMBERS),...Object.keys(REVIEW_ENUMS),...LISTS,'age_group']);
export const WORKFLOW_PHASES=Object.freeze(['confirm','submit','review-fields']);
const clone = value => JSON.parse(JSON.stringify(value));
const invalidRecovery = () => new Error('저장된 음성 요청의 복구 정보가 맞지 않습니다. 원문을 확인하세요.');

// Validate the small JSON recovery contract rather than serializing arbitrary
// client data. Operational writes are limited to explicit review/confirm/submit;
// hospital inquiry endpoints and audio remain outside this contract.
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
/** Full supported observation/R1 review. Missing values are only explicit unsets. */
export function normalizeReviewFields(input={}){
  try{
    shape(input,[],['changes','unset_fields']);
    const changes=Object.hasOwn(input,'changes')?input.changes:{},unsetFields=Object.hasOwn(input,'unset_fields')?input.unset_fields:[];
    shape(changes,[],REVIEW_FIELDS);stringArray(unsetFields,REVIEW_FIELDS,REVIEW_FIELDS.length);
    const changed=Object.keys(changes);
    if(!changed.length&&!unsetFields.length||changed.some(key=>unsetFields.includes(key)))throw invalidRecovery();
    for(const key of changed){
      const value=changes[key];
      if(Object.hasOwn(REVIEW_NUMBERS,key)){
        const [low,high,integer]=REVIEW_NUMBERS[key];
        if(typeof value!=='number'||!Number.isFinite(value)||value<low||value>high||integer&&!Number.isSafeInteger(value))throw invalidRecovery();
      }else if(Object.hasOwn(REVIEW_ENUMS,key)){
        if(!REVIEW_ENUMS[key].includes(value))throw invalidRecovery();
      }else if(key==='age_group'){
        if(typeof value!=='string'||!/^(?:0|[1-9]0|1[0-2]0)대$/.test(value))throw invalidRecovery();
      }else stringArray(value,SYMPTOMS,SYMPTOMS.length);
    }
    if(Object.hasOwn(changes,'age')&&Object.hasOwn(changes,'age_group')&&Math.floor(changes.age/10)*10!==Number(changes.age_group.slice(0,-1)))throw invalidRecovery();
    if(Object.hasOwn(changes,'systolic_bp')&&Object.hasOwn(changes,'diastolic_bp')&&changes.systolic_bp<=changes.diastolic_bp)throw invalidRecovery();
    const avpu={A:'alert',V:'impaired',P:'impaired',U:'unresponsive',unknown:'unknown'};
    if(Object.hasOwn(changes,'avpu')&&Object.hasOwn(changes,'consciousness')&&avpu[changes.avpu]!==changes.consciousness)throw invalidRecovery();
    const observed=new Set();for(const field of LISTS)for(const symptom of changes[field]||[]){if(observed.has(symptom))throw invalidRecovery();observed.add(symptom);}
    return {changes:clone(changes),unsetFields:clone(unsetFields)};
  }catch{throw new Error('검토한 항목이나 값이 올바르지 않습니다. 나이·수치·분류와 미확인 항목을 확인하세요.');}
}
/** New-patient registration context only; no defaults, condition inference or inquiry flags. */
export function normalizeSubmitContext(context){
  try{
    shape(context,['consent','lat','lon','required_capabilities','resource_demands']);
    if(context.consent!==true)throw invalidRecovery();
    for(const [key,limit] of [['lat',90],['lon',180]])if(typeof context[key]!=='number'||!Number.isFinite(context[key])||Math.abs(context[key])>limit)throw invalidRecovery();
    const list=context.required_capabilities;
    if(!Array.isArray(list)||list.length>128||Reflect.ownKeys(list).length!==list.length+1)throw invalidRecovery();
    const seen=new Set();
    for(let index=0;index<list.length;index++){
      const descriptor=Object.getOwnPropertyDescriptor(list,String(index));
      if(!descriptor?.enumerable||!Object.hasOwn(descriptor,'value')||typeof list[index]!=='string'||!list[index].trim()||list[index].length>200||/[\u0000-\u001f\u007f]/.test(list[index])||seen.has(list[index]))throw invalidRecovery();
      seen.add(list[index]);
    }
    shape(context.resource_demands,['ed_bed']);if(context.resource_demands.ed_bed!==1)throw invalidRecovery();
    return clone(context);
  }catch{throw new Error('등록할 위치·동의·필요 진료를 확인하세요. 병원 문의는 별도로 시작합니다.');}
}
/** Shared by the in-memory job and durable notebook validation; never dispatches HTTP. */
export function validateWorkflowRecovery(pending,job,{actorId,sessionId,version}){
  shape(pending,['path','method','body']);identifier(actorId,80);identifier(sessionId);positiveVersion(version);
  if(!WORKFLOW_PHASES.includes(job?.phase))throw invalidRecovery();
  const phase=job.phase,review=phase==='review-fields',body=pending.body;
  shape(job,review?['phase','changes','unsetFields']:phase==='submit'?['phase','context']:['phase']);
  const suffix=review?'fields':phase;
  if(pending.path!==`/api/voice/sessions/${encodeURIComponent(sessionId)}/${suffix}`||pending.method!==(review?'PATCH':'POST'))throw invalidRecovery();
  const common=['actor_id','client_request_id','expected_version'];
  shape(body,review?[...common,'changes','unset_fields','change_kind']:phase==='submit'?[...common,'context','start_inquiry']:[...common,'confirmed']);
  identifier(body.actor_id,80);identifier(body.client_request_id);positiveVersion(body.expected_version);
  if(body.actor_id!==actorId||body.expected_version>version)throw invalidRecovery();
  if(review){
    normalizeReviewFields({changes:job.changes,unset_fields:job.unsetFields});normalizeReviewFields({changes:body.changes,unset_fields:body.unset_fields});
    if(body.change_kind!=='correction'||JSON.stringify(job.changes)!==JSON.stringify(body.changes)||JSON.stringify(job.unsetFields)!==JSON.stringify(body.unset_fields))throw invalidRecovery();
  }else if(phase==='submit'){
    normalizeSubmitContext(job.context);normalizeSubmitContext(body.context);
    if(body.start_inquiry!==false||JSON.stringify(body.context)!==JSON.stringify(job.context))throw invalidRecovery();
  }else if(body.confirmed!==true)throw invalidRecovery();
}
function validateRecovery(snapshot, client) {
  shape(snapshot, ['pending', 'job']);
  const {pending, job} = snapshot;
  if (pending === null && job === null) return;
  if (!pending || !job) throw invalidRecovery();
  shape(pending, ['path', 'method', 'body']);
  identifier(client.actorId, 80);
  if(WORKFLOW_PHASES.includes(job.phase)){
    const session=client.session;
    if(!session||!['DRAFT','SUBMITTED'].includes(session.state)||(session.actor_id!==undefined&&session.actor_id!==client.actorId))throw invalidRecovery();
    validateWorkflowRecovery(pending,job,{actorId:client.actorId,sessionId:session.id,version:session.version});return;
  }
  const answer = job.phase === 'answer', correct = job.phase === 'correct';
  shape(job, correct ? ['phase','changes','unsetFields'] : answer ? ['text', 'questionId', 'utteranceId', 'phase'] : ['text', 'mode', 'utteranceId', 'phase'], correct ? [] : ['recognitionAlternatives']);
  if (!['create', 'write', 'answer','correct'].includes(job.phase) || pending.method !== (correct ? 'PATCH' : 'POST')) throw invalidRecovery();
  if (correct) normalizeCorrection({changes:job.changes,unset_fields:job.unsetFields});
  else {
    recoveryText(job.text);identifier(job.utteranceId);
    if(Object.hasOwn(job,'recognitionAlternatives'))normalizeRecognitionAlternatives(job.text,job.recognitionAlternatives);
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
      ...(answer ? ['question_id', 'profile'] : ['mode', 'is_final'])], correct ? [] : [...(answer ? ['unavailable'] : []),'recognition_alternatives']);
    positiveVersion(body.expected_version);
    // A retry may use an older version because idempotency is checked before
    // version conflicts. Never replace it with the freshly loaded version.
    if (body.expected_version > session.version) throw invalidRecovery();
    if (correct) {
      normalizeCorrection({changes:body.changes,unset_fields:body.unset_fields});
      if (body.change_kind !== 'correction' || JSON.stringify(body.changes) !== JSON.stringify(job.changes) ||
          JSON.stringify(body.unset_fields) !== JSON.stringify(job.unsetFields)) throw invalidRecovery();
    } else if (body.text !== job.text || body.utterance_id !== job.utteranceId) throw invalidRecovery();
    if(!correct && JSON.stringify(body.recognition_alternatives)!==JSON.stringify(job.recognitionAlternatives))throw invalidRecovery();
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
  async apply(text, mode = 'dictation', {recognitionAlternatives} = {}) {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!MODES.includes(mode)) throw new Error('지원하지 않는 말하기 모드입니다.');
    checkText(text);
    if (mode === 'command' && !this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    const context=recognitionAlternatives===undefined?{}:{recognitionAlternatives:normalizeRecognitionAlternatives(text,recognitionAlternatives)};
    this.job = {text, mode, utteranceId: this.makeId(), phase: this.client.session ? 'write' : 'create',...context};
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
    const {text, mode, utteranceId, recognitionAlternatives} = this.job;
    // Even submit commands only reach the transcript endpoint, never /submit.
    const result = await this.client.write('transcripts', {
      text, mode, utterance_id: utteranceId, is_final: true,
      ...(recognitionAlternatives===undefined?{}:{recognition_alternatives:recognitionAlternatives}),
    });
    this.job = null;
    return result;
  }
  /** Answer only the question the server offered; the server re-checks it against the current version. */
  async answer(question, text, {unavailable = false, recognitionAlternatives} = {}) {
    if (this.job) throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if (!this.client.session) throw new Error('환자 상태를 먼저 말해 주세요.');
    if (!question?.id) throw new Error('답할 확인 질문이 없습니다.');
    checkText(text);
    const context=recognitionAlternatives===undefined?{}:{recognitionAlternatives:normalizeRecognitionAlternatives(text,recognitionAlternatives)};
    this.job = {text, questionId: question.id, utteranceId: this.makeId(), phase: 'answer',...context};
    try {
      const result = await this.client.write('followup/answers', {
        question_id: question.id, profile: FOLLOWUP_PROFILE, utterance_id: this.job.utteranceId, text,
        ...(unavailable ? {unavailable: true} : {}),
        ...(recognitionAlternatives===undefined?{}:{recognition_alternatives:context.recognitionAlternatives}),
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
  async _workflow(phase,body,job){
    if(this.job||this.client.pending)throw new Error('진행 중인 요청을 먼저 확인하세요.');
    if(!this.client.session||this.client.session.state!=='DRAFT')throw new Error('작업할 초안을 먼저 불러오세요.');
    this.job=job;
    try{
      const result=await this.client.write(phase==='review-fields'?'fields':phase,body,phase==='review-fields'?'PATCH':'POST');
      this.job=null;return result;
    }catch(error){if(!this.client.pending)this.job=null;throw error;}
  }
  /** Separate explicit action after the displayed review; never triggered by voice commands. */
  async confirm(){return this._workflow('confirm',{confirmed:true},{phase:'confirm'});}
  async reviewFields(input){
    const {changes,unsetFields}=normalizeReviewFields(input);
    return this._workflow('review-fields',{changes,unset_fields:unsetFields,change_kind:'correction'},{phase:'review-fields',changes,unsetFields});
  }
  async submit(context){
    if(!this.client.session?.confirmed||this.client.session.patient_id)throw new Error('현재 미등록 초안을 먼저 확인하세요. 이미 등록된 환자는 새로 등록하지 않습니다.');
    const normalized=normalizeSubmitContext(context);
    return this._workflow('submit',{context:normalized,start_inquiry:false},{phase:'submit',context:normalized});
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
