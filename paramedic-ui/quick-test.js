import {VoiceCapture} from './speech.mjs';
import {VoiceClient, requestId} from './voice-client.mjs';
import {QuickSession} from './quick-session.mjs';
import {chooseAnswer, answerVocabulary} from './answer-match.mjs';
import {RECORDING_LIMITS, findVoiceTrigger} from './recording-control.mjs';
import {writeDraftHandoff} from './draft-handoff.mjs';
import {RECOVERY_KEY,writeDraftRecovery,readDraftRecovery,clearDraftRecovery} from './draft-recovery.mjs';
import {buildCriticalReview,planCriticalCorrection,classifyCriticalReply} from './critical-review.mjs';
import {PATIENT_RECORDS_KEY,emptyBook,readPatientRecords,writePatientRecords,createPatientRecord,updatePatientCheckpoint,selectPatientRecord,removePatientRecord} from './patient-records.mjs';
import {buildHospitalHandoff} from './hospital-handoff.mjs';
import {buildObservationHistory,historySnapshotSignature} from './observation-history.mjs';

const $ = id => document.getElementById(id);
const VOICE_REVISION = 'voice-v2-20261030', HOLD_MS = 400;
const labels = {age:'나이',age_group:'연령대',sex:'성별',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상',consciousness:'의식',breathing:'호흡',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',onset_at:'발생 시각',avpu:'AVPU',chief_complaint:'주증상 분류',age_category:'연령 구분',pre_ktas:'Pre-KTAS',stemi_suspected:'STEMI 의심',injury_region:'손상 부위',labor:'분만 진행',pregnancy_weeks:'임신 주수',seizure_ongoing:'경련 진행',bleeding_risk:'출혈 위험',high_risk_mechanism:'고위험 손상기전',pain_score:'통증 점수',gcs:'GCS'};
const observationKeys = new Set(Object.keys(labels).slice(0,16));
const names = {male:'남성',female:'여성',unknown:'미확인',alert:'명료',impaired:'저하',unresponsive:'반응 없음',normal:'정상',abnormal:'비정상',absent:'호흡 없음',adult:'성인',child:'소아',infant:'영아',cardiac:'심혈관',respiratory:'호흡기'};
const units = {age:'세',systolic_bp:'mmHg',diastolic_bp:'mmHg',pulse:'회/분',spo2:'%',temperature:'℃',respiratory_rate:'회/분',glucose:'mg/dL',pregnancy_weeks:'주'};
const kinds = {dictation:'받아쓰기',observation:'상태 변화 관찰',correction:'수정',followup_answer:'질문 답변',undo:'되돌리기'};
const reasons = {missing:'누락',needs_clarification:'재확인 필요',uncertain:'미확인'};
const directions = {increased:'▲ 증가',decreased:'▼ 감소',unchanged:'= 같음'};
const actions = {review:'읽어주기',missing:'빠진 항목 안내',undo:'되돌리기',edit:'수정',add:'추가',confirm:'확인(화면에서 누르기)',submit:'전송 요청(실행 안 함)',observe:'상태 변화 관찰'};
const modeHints = {auto:'상태 설명은 초안에 더하고, “방금 거 취소”·“정리해줘”·“혈압 110에 70으로 수정” 같은 말은 명령으로 처리합니다.',dictation:'설명한 내용을 같은 초안에 추가합니다. 명령으로 해석하지 않습니다.',command:'“혈압 110에 70으로 수정해”, “정리해줘”, “마지막 입력 취소”처럼 말하세요.'};
const idleHint = () => '“새 환자 시작”·“이전 환자로 돌아가”는 현재 내용을 보관한 뒤 전환합니다. “중단”은 앞 내용을 저장합니다.';
// Hints that carry a finding value (normal status, negation, numbers) could pull unclear speech
// toward that value, so only vocabulary hints reach the recognizer.
const VALUE_HINT = /[0-9]|[십백]|명료|정상|저하|없음|있음/;
// The 12 items of the server's voice_extended_v1 checklist, shown as 10 summary chips.
const SUMMARY = [['age','sex'],['symptoms','onset_at'],['consciousness'],['blood_pressure'],['pulse'],['spo2'],['temperature'],['breathing'],['respiratory_rate'],['glucose']];
const SHORT = {age:'나이',sex:'성별',symptoms:'증상',onset_at:'발생',consciousness:'의식',blood_pressure:'혈압',pulse:'맥박',spo2:'SpO₂',temperature:'체온',breathing:'호흡',respiratory_rate:'호흡수',glucose:'혈당'};
const ITEM_KEYS = {age:['age','age_group'],symptoms:['symptoms','denied_symptoms','uncertain_symptoms'],blood_pressure:['systolic_bp','diastolic_bp']};
const OPTIONS = {consciousness:['명료','저하','반응 없음'],breathing:['정상','비정상','없음'],sex:['남성','여성']};
let actor = 'QUICK-VOICE-' + requestId();
const makeClient = (owner=actor) => new VoiceClient({server:location.origin,actorId:owner,beforeWrite:()=>persistRecovery(true)});
let client = makeClient();
let flow = new QuickSession(client), mode = 'auto', attempt = null, busy = false, stopping = false, ticker, historyCount = 0;
let autoAvailable = true, recordingMode = 'toggle', press = null, lastSpoken = '', hintPlan = {applied: [], excluded: []}, phraseCount = 0;
// Questions whose answer was not understood: set aside (with what was heard) for the crew's review
// instead of asking the same question again. Cleared by "다시 묻기" or a new draft.
let deferred = new Map();
// What the latest request changed (only when it produced a new draft version).
let recent = null;
let manualAppliedText = '', manualCopiedSource = '';
let unappliedText = '', recommendationReady = false;
let recovering=true,recoveryBlocked=false,recoveryConflict=false,recoveryRaw,unsentVoice='';
let criticalAcknowledged=new Map(),criticalEditor=null,lastCriticalSpoken='';
let patientBook=null,patientBookRaw=null,patientEpoch=0,patientStoreInvalid=false,legacyRecoveryRaw=null;
let handoffPreview=null;
let observationHistory=null;
const activePatient=()=>patientBook?.records.find(record=>record.id===patientBook.activeId)||null;
const patientLabel=record=>`환자 ${String(record?.number??patientBook?.nextNumber??1).padStart(2,'0')}`;
function handoffHasUnapplied(){const manual=$('recording-review').value;return !!(unappliedText.trim()||unsentVoice.trim()||(manual.trim()&&manual.trim()!==manualAppliedText)||criticalEditor?.dirty);}
function handoffModel(session=client.session,now=Date.now()){
  const reviewPendingIds=buildCriticalReview(session).filter(item=>criticalAcknowledged.get((session?.id||'')+'|'+item.id)!==item.signature).map(item=>item.id);
  return buildHospitalHandoff(session,{patientLabel:patientLabel(activePatient()),reviewPendingIds,hasUnapplied:handoffHasUnapplied(),hasPendingRequest:!!client.pending||!!flow.job,now});
}
function handoffOwnerMatches(preview){return preview&&preview.epoch===patientEpoch&&preview.recordId===patientBook?.activeId&&preview.actorId===actor&&preview.sessionId===client.session?.id;}
function handoffCurrent(preview){return handoffOwnerMatches(preview)&&preview.version===client.session?.version&&preview.model.signature===handoffModel(client.session,preview.generatedAt).signature;}
function invalidateHandoff(message='기록이나 검토 상태가 바뀌었습니다. 인계용 요약을 다시 만들어 주세요.'){
  handoffPreview=null;$('handoff-text').value='';$('handoff-status').textContent=message;
}
function renderHandoff(){
  if(handoffPreview&&!handoffCurrent(handoffPreview))invalidateHandoff();
  const lockedNow=locked()||!!criticalEditor,preview=handoffPreview,epoch=patientEpoch;
  $('handoff-panel').hidden=!client.session;
  $('handoff-generate').disabled=lockedNow||!client.session||client.session.state!=='DRAFT';
  $('handoff-copy').disabled=lockedNow||!preview?.model.hasContent;
  $('handoff-read').disabled=lockedNow||!preview?.model.hasContent||!VoiceCapture.capabilities().tts;
  $('handoff-generate').onclick=()=>{if(epoch===patientEpoch)return generateHandoff();};
  $('handoff-copy').onclick=()=>runHandoffAction('copy',preview);
  $('handoff-read').onclick=()=>runHandoffAction('read',preview);
}
function validHandoffSession(session,sid,minVersion){
  return session&&session.id===sid&&session.state==='DRAFT'&&!session.patient_id&&Number.isSafeInteger(session.version)&&session.version>=minVersion&&
    (session.actor_id===undefined||session.actor_id===actor);
}
async function freshHandoffSession(context){
  assertPatientStore();
  const result=await context.owner.api(`/api/voice/sessions/${encodeURIComponent(context.sid)}?actor_id=${encodeURIComponent(context.actorId)}`);
  assertPatientStore();
  if(context.owner!==client||context.epoch!==patientEpoch||context.recordId!==patientBook?.activeId||context.actorId!==actor||context.bookRaw!==patientBookRaw)
    throw Error('기록을 조회하는 동안 환자 기록이 변경됐습니다.');
  if(!validHandoffSession(result.session,context.sid,context.version))throw Error('요약 대상 초안의 ID·상태·작성자·버전이 맞지 않습니다.');
  return result.session;
}
function handoffContext(){return {owner:client,epoch:patientEpoch,recordId:patientBook?.activeId,actorId:actor,sid:client.session?.id,version:client.session?.version,bookRaw:patientBookRaw};}
async function generateHandoff(){
  if(locked()||criticalEditor)return false;
  if(!client.session){$('handoff-status').textContent='환자 정보를 먼저 저장한 뒤 인계용 요약을 요청하세요.';state('요약할 기록 없음');return false;}
  const context=handoffContext();busy=true;capture.stopSpeaking();controls();
  try{const fresh=await freshHandoffSession(context);client.session=fresh;
    const generatedAt=Date.now(),model=handoffModel(fresh,generatedAt);
    handoffPreview={model,generatedAt,epoch:patientEpoch,recordId:patientBook?.activeId,actorId:actor,sessionId:fresh.id,version:fresh.version};
    $('handoff-text').value=model.text;$('handoff-panel').open=true;
    $('handoff-status').textContent=model.hasContent?`${patientLabel(activePatient())} · 초안 버전 ${fresh.version}의 저장된 입력으로 만든 메모입니다. 전달 전 검토하세요.`:'요약할 저장 관찰이 없습니다. 환자 정보를 확인해 입력하세요.';
    render();$('handoff-panel').scrollIntoView?.({block:'nearest'});return true;
  }catch(err){invalidateHandoff('요약을 생성하지 못했습니다. '+err.message);error(err.message);return false;}
  finally{busy=false;controls();}
}
function selectHandoffFallback(){const text=$('handoff-text');text.focus?.();text.select?.();text.setSelectionRange?.(0,text.value.length);}
async function runHandoffAction(action,preview){
  if(locked()||criticalEditor||!preview||preview!==handoffPreview||!preview.model.hasContent||!handoffCurrent(preview))return false;
  const context=handoffContext();busy=true;capture.stopSpeaking();controls();
  try{const fresh=await freshHandoffSession(context),model=handoffModel(fresh,preview.generatedAt);
    if(fresh.version!==preview.version||model.signature!==preview.model.signature){client.session=fresh;invalidateHandoff();render();return false;}
    if(action==='read'){
      if(capture.active)throw Error('마이크를 먼저 종료하세요.');capture.speak(preview.model.text);
      $('handoff-status').textContent='현재 환자의 요약을 읽었습니다. 병원으로 전송하지 않았습니다.';return true;
    }
    try{
      const clipboard=globalThis.navigator?.clipboard;if(!clipboard?.writeText)throw Error('자동 복사를 지원하지 않습니다.');
      await clipboard.writeText(preview.model.text);
    }catch(err){
      if(!handoffCurrent(preview)||preview!==handoffPreview||context.bookRaw!==patientBookRaw||recoveryConflict){invalidateHandoff('기록이 바뀌어 직접 복사도 중단했습니다. 요약을 다시 만들어 주세요.');return false;}
      selectHandoffFallback();$('handoff-status').textContent='자동 복사하지 못해 내용을 선택했습니다. 직접 복사해 주세요. '+err.message;return false;
    }
    if(!handoffCurrent(preview)||preview!==handoffPreview||context.bookRaw!==patientBookRaw||recoveryConflict){invalidateHandoff('복사 요청 처리 중 기록 목록이 변경됐습니다. 클립보드의 환자와 버전을 다시 확인하세요.');return false;}
    $('handoff-status').textContent=`${patientLabel(activePatient())}의 요약을 클립보드에 복사했습니다. 병원으로 전송하지 않았습니다.`;return true;
  }catch(err){invalidateHandoff('최신 초안을 확인하지 못해 복사·읽어주기를 중단했습니다. '+err.message);error(err.message);return false;}
  finally{busy=false;controls();}
}
function invalidateObservationHistory(message='현재 환자의 저장된 관찰·수정 이력을 조회해 주세요.'){
  observationHistory=null;$('observation-history-list').replaceChildren();$('observation-history-status').textContent=message;
}
function renderObservationHistory(){
  if(observationHistory&&(client.pending||flow.job||!handoffOwnerMatches(observationHistory)||observationHistory.version!==client.session?.version||observationHistory.model.sourceSignature!==historySnapshotSignature(client.session)))
    invalidateObservationHistory('저장본이 바뀌었거나 저장 확인 중입니다. 이력을 다시 조회해 주세요.');
  const epoch=patientEpoch;$('observation-history-panel').hidden=!client.session;
  $('observation-history-load').disabled=locked()||!!criticalEditor||!client.session||client.session.state!=='DRAFT';
  $('observation-history-load').onclick=()=>{if(epoch===patientEpoch)return loadObservationHistory();};
}
async function loadObservationHistory(){
  if(locked()||criticalEditor)return false;
  if(!client.session){invalidateObservationHistory('환자 정보를 먼저 저장한 뒤 이력을 조회하세요.');return false;}
  const context=handoffContext();busy=true;capture.stopSpeaking();invalidateObservationHistory('저장된 이력을 조회하고 있습니다.');controls();
  try{
    const before=await freshHandoffSession(context);
    const history=await context.owner.api(`/api/voice/sessions/${encodeURIComponent(context.sid)}/history?actor_id=${encodeURIComponent(context.actorId)}`);
    const after=await freshHandoffSession(context);
    if(before.version!==after.version||historySnapshotSignature(before)!==historySnapshotSignature(after))throw Error('조회 중 저장본이 변경됐습니다. 이력을 다시 조회해 주세요.');
    const model=buildObservationHistory(after,history);
    client.session=after;observationHistory={model,epoch:patientEpoch,recordId:patientBook?.activeId,actorId:actor,sessionId:after.id,version:after.version};
    for(const entry of model.entries){
      const card=node('article',undefined,'qv-observation-entry');card.append(node('h3',`${entry.kindLabel} · 버전 ${entry.version}`),node('p',`기록 시각 ${entry.recordedAt}`,'qv-help'));
      const changes=node('ul');for(const change of entry.changes)changes.append(node('li',change));card.append(changes);$('observation-history-list').append(card);
    }
    $('observation-history-status').textContent=`${patientLabel(activePatient())} · 조회 시점 버전 ${after.version} · 전체 ${model.total}건 중 최근 ${model.shown}건${model.total>model.shown?' 표시':''}. ${model.hasEntries?'최신 기록부터 표시합니다.':'저장된 변경 이력이 없습니다.'}`;
    $('observation-history-panel').open=true;render();$('observation-history-panel').scrollIntoView?.({block:'nearest'});return true;
  }catch(err){invalidateObservationHistory('이력을 표시하지 못했습니다. '+err.message);error(err.message);return false;}
  finally{busy=false;controls();}
}
function physicalStorage(){try{return localStorage;}catch{return null;}}
function assertPatientStore(){
  const storage=physicalStorage();if(!storage)throw Error('브라우저 저장 공간을 사용할 수 없습니다.');
  if(storage.getItem(PATIENT_RECORDS_KEY)!==patientBookRaw){recoveryConflict=true;recoveryBlocked=true;throw Error('다른 탭에서 환자 기록 목록이 변경됐습니다.');}
  return storage;
}
function commitPatientBook(next){const storage=assertPatientStore(),result=writePatientRecords(storage,next,{expectedRaw:patientBookRaw});
  if(storage.getItem(PATIENT_RECORDS_KEY)!==result.raw)throw Error('환자 기록을 저장한 결과를 확인하지 못했습니다. 현재 화면을 유지하세요.');
  patientBook=result.book;patientBookRaw=result.raw;return result;}
function ensureActivePatient(){
  if(!patientBook)throw Error('환자 기록 목록을 먼저 확인하세요.');
  if(!patientBook.activeId){if(patientBook.records.length)throw Error('먼저 환자를 선택하거나 새 환자 시작을 누르세요.');
    patientBook=createPatientRecord(patientBook,'NOTE-'+requestId());}
  return activePatient();
}
function renderPatients(){
  $('patient-current').textContent=patientBook&&!patientBook.activeId&&patientBook.records.length?'현재 환자 선택 필요':`현재 ${patientLabel(activePatient())} · 음성 초안`;
  const selected=$('patient-select').value;$('patient-select').replaceChildren();
  const placeholder=node('option',patientBook?.records.length?'환자 기록 선택':'아직 보관한 기록 없음');placeholder.value='';$('patient-select').append(placeholder);
  for(const record of patientBook?.records||[]){const option=node('option',patientLabel(record)+(record.checkpoint?' · 보관됨':' · 새 기록'));option.value=record.id;$('patient-select').append(option);}
  $('patient-select').value=patientBook?.records.some(record=>record.id===selected)?selected:patientBook?.activeId||'';
  const blocked=busy||stopping||capture.active||!!flow.job||!!client.pending||recovering||recoveryConflict;
  $('patient-new').disabled=blocked||patientStoreInvalid;
  $('patient-switch').disabled=blocked||patientStoreInvalid||!patientBook?.records.length;
  $('patient-select').disabled=blocked||patientStoreInvalid;
  $('patient-previous').disabled=blocked||patientStoreInvalid||!patientBook?.previousId;
  $('patient-remove').disabled=blocked||patientStoreInvalid||!activePatient();
  $('patient-reset-invalid').hidden=!patientStoreInvalid;$('patient-reset-invalid').disabled=busy||stopping||capture.active||recovering||recoveryConflict;
}
function resetPatientUI(){
  patientEpoch++;capture.stopSpeaking();clearInterval(ticker);attempt=null;historyCount=0;press=null;lastSpoken='';deferred=new Map();recent=null;
  unsentVoice='';unappliedText='';manualAppliedText='';manualCopiedSource='';recommendationReady=false;criticalAcknowledged=new Map();criticalEditor=null;lastCriticalSpoken='';
  invalidateHandoff('현재 환자의 기록으로 인계용 요약을 만들어 주세요.');
  invalidateObservationHistory();
  error('');heard('');$('quick-history').replaceChildren();$('quick-history-count').textContent='0회';$('quick-time').textContent='00:00';
  $('recording-ended').hidden=true;$('recording-excluded').hidden=true;$('recording-excluded-text').textContent='';$('recording-review').value='';
  $('quick-feedback').hidden=true;$('recommendation-prep').hidden=true;$('critical-status').textContent='항목별 복창 확인을 진행하세요.';
  $('recording-review-status').textContent='검토한 뒤 반영 버튼을 눌러야 이 환자의 초안에 반영됩니다.';
}
function bootPatientRecords(){
  const storage=physicalStorage(),result=readPatientRecords(storage,location.origin);patientBookRaw=result.raw;patientStoreInvalid=false;legacyRecoveryRaw=storage?.getItem(RECOVERY_KEY)??null;
  if(result.status==='invalid'){patientStoreInvalid=true;throw Error('환자 기록 목록을 읽지 못했습니다. 원본을 덮어쓰지 않았습니다.');}
  if(result.status==='valid'){patientBook=result.book;return;}
  patientBook=emptyBook(location.origin);legacyRecoveryRaw=storage?.getItem(RECOVERY_KEY)??null;
  if(legacyRecoveryRaw!==null){
    let now=Date.now();try{const saved=JSON.parse(legacyRecoveryRaw);if(Number.isSafeInteger(saved.createdAt)&&saved.createdAt<=now)now=Math.min(now,saved.createdAt+12*60*60*1000-1);}catch{}
    if(readDraftRecovery(storage,location.origin,{now}).status!=='valid'){patientStoreInvalid=true;throw Error('기존 단일 복구본을 확인하지 못해 보존했습니다.');}
    const migrated=createPatientRecord(patientBook,'NOTE-'+requestId());
    try{commitPatientBook(updatePatientCheckpoint(migrated,migrated.activeId,legacyRecoveryRaw));}catch(err){patientStoreInvalid=true;throw err;}
    // Retire only the exact legacy value after its durable replacement exists.
    try{if(storage.getItem(PATIENT_RECORDS_KEY)===patientBookRaw&&storage.getItem(RECOVERY_KEY)===legacyRecoveryRaw)storage.removeItem(RECOVERY_KEY);}catch{}
  }
}
function blankPatientState(){const owner='QUICK-VOICE-'+requestId(),next=makeClient(owner);return {owner,client:next,flow:new QuickSession(next),payload:null};}
async function loadPatientState(record){
  if(!record.checkpoint)return blankPatientState();
  const result=readDraftRecovery({getItem:()=>record.checkpoint},location.origin);
  if(result.status!=='valid')throw Error(result.status==='expired'?'이 환자의 자동 복원 유효기간 12시간이 지났습니다. 기존 기록을 새 초안으로 바꾸지 않았습니다.':'환자 복구본을 확인할 수 없습니다.');
  const p=result.payload,next=makeClient(p.actorId);
  if(p.sessionId){await next.sync(p.sessionId);
    if(next.session?.id!==p.sessionId||next.session.state!=='DRAFT'||next.session.patient_id||!Number.isSafeInteger(next.session.version)||next.session.version<p.version||(next.session.actor_id!==undefined&&next.session.actor_id!==p.actorId))throw Error('대상 초안의 상태·작성자·버전이 환자 기록과 일치하지 않습니다.');}
  const nextFlow=new QuickSession(next);if(p.pending)nextFlow.restoreRecovery({pending:p.pending.request,job:p.pending.job});
  return {owner:p.actorId,client:next,flow:nextFlow,payload:p};
}
function applyPatientState(loaded){
  resetPatientUI();actor=loaded.owner;client=loaded.client;flow=loaded.flow;const p=loaded.payload;
  mode=p?.mode||(autoAvailable?'auto':'dictation');recordingMode=p?.recordingMode||'toggle';
  recoveryRaw=activePatient()?.checkpoint??null;recoveryBlocked=false;$('patient-select').value=patientBook?.activeId||'';
  if(p){unappliedText=p.recoveryText;$('recording-excluded-text').textContent=unappliedText;$('recording-excluded').hidden=!unappliedText;$('recording-review').value=p.manualText;
    for(const u of client.session?.utterances||[]){const item=node('article',undefined,'qv-msg is-user');item.append(node('p',u.transcript,'qv-bubble'),node('div','이 환자의 서버 저장 원문','qv-meta'));$('quick-history').append(item);historyCount++;}
    $('quick-history-count').textContent=`${historyCount}회`;
    if(p.pending){attempt=recoveredAttempt(p.pending.attempt);attempt.patientEpoch=patientEpoch;heard(attempt.sourceFinals.join(' '));}
    if(p.manualText){$('settings').hidden=false;$('recording-manual').open=true;$('settings-toggle').setAttribute('aria-expanded','true');}}
  $('mode-hint').textContent=modeHints[mode];$('quick-interim').textContent=idleHint();
  recoveryMessage(p?.pending?'이 환자의 저장 확인 대기 요청을 복원했습니다. 같은 요청으로 재시도하세요.':'환자별 미반영 내용은 따로 보관합니다. 전환만으로 입력·마이크·병원 문의를 실행하지 않습니다.');
  $('patient-status').textContent=`${patientLabel(activePatient())} 기록입니다. 입력 전에 현재 환자를 확인하세요.`;
  render();renderChanges();state(p?.pending?'저장 확인 대기':p?'복원 완료':'새 환자 기록');
}
function canLeavePatient(){return !busy&&!stopping&&!capture.active&&!flow.job&&!client.pending&&!recovering&&!recoveryConflict&&!patientStoreInvalid;}
function preservePatientBeforeSwitch(){
  if(!recoveryBlocked)return persistRecovery(true);
  if(unsentVoice||unappliedText||$('recording-review').value.trim()||criticalEditor)throw Error('현재 미반영 내용을 먼저 보관하세요.');
  assertPatientStore();return true;
}
async function switchPatient(id,{initial=false}={}){
  if(!initial&&!canLeavePatient())return false;
  try{
    if(!initial)preservePatientBeforeSwitch();
    const record=patientBook?.records.find(row=>row.id===id);if(!record)throw Error('선택한 환자 기록이 없습니다.');
    if(!initial&&id===patientBook.activeId)return true;
    recovering=true;controls();const loaded=await loadPatientState(record);assertPatientStore();
    if(!initial)commitPatientBook(selectPatientRecord(patientBook,id));
    applyPatientState(loaded);return true;
  }catch(err){if(initial)recoveryBlocked=true;$('patient-select').value=patientBook?.activeId||'';error('환자를 전환하지 않았습니다. '+err.message);$('patient-status').textContent=err.message;return false;}
  finally{recovering=false;controls();}
}
async function newPatient(){
  if(!canLeavePatient())return false;
  try{preservePatientBeforeSwitch();recovering=true;controls();const next=createPatientRecord(patientBook,'NOTE-'+requestId());commitPatientBook(next);
    applyPatientState(blankPatientState());return true;
  }catch(err){$('patient-select').value=patientBook?.activeId||'';error('새 환자를 시작하지 못했습니다. 현재 기록은 유지했습니다. '+err.message);return false;}
  finally{recovering=false;controls();}
}
async function previousPatient(){
  if(!patientBook?.previousId){error('이전에 열었던 환자 기록이 없습니다. 현재 환자는 유지합니다.');return false;}
  return switchPatient(patientBook.previousId);
}
function removeCurrentPatient(){
  if(!canLeavePatient()||!activePatient())return;
  try{commitPatientBook(removePatientRecord(patientBook,patientBook.activeId));resetPatientUI();actor='QUICK-VOICE-'+requestId();client=makeClient();flow=new QuickSession(client);recoveryRaw=null;
    recoveryBlocked=patientBook.records.length>0;recoveryMessage('현재 환자의 브라우저 복구본만 지웠습니다. 서버 원기록과 다른 환자 기록은 유지됩니다.');render();renderChanges();
  }catch(err){error(err.message);}finally{controls();}
}
function resetInvalidPatientStore(){
  if(!patientStoreInvalid||busy||stopping||capture.active||recovering||recoveryConflict)return;
  try{const storage=assertPatientStore();if(storage.getItem(RECOVERY_KEY)!==legacyRecoveryRaw)throw Error('기존 복구본이 다른 탭에서 바뀌었습니다.');
    storage.removeItem(PATIENT_RECORDS_KEY);if(legacyRecoveryRaw!==null)storage.removeItem(RECOVERY_KEY);
    patientBook=emptyBook(location.origin);patientBookRaw=null;legacyRecoveryRaw=null;patientStoreInvalid=false;recoveryBlocked=false;recoveryRaw=null;
    resetPatientUI();actor='QUICK-VOICE-'+requestId();client=makeClient();flow=new QuickSession(client);render();renderChanges();
    recoveryMessage('이 브라우저의 읽을 수 없던 환자 기록 목록을 초기화했습니다. 서버 원기록은 지우지 않았습니다.');
  }catch(err){error(err.message);}finally{controls();}
}
async function performPatientAction(a){
  if(a?.handoffRequested&&!a.handoffHandled){a.handoffHandled=true;await generateHandoff();return;}
  if(!a?.patientAction||a.patientActionHandled)return;a.patientActionHandled=true;
  if(a.patientAction==='new_patient')await newPatient();else await previousPatient();
}
const criticalItems=()=>buildCriticalReview(client.session);
const criticalKey=item=>(client.session?.id||'')+'|'+item.id;
const criticalPending=()=>criticalItems().filter(item=>criticalAcknowledged.get(criticalKey(item))!==item.signature);
function criticalSnapshot(items){return {patientEpoch,sessionId:client.session?.id,version:client.session?.version,items:items.map(item=>({id:item.id,signature:item.signature}))};}
function matchesCritical(snapshot){return snapshot.patientEpoch===patientEpoch&&client.session?.id===snapshot.sessionId&&client.session?.version===snapshot.version&&snapshot.items.every(old=>criticalItems().some(item=>item.id===old.id&&item.signature===old.signature));}
function criticalEditorText(){
  if(!criticalEditor?.dirty)return '';
  return '아직 반영하지 않은 항목 수정안입니다.\n'+Object.entries(criticalEditor.inputs).map(([field,input])=>`${labels[field]||field}: ${input.value||'입력 비움(값 없음)'}`).join('\n');
}
function readCritical(items=criticalPending(),automatic=false){
  if(capture.active||!items.length||!VoiceCapture.capabilities().tts)return false;
  const key=(client.session?.id||'')+'|'+items.map(item=>item.signature).join('|');
  if(automatic&&lastCriticalSpoken===key)return false;
  try{capture.speak('입력 내용 복창 확인입니다. '+items.map(item=>item.text).join('. ')+'. 원문과 맞는지 항목별로 확인해 주세요.');
    lastCriticalSpoken=key;$('critical-panel').open=true;$('critical-panel').scrollIntoView?.({block:'nearest'});return true;}catch(err){error('복창을 읽지 못했습니다. 화면에서 확인하거나 다시 듣기를 누르세요. '+err.message);return false;}
}
function renderCritical(){
  const items=criticalItems(),pending=criticalPending(),lockedNow=locked(),activeVerification=capture.active&&attempt?.target==='verification';
  $('critical-panel').hidden=!items.length&&!criticalEditor;$('critical-summary').textContent=`중요 항목 복창 확인 · ${pending.length?pending.length+'개 대기':'표시된 항목 확인됨'}`;
  $('critical-all').disabled=lockedNow||!!criticalEditor||!pending.length||pending.some(item=>!item.confirmable);
  $('critical-read').disabled=lockedNow||!VoiceCapture.capabilities().tts||!items.length;
  const shot=criticalSnapshot(pending);$('critical-all').onclick=()=>acknowledgeCritical(shot);
  $('critical-items').replaceChildren(...items.map(item=>{
    const ownerEpoch=patientEpoch,ownerShot=criticalSnapshot([item]);
    const confirmed=criticalAcknowledged.get(criticalKey(item))===item.signature;
    const card=node('article',undefined,'qv-critical-item'+(confirmed?' is-confirmed':''));card.setAttribute('data-critical-id',item.id);
    card.append(node('h3',item.label),node('p',item.text),node('small',confirmed?'입력 내용 확인됨':item.confirmable?'복창 확인 대기':'값 재확인·수정 필요'));
    const row=node('div',undefined,'qv-critical-actions');
    for(const [name,label,handler,disabled]of [
      ['confirm','맞아요',()=>acknowledgeCritical(ownerShot),lockedNow||!!criticalEditor||!item.confirmable||confirmed],
      ['edit','이 항목 수정',()=>{if(ownerEpoch===patientEpoch&&matchesCritical(ownerShot))openCriticalEditor(item);},lockedNow||!!criticalEditor],
      ['read','다시 듣기',()=>{if(ownerEpoch===patientEpoch&&matchesCritical(ownerShot))readCritical([item]);},lockedNow||!VoiceCapture.capabilities().tts],
      ['voice',activeVerification&&attempt.verification.items[0].id===item.id?'말하기 마치기':'말로 확인',()=>{
        if(ownerEpoch!==patientEpoch||!matchesCritical(ownerShot))return;
        if(capture.active&&attempt?.target==='verification'&&attempt.verification.items[0].id===item.id)return finish();
        if(locked()||criticalEditor||!item.confirmable)return;return record('verification',false,criticalSnapshot([item]));
      },(!activeVerification&&lockedNow)||!!criticalEditor||!item.confirmable||(capture.active&&(!activeVerification||attempt.verification.items[0].id!==item.id))]
    ]){const button=node('button',label,'qv-chip-btn');button.type='button';button.disabled=disabled;button.setAttribute('data-critical-action',name);button.onclick=handler;row.append(button);}
    card.append(row);return card;
  }));
  $('critical-editor').hidden=!criticalEditor;
  if(criticalEditor){$('critical-save').disabled=lockedNow;$('critical-cancel').disabled=lockedNow;
    for(const input of Object.values(criticalEditor.inputs))input.disabled=lockedNow;}
}
async function acknowledgeCritical(shot){
  if(locked()||criticalEditor||!shot.items.length||!matchesCritical(shot))return false;
  busy=true;capture.stopSpeaking();controls();
  try{await client.sync();
    if(client.session?.state!=='DRAFT'||client.session.patient_id||(client.session.actor_id!==undefined&&client.session.actor_id!==actor)){
      recoveryBlocked=true;recoveryMessage('서버 초안의 상태·작성자가 달라 작업을 멈췄습니다. 원기록을 확인하거나 새 기록을 명시적으로 시작하세요.');
      throw Error('복창 확인 대상인 미등록 음성 초안이 아닙니다.');}
    if(!matchesCritical(shot)){$('critical-status').textContent='기록이 갱신됐습니다. 최신 표시를 다시 확인해 주세요.';error('초안이 갱신되어 확인 상태를 바꾸지 않았습니다. 최신 표시를 다시 확인해 주세요.');render();persistRecovery();return false;}
    const items=criticalItems().filter(item=>shot.items.some(old=>old.id===item.id));
    if(items.some(item=>!item.confirmable))return false;
    for(const item of items)criticalAcknowledged.set(criticalKey(item),item.signature);
    $('critical-status').textContent='표시된 입력 내용을 확인했습니다. 환자 정보의 최종 확인·등록은 별도 화면에서 진행합니다.';
    render();persistRecovery();return true;
  }catch(err){error('최신 초안을 읽지 못해 확인 상태를 바꾸지 않았습니다. '+err.message);return false;}
  finally{busy=false;controls();}
}
function openCriticalEditor(item){
  if(locked()||criticalEditor)return;
  criticalAcknowledged.delete(criticalKey(item));
  const inputs={},wrapper=$('critical-inputs');wrapper.replaceChildren();
  for(const field of item.fields){const label=node('label',labels[field]||field),value=item.currentValues[field];
    let input;
    if(['consciousness','breathing'].includes(field)){
      input=node('select');for(const [v,t]of [['','값 비움'],...(field==='breathing'?[['normal','정상'],['abnormal','비정상'],['absent','호흡 없음'],['unknown','미확인']]:[['alert','명료'],['impaired','저하'],['unresponsive','반응 없음'],['unknown','미확인']])]){const option=node('option',t);option.value=v;input.append(option);}
    }else if(['symptoms','denied_symptoms','uncertain_symptoms'].includes(field)){input=node('textarea');input.rows=2;}
    else{input=node('input');input.type='text';input.inputMode='decimal';}
    input.setAttribute('data-critical-field',field);
    input.value=value===undefined?'':Array.isArray(value)?value.join(', '):String(value);
    input.oninput=()=>{if(criticalEditor)criticalEditor.dirty=true;persistRecovery();};inputs[field]=input;label.append(input);wrapper.append(label);
  }
  criticalEditor={item,shot:criticalSnapshot([item]),inputs,dirty:false};$('critical-editor-title').textContent=item.label+' 수정';
  $('critical-status').textContent='이 항목만 수정합니다. 빈 값은 미확인으로 남기며 저장 후 다시 복창 확인합니다.';
  $('critical-panel').open=true;controls();
}
async function saveCriticalEditor(){
  const editor=criticalEditor;if(!editor||locked())return;
  if(!matchesCritical(editor.shot)){error('수정 중 초안이 바뀌었습니다. 입력한 수정안은 검토칸에 보관하고 최신 항목을 다시 여세요.');return;}
  try{if(Object.values(editor.inputs).some(input=>input.validity?.badInput))throw Error('숫자 입력을 끝낸 뒤 저장하세요. 기존 값은 변경하지 않았습니다.');
    const values=Object.fromEntries(Object.entries(editor.inputs).map(([field,input])=>[field,input.value]));
    const patch=planCriticalCorrection(editor.item,values);
    if(!Object.keys(patch.changes).length&&!patch.unset_fields.length){$('critical-status').textContent='바뀐 값이 없습니다. 맞아요 버튼으로 입력 내용을 확인하세요.';return;}
    const a={target:'correction',mode:'dictation',fragments:[],sourceFinals:[],excludedFinals:[],apiText:'',finished:true,endReason:'correction',correctionEditor:editor};
    attempt=a;capture.stopSpeaking();await send(a,()=>flow.correct(patch));
  }catch(err){error(err.message);controls();}
}
function recoveryStorage(){return {getItem(key){assertPatientStore();return key===RECOVERY_KEY?(activePatient()?.checkpoint??null):null;}};}
function recoveryMessage(text){$('recovery-status').textContent=text;}
const guardedRecoveryStorage={
  getItem(key){return recoveryStorage().getItem(key);},
  setItem(key,value){if(key!==RECOVERY_KEY)throw Error('잘못된 환자 복구 키입니다.');assertPatientStore();const current=ensureActivePatient();
    commitPatientBook(updatePatientCheckpoint(patientBook,current.id,value));recoveryRaw=value;},
  removeItem(key){if(key!==RECOVERY_KEY)throw Error('잘못된 환자 복구 키입니다.');if(activePatient())commitPatientBook(removePatientRecord(patientBook,patientBook.activeId));recoveryRaw=null;}
};
function attemptRecovery(a){
  return {target:a.target,mode:a.mode||mode,questionId:a.question?.id||null,questionText:a.question?.question||'',
    apiText:a.apiText??a.fragments.join(' '),sourceText:(a.sourceFinals||a.fragments).join(' '),excludedText:(a.excludedFinals||[]).join(' '),
    unappliedBefore:a.unappliedBefore||'',manual:!!a.manual,copiedFrom:a.copiedFrom||'',beforeVersion:a.before?.version||0,
    beforeFlagged:a.before?.flagged||[],trigger:a.trigger?{action:a.trigger.action,command:a.trigger.command}:null};
}
function recoveredAttempt(a){return {...a,fragments:[a.apiText],sourceFinals:[a.sourceText],excludedFinals:a.excludedText?[a.excludedText]:[],
  question:a.questionId?{id:a.questionId,question:a.questionText}:null,before:{version:a.beforeVersion,flagged:a.beforeFlagged},
  finished:true,stopCommand:a.trigger?.command||'',endedAt:Date.now(),endReason:'recovery'};}
function persistRecovery(required=false){
  try{
    if(recovering||recoveryBlocked)throw Error(recoveryConflict?'다른 탭에서 복구 기록이 변경됐습니다.':'먼저 저장된 기록을 복원하거나 새 기록을 시작하세요.');
    const saved=flow.exportRecovery(),pending=saved.pending?{request:saved.pending,job:saved.job,attempt:attemptRecovery(attempt)}:null;
    const recoveryText=!pending&&unsentVoice?[attempt?.unappliedBefore??unappliedText,unsentVoice,attempt?.trigger?(attempt.excludedFinals||[]).join(' '):''].filter(Boolean).join(' '):unappliedText;
    const manual=$('recording-review').value,reviewText=manual.trim()===manualAppliedText?'':manual;
    const manualText=[reviewText,pending?.job.phase==='correct'?'':criticalEditorText()].filter(Boolean).join('\n\n');
    if(!client.session&&!pending&&!recoveryText&&!manualText)return true;
    writeDraftRecovery(guardedRecoveryStorage,{serverOrigin:client.server,actorId:actor,sessionId:client.session?.id||null,
      version:client.session?.version||0,pending,recoveryText,manualText,mode,recordingMode});
    recoveryMessage(pending?'이 브라우저에 요청을 보관했습니다 · 서버 저장 결과 확인 필요':'브라우저 복구본 저장됨 · 서버 저장 여부는 환자 요약에서 확인하세요.');return true;
  }catch(err){recoveryMessage('브라우저 임시 저장 실패 · 현재 화면을 유지하고 원문을 복사해 주세요. '+err.message);
    if(required)throw err;return false;}
}
async function resumeRecovery(){
  if(busy||capture.active||stopping||recoveryConflict)return;
  recovering=true;recoveryBlocked=false;controls();
  try{
    bootPatientRecords();
    if(!patientBook.activeId){recoveryBlocked=patientBook.records.length>0;recoveryMessage(recoveryBlocked?'환자를 선택하거나 새 환자 시작을 누르세요.':'환자별로 임시 저장하고 마지막 저장 후 12시간 이내의 기록을 복원합니다.');return;}
    const loaded=await loadPatientState(activePatient());assertPatientStore();applyPatientState(loaded);
  }catch(err){recoveryBlocked=true;recoveryMessage('복구 확인 필요 · '+err.message);error(err.message);}
  finally{recovering=false;controls();}
}

function keepUnapplied(text) {
  if(!text.trim())return;
  unappliedText=[unappliedText,text].filter(Boolean).join(' ');
  $('recording-excluded-text').textContent=unappliedText;
  $('recording-excluded').hidden=false;
}
function pauseRecommendation(a) {
  recommendationReady=false;$('recommendation-prep').hidden=true;
  // A definitive failure must not leave an older Continue action enabled, or
  // lose this attempt's unsaved prefix when a later command opens the draft.
  // Uncertain writes remain owned by exact retry, never a second manual write.
  if(!a?.trigger||client.pending||a.unsentRecovered)return;
  a.unsentRecovered=true;
  const text=a.apiText??a.fragments.join(' ');
  if(text.trim()){
    unappliedText=[a.unappliedBefore||'',text,...a.excludedFinals].filter(Boolean).join(' ');
    $('recording-excluded-text').textContent=unappliedText;$('recording-excluded').hidden=false;
  }
}
// This is a preparation step. Clinical confirmation, location, consent and
// patient registration remain explicit actions on the existing main screen.
function afterTrigger(a) {
  if(!a.trigger||a.actionHandled)return;
  a.actionHandled=true;
  capture.stopSpeaking();
  if(a.trigger.action==='handoff_summary'){a.handoffRequested=true;return;}
  if(['new_patient','previous_patient'].includes(a.trigger.action)){a.patientAction=a.trigger.action;return;}
  if(a.trigger.action==='stop'){
    state('저장 후 중단');$('quick-status').textContent='명령 앞의 내용을 저장하고 말하기를 마쳤습니다. 이어서 말하면 같은 초안에 추가됩니다.';
    return;
  }
  if(!client.session){
    state('환자 정보 필요');$('quick-status').textContent='아직 저장된 초안이 없습니다. 환자 정보를 먼저 말한 뒤 병원 추천을 요청해 주세요.';
    appendApp('아직 저장된 초안이 없습니다. 환자 정보를 먼저 말해 주세요.');return;
  }
  recommendationReady=true;$('recommendation-prep').hidden=false;
  $('recommendation-status').textContent='같은 초안의 저장 결과를 확인했습니다. 환자 정보·위치·동의를 확인한 뒤 병원 후보를 조회할 수 있습니다.';
  state('추천 준비');$('quick-status').textContent='저장한 초안으로 추천 준비를 시작합니다. 아래 계속 버튼으로 정보 확인 화면에 이어가세요.';
  appendApp('앞 내용을 저장했습니다. 정보 확인·병원 추천으로 계속할 수 있습니다. 명령 뒤의 미반영 내용은 별도로 검토해 주세요.');
}
function handoffText() {
  const manual=$('recording-review').value;
  const unsaved=manual.trim()&&manual.trim()!==manualAppliedText?manual:'';
  return unsaved&&unsaved!==unappliedText?[unappliedText,unsaved].filter(Boolean).join('\n\n'):unappliedText||unsaved;
}

const node = (tag, text, className) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const has = value => value !== null && value !== undefined;
const itemKeys = id => ITEM_KEYS[id]||[id];
function error(text) {$('quick-error').textContent=text;$('quick-error').hidden=!text;}
function state(text) {$('quick-state').textContent=text;}
function heard(text) {$('quick-transcript').textContent=text;$('quick-live').hidden=!(text||capture.active);if(text)timelineEnd();}
function locked() {return busy||stopping||capture.active||!!flow.job||recovering||recoveryBlocked;}
// The server accepts an answer to any offered item, so a deferred question can be skipped over.
function currentQuestion() {
  const plan=client.session?.followup;
  if(!plan)return null;
  const next=(plan.items||[]).find(item=>item.askable!==false&&!deferred.has(item.id))||plan.next_question;
  return next&&!deferred.has(next.id)?next:null;
}
function pttControl(id,target,blocked,idle,holding) {
  const button=$(id), mine=capture.active&&attempt?.target===target;
  button.disabled=blocked||(capture.active&&!mine);
  button.textContent=!mine?idle:!attempt.listening?(attempt.held?'마이크 준비 중… 누른 채로 기다리세요':'시작 취소'):attempt.held?holding:'한 번 눌러 마치기';
  button.setAttribute('aria-pressed',String(mine));
}
function controls() {
  renderStorageReceipt();
  renderPatients();
  const active=capture.active, isLocked=locked()||!!criticalEditor, q=currentQuestion(), caps=VoiceCapture.capabilities();
  const hasFields=Object.keys(client.session?.fields||{}).length>0, micBlocked=busy||stopping||!!flow.job||recovering||recoveryBlocked||!!criticalEditor||!caps.browser;
  // With a question showing, the big button answers it and general input moves to the side button.
  pttControl('quick-record','transcript',micBlocked,q?'다른 내용':recordingMode==='hold'?'누른 채로 말하기':'한 번 눌러 말하기','손을 떼면 전송');
  pttControl('followup-record','answer',micBlocked||!q,recordingMode==='hold'?'누른 채로 답하기':'한 번 눌러 답하기','손을 떼면 답변 전송');
  $('dock').classList.toggle('has-question',!!q);
  $('dock').classList.toggle('is-recording',active);
  $('quick-live').hidden=!(active||$('quick-transcript').textContent);
  $('quick-cancel').hidden=!active;
  $('mode-auto').disabled=isLocked||!autoAvailable;
  $('mode-dictation').disabled=isLocked;
  $('mode-command').disabled=isLocked||!hasFields;
  for(const name of ['auto','dictation','command'])$('mode-'+name).setAttribute('aria-pressed',String(mode===name));
  for(const name of ['toggle','hold']){$('recording-'+name).disabled=isLocked;$('recording-'+name).setAttribute('aria-pressed',String(recordingMode===name));}
  $('followup-unavailable').disabled=isLocked||!q;
  for(const option of $('followup-options').children)option.disabled=isLocked;
  $('followup-speak').disabled=isLocked||!q||!caps.tts;
  $('quick-new').disabled=busy||stopping||active||!!flow.job||recovering||recoveryConflict||patientStoreInvalid;
  $('quick-readback').disabled=isLocked||!hasFields||!caps.tts;
  $('quick-retry').hidden=!client.pending||busy||stopping;
  $('quick-retry').disabled=busy||stopping||active||recovering||recoveryBlocked;
  const manualText=$('recording-review').value.trim();
  $('recording-review').disabled=isLocked;
  $('recording-excluded-copy').disabled=isLocked;
  $('recording-review-apply').disabled=isLocked||!manualText||manualText.length>RECORDING_LIMITS.text||manualText===manualAppliedText;
  $('recording-review-count').textContent=`${manualText.length} / ${RECORDING_LIMITS.text}자`;
  $('recommendation-continue').disabled=isLocked||!recommendationReady||!client.session||criticalPending().length>0;
  $('recovery-retry').hidden=!recoveryBlocked;$('recovery-retry').disabled=busy||stopping||active||recovering||recoveryConflict;
  renderCritical();
  renderHandoff();
  renderObservationHistory();
}
function valueText(key,value) {
  if(key==='onset_at')return new Date(value*1000).toLocaleString('ko-KR');
  if(Array.isArray(value))return value.join(', ')||'없음';
  if(typeof value==='boolean')return value?'예':'아니오';
  return (names[value]||String(value))+(units[key]?' '+units[key]:'');
}
const order = key => {const index=Object.keys(labels).indexOf(key);return index<0?99:index;};
const named = list => [...(list||[])].sort((a,b)=>order(a)-order(b)).map(f=>labels[f]||f);
// Fields of questions answered "미측정/모름": left unresolved on purpose, never filled.
function unavailableKeys() {return new Set((client.session?.followup?.items||[]).filter(item=>item.answer_status==='unavailable').flatMap(item=>item.fields||[]));}
function unavailableQuestions() {return new Set((client.session?.followup?.items||[]).filter(item=>item.answer_status==='unavailable').map(item=>item.id));}
function fieldCard(key,value,meta,unavailable) {
  const unclear=meta.needs_clarification===true, card=node('dl',undefined,unclear?'quick-field needs-clarification':'quick-field');
  // A value-less change report keeps the earlier observation; it is shown as earlier, not current.
  card.append(node('dt',(labels[key]||key)+(unclear?(unavailable?' · 미측정·확인 불가':' · 재확인 필요'):'')),node('dd',!has(value)?'값 없음':valueText(key,value)+(unclear?' (이전 관찰값)':'')));
  const evidence=(unclear?meta.clarification_evidence:meta.evidence)||[];
  const source=meta.source==='followup_answer'?(meta.contextual_answer?'질문 답변 · 질문 문맥으로 해석':'질문 답변'):'';
  const note=[evidence.join(' / '),source].filter(Boolean).join(' · ');
  if(note)card.append(node('p',note));
  return card;
}
function sinceText(epoch) {const minutes=Math.max(0,Math.round((Date.now()/1000-epoch)/60));return minutes<60?`${minutes}분 전`:`${Math.floor(minutes/60)}시간 ${minutes%60}분 전`;}
function itemValue(id,f) {
  if(id==='age')return has(f.age)?`${f.age}세`:has(f.age_group)?String(f.age_group):null;
  if(id==='symptoms')return f.symptoms?.length?f.symptoms.slice(0,2).join(', ')+(f.symptoms.length>2?' 외':''):f.denied_symptoms?.length?`없음 ${f.denied_symptoms.slice(0,2).join('·')}`:f.uncertain_symptoms?.length?`확인 필요 ${f.uncertain_symptoms[0]}`:null;
  if(id==='onset_at')return has(f.onset_at)?sinceText(f.onset_at):null;
  if(id==='blood_pressure')return has(f.systolic_bp)||has(f.diastolic_bp)?`${f.systolic_bp??'—'}/${f.diastolic_bp??'—'}`:null;
  return has(f[id])?names[f[id]]||String(f[id]):null;
}
function itemState(id,ctx) {
  const keys=itemKeys(id), changes=(recent?.changes||[]).filter(c=>keys.includes(c.field));
  if(deferred.has(id))return 'deferred';
  if(ctx.asking===id)return 'asking';
  if(ctx.unavailable.has(id))return 'unavailable';
  if(keys.some(k=>ctx.meta[k]?.needs_clarification===true))return 'unclear';
  if(changes.some(c=>has(c.previous)&&has(c.current)))return 'changed';
  if(changes.some(c=>!has(c.previous)&&has(c.current)))return 'new';
  return itemValue(id,ctx.fields)?'filled':'missing';
}
const STATE_ORDER = ['deferred','asking','unavailable','unclear','changed','new','filled','missing'];
// Only the server's returned utterance list is a storage receipt. Empty extracted
// fields do not mean the transcript was lost, and a created draft is not a saved utterance.
function renderStorageReceipt() {
  const s=client.session, count=Array.isArray(s?.utterances)?s.utterances.length:null;
  const pending=Boolean(client.pending||flow.job||busy);
  let text=!s?'아직 서버에 전송한 발화가 없습니다.':count===null?'초안 응답을 받았습니다. 저장 발화 수는 확인되지 않았습니다.':count===0?'초안 준비됨 · 서버에서 확인한 저장 발화 0건':`서버에서 확인한 저장 발화 ${count}건`;
  if(s&&count>0&&!Object.keys(s.fields||{}).length)text+=' · 원문은 저장됐고, 구조화된 환자 항목은 확인이 필요합니다.';
  if(pending)text+=' · 이번 입력의 저장 결과를 확인하고 있습니다.';
  else if(capture.active)text+=' · 현재 말하는 내용은 종료 후 전송합니다.';
  $('quick-storage-status').textContent=text;
  $('quick-storage-receipt').hidden=!s;
  $('quick-storage-details').textContent=s?`서버: ${client.server}\n기록 ID: ${s.id}\n초안 버전: ${s.version}\n서버 확인 발화: ${count===null?'확인되지 않음':count+'건'}`:'';
}
function summaryChip(ids,ctx) {
  const states=ids.map(id=>itemState(id,ctx)), chipState=STATE_ORDER.find(s=>states.includes(s));
  const single=ids.length===1, parts=ids.map((id,i)=>{
    const value=itemValue(id,ctx.fields), s=states[i], label=SHORT[id];
    if(s==='deferred')return `${label} 보류`;
    if(s==='asking')return `${label} 질문 중`;
    if(s==='unavailable')return `${label} 미측정`;
    if(!value)return single?`${label} —`:`${label} ?`;
    const change=(recent?.changes||[]).find(c=>itemKeys(id).includes(c.field)&&c.direction&&has(c.previous));
    const text=(single?`${label} `:'')+value;
    return text+(s==='unclear'?' 재확인':s==='changed'&&change&&directions[change.direction]?' '+directions[change.direction][0]:'');
  });
  const empty=states.every(s=>s==='missing');
  const text=empty&&!single?`${ids.map(id=>SHORT[id]).join('·')} —`:parts.join(ids[0]==='age'?' ':' · ');
  const complaint=ids.includes('symptoms')&&ctx.fields.symptoms?.length&&['filled','new','changed'].includes(chipState);
  return node('span',text,`qv-sum-chip is-${chipState}${complaint?' is-complaint':''}`);
}
function render() {
  const s=client.session, fields=s?.fields||{}, meta=s?.field_metadata||{}, unavailable=unavailableKeys();
  const entries=Object.entries(fields).sort(([a],[b])=>order(a)-order(b));
  $('quick-fields').replaceChildren();$('derived-fields').replaceChildren();
  for(const [key,value] of entries)$(observationKeys.has(key)?'quick-fields':'derived-fields').append(fieldCard(key,value,meta[key]||{},unavailable.has(key)));
  for(const key of Object.keys(meta).sort((a,b)=>order(a)-order(b)))if(!(key in fields)&&meta[key]?.needs_clarification===true&&observationKeys.has(key))$('quick-fields').append(fieldCard(key,null,meta[key],unavailable.has(key)));
  if(!$('quick-fields').children.length)$('quick-fields').append(node('p','아직 추출된 관찰 정보가 없습니다. 아래 마이크 버튼으로 말해 보세요.','quick-empty'));
  const derived=entries.filter(([k])=>!observationKeys.has(k)).length;
  $('quick-derived').hidden=!derived;$('derived-count').textContent=`${derived}개`;
  const warnings=s?.warnings||[];
  $('quick-warnings').hidden=!warnings.length;$('warning-count').textContent=`${warnings.length}개`;
  $('warning-items').replaceChildren(...warnings.map(text=>node('li',text)));
  const ctx={fields,meta,unavailable:unavailableQuestions(),asking:currentQuestion()?.id};
  $('summary-chips').replaceChildren(...SUMMARY.map(ids=>summaryChip(ids,ctx)));
  const filled=SUMMARY.flat().filter(id=>itemValue(id,fields)).length;
  $('quick-count').textContent=filled?`12개 중 ${filled}개`:s?.utterances?.length?'원문 저장됨 · 항목 확인 필요':'기록 대기';
  const ownerLabel=patientBook?.records.length&&!activePatient()?'환자 선택 필요':patientLabel(activePatient());
  $('quick-session').textContent=s?`${ownerLabel} · 초안 ${String(s.id).slice(-6)} · 버전 ${s.version}`:`${ownerLabel} · 새 기록`;
  renderFollowup();controls();
}
function answerOptions(q) {return q.id==='symptoms'?(q.uncertain_items?.length===1?['있음','없음']:[]):OPTIONS[q.id]||[];}
function renderFollowup() {
  const plan=client.session?.followup, items=plan?.items||[];
  for(const id of [...deferred.keys()])if(!items.some(item=>item.id===id&&item.askable!==false))deferred.delete(id);
  const q=currentQuestion();
  $('followup-card').hidden=!q;
  if(q){
    $('followup-question').textContent=q.question;$('followup-hint').textContent=q.answer_hint||'';
    $('followup-reason').textContent=reasons[q.reason]||'';
    $('followup-progress').textContent=Number.isInteger(plan.unanswered_count)?`남은 ${Math.max(0,plan.unanswered_count-deferred.size)}${deferred.size?` · 보류 ${deferred.size}`:''}`:'';
  }
  const options=q?answerOptions(q):[];
  const offeredEpoch=patientEpoch,offeredVersion=client.session?.version;
  $('followup-options').replaceChildren(...options.map(text=>{const button=node('button',text,'qv-chip-btn');button.type='button';button.onclick=()=>{if(offeredEpoch===patientEpoch&&offeredVersion===client.session?.version)return tapAnswer(text);};return button;}));
  $('followup-options').hidden=!options.length;
  $('followup-deferred').hidden=!deferred.size;
  $('followup-deferred-text').textContent=deferred.size?`검토 때 확인 ${deferred.size}개 · ${[...deferred].map(([id,heardText])=>`${questionLabel(items.find(item=>item.id===id))} “${heardText}”`).join(', ')}`:'';
  const done=Boolean(client.session&&plan&&!q);
  $('followup-done').hidden=!done||deferred.size>0;
  $('followup-done').textContent=!done||deferred.size?'':plan.complete?'확인 질문 목록의 항목이 모두 기록됐습니다. 값은 원문과 비교해 검토하세요.':`더 묻지 않습니다. 미측정·확인 불가로 남긴 항목 ${plan.unresolved_count??''}개는 0이나 정상으로 채우지 않았습니다.`;
}
function snapshot() {const meta=client.session?.field_metadata||{};return {version:client.session?.version||0,flagged:Object.keys(meta).filter(k=>meta[k]?.needs_clarification===true)};}
// latest_changes describes the newest revision; without a new version this request changed nothing.
function computeRecent(before) {
  const s=client.session;
  if(!s||!before||s.version<=before.version)return null;
  const meta=s.field_metadata||{};
  return {changes:(s.latest_changes||[]).filter(c=>typeof c?.field==='string'),
    flagged:Object.keys(meta).filter(k=>meta[k]?.needs_clarification===true&&!before.flagged.includes(k)).sort((a,b)=>order(a)-order(b))};
}
function renderChanges() {
  const list=$('change-items');
  list.replaceChildren();
  if(!client.session||!recent){$('quick-changes').hidden=true;$('changes-kind').textContent='';return;}
  const s=client.session, changes=recent.changes, unavailable=unavailableKeys();
  const observed=changes.filter(c=>observationKeys.has(c.field)).sort((a,b)=>order(a.field)-order(b.field)), derived=changes.length-observed.length;
  for(const c of observed.filter(c=>has(c.previous)&&has(c.current))){
    const item=node('li',undefined,'change-update');
    item.append(node('span',labels[c.field]||c.field,'change-label'),node('s',valueText(c.field,c.previous)),node('span','→','change-arrow'),node('strong',valueText(c.field,c.current)));
    if(directions[c.direction])item.append(node('em',directions[c.direction],'change-'+c.direction));
    list.append(item);
  }
  for(const key of recent.flagged){
    const item=node('li',undefined,'change-clarify'), value=s.fields?.[key];
    item.append(node('span',labels[key]||key,'change-label'),node('span',has(value)?`이전 관찰값 ${valueText(key,value)}`:'이전 값 없음'),node('strong',unavailable.has(key)?'미측정 · 확인 불가로 남김':'현재 값 미확인 · 재확인 필요'));
    list.append(item);
  }
  const added=observed.filter(c=>!has(c.previous)&&has(c.current)), removed=observed.filter(c=>has(c.previous)&&!has(c.current));
  if(added.length)list.append(node('li',`새로 기록 ${added.length}개: ${added.map(c=>labels[c.field]).join(', ')}`,'change-added'));
  if(removed.length)list.append(node('li',`비움 ${removed.length}개: ${removed.map(c=>labels[c.field]).join(', ')}`,'change-removed'));
  if(derived)list.append(node('li',`자동 해석 제안 ${derived}개 갱신`,'change-derived'));
  if(!list.children.length)list.append(node('li','바뀐 칸이 없습니다. 인식 원문과 확인할 내용을 살펴보세요.','change-none'));
  $('changes-kind').textContent=kinds[changes[0]?.change_kind]?'· '+kinds[changes[0].change_kind]:'';
  $('quick-changes').hidden=false;
}
function questionLabel(q) {return (q?.fields||[]).map(f=>labels[f]||f).join('·')||q?.id||'';}
function answerSummary(result) {
  const answer=result.answer||{}, applied=named(answer.applied_fields), ignored=named(answer.ignored_fields), parts=[];
  if(answer.unavailable)parts.push('미측정·확인 불가로 남겼습니다. 0이나 정상으로 채우지 않습니다.');
  else if(answer.accepted&&applied.length)parts.push(`답변을 반영했습니다: ${applied.join(', ')}${answer.contextual_answer?' (질문 문맥으로 해석)':''}.`);
  else if(!answer.accepted)parts.push('값을 알아듣지 못해 이 질문은 보류하고 다음 질문으로 넘어갑니다. 들린 말은 검토 목록에 남겼습니다.');
  if(ignored.length)parts.push(`질문 대상이 아닌 ${ignored.join(', ')} 값은 반영하지 않았습니다. 큰 버튼으로 다시 말해 주세요.`);
  return parts.join(' ')||'답변을 처리했습니다.';
}
function changeText(changes) {
  const fields=client.session?.fields||{}, by=Object.fromEntries(changes.map(c=>[c.field,c])), parts=[], arrow=c=>c?.direction&&directions[c.direction]?' '+directions[c.direction][0]:'';
  for(const c of changes){
    if(c.field==='diastolic_bp'&&by.systolic_bp)continue;
    if(c.field==='systolic_bp'||c.field==='diastolic_bp'){
      const s=by.systolic_bp, d=by.diastolic_bp;
      parts.push(`혈압 ${s?s.previous:fields.systolic_bp??'—'}/${d?d.previous:fields.diastolic_bp??'—'} → ${s?s.current:fields.systolic_bp??'—'}/${d?d.current:fields.diastolic_bp??'—'}${arrow(s||d)}`);
    } else parts.push(`${labels[c.field]} ${valueText(c.field,c.previous)} → ${valueText(c.field,c.current)}${arrow(c)}`);
  }
  return parts.join(', ');
}
// One short tag under the crew's bubble: what this utterance did to the draft.
function resultTag(a,result) {
  const answer=result.answer;
  if(a.target==='answer'){
    if(answer?.unavailable)return {text:'미측정 · 값을 채우지 않음',tone:'is-muted'};
    if(!answer?.accepted)return {text:'알아듣지 못해 보류 · 검토 때 확인',tone:'is-warn'};
    if(answer.ignored_fields?.length)return {text:`${named(answer.ignored_fields).join(', ')} 값은 반영 안 함`,tone:'is-warn'};
  }
  if(!recent)return null;
  const observed=recent.changes.filter(c=>observationKeys.has(c.field)), updated=observed.filter(c=>has(c.previous)&&has(c.current));
  if(updated.length)return {text:`${kinds[updated[0].change_kind]||'변경'} · ${changeText(updated)}`,tone:'is-change'};
  if(recent.flagged.length)return {text:`${recent.flagged.map(k=>labels[k]).join(', ')} 재확인 필요`,tone:'is-warn'};
  const added=observed.filter(c=>!has(c.previous)&&has(c.current));
  // An accepted answer already names what it filled ("반영: 의식").
  return added.length&&a.target!=='answer'?{text:`${added.length}칸 기록`,tone:'is-ok'}:null;
}
function timelineEnd() {const timeline=document.querySelector('.qv-timeline');if(timeline)timeline.scrollTop=timeline.scrollHeight;}
function appendApp(text,className='') {
  if(!text)return;
  const item=node('div',undefined,'qv-msg is-app'+(className?' '+className:''));
  item.append(node('p',text,'qv-bubble'));$('quick-history').append(item);timelineEnd();
}
function addHistory(a,result) {
  historyCount++;
  if(a.target==='answer'&&a.question)appendApp(a.question.question,'is-question');
  // A natural state report ("혈압이 100에 70으로 떨어졌어") comes back as command "observe": show it as a state change.
  const observe=result.command?.action==='observe';
  const title=a.target==='correction'?'복창 확인 · 항목 수정':a.manual?'직접 입력 · 검토한 내용을 초안에 반영':a.target==='answer'?`확인 질문 답변 · ${questionLabel(a.question)}`:a.mode==='command'?'수정 · 확인 명령':a.mode==='auto'?`자동 판단${result.effective_mode?` → ${observe?'상태 변화':result.effective_mode==='command'?'명령':'상태 설명'}`:''}`:'환자 상태 설명';
  const pick=a.pick, text=pick?pick.text:(a.sourceFinals||a.fragments).join(' ');
  const note=!pick?'':[pick.rank>0?`인식 후보 ${pick.rank+1}순위 사용(1순위 “${pick.alternatives[0]}”)`:'',pick.unavailable?'미측정 뜻으로 보고 보냄':''].filter(Boolean).join(' · ');
  const answer=result.answer||{}, tag=resultTag(a,result);
  const base=a.target==='answer'?(answer.unavailable?'미측정으로 남김':answer.accepted?`반영: ${named(answer.applied_fields).join(', ')}${answer.contextual_answer?' (질문 문맥으로 해석)':''}`:'반영 안 됨'):observe?'':result.command?`처리: ${actions[result.command.action]||result.command.action}`:tag?'':'음성 초안에 자동 반영';
  const detail=[base,note?`(${note})`:''].filter(Boolean).join(' ');
  const item=node('article',undefined,'qv-msg is-user quick-entry'), meta=node('div',undefined,'qv-meta');
  meta.append(node('strong',title));
  if(detail)meta.append(node('span',detail));
  if(tag)meta.append(node('span',tag.text,'qv-tag '+tag.tone));
  item.append(node('p',a.target==='correction'?'선택한 항목을 구조화된 값으로 수정했습니다.':(a.button?'[버튼] ':'')+text,'qv-bubble'),meta);
  if(!a.button)item.append(sourceDetail(a));
  $('quick-history').append(item);$('quick-history-count').textContent=`${historyCount}회`;timelineEnd();
}
function endLabel(a) {
  if(a.invalidTrigger)return `명령 재확인 · 실행 안 함 · ${new Date(a.endedAt||Date.now()).toLocaleTimeString('ko-KR')}`;
  const reason={button:'버튼으로 종료',release:'손을 떼어 종료',voice:`음성 명령 “${a.stopCommand||''}”`,limit:`${a.maxSeconds}초 시간 한도`,text_limit:'4000자 한도',recognizer:'브라우저 인식 종료',cancel:'취소',error:'음성 연결 오류',manual:'직접 입력 반영'}[a.endReason]||'말하기 종료';
  return `${reason}${a.failed&&a.endReason!=='error'?' · 종료 중 연결 오류':''} · ${new Date(a.endedAt||Date.now()).toLocaleTimeString('ko-KR')}`;
}
function sourceDetail(a) {
  if(a.target==='correction'){const detail=node('details',undefined,'qv-source');detail.append(node('summary','항목 수정 요청'),node('p','음성 인식문을 보내지 않고 선택한 항목만 PATCH로 수정했습니다. 환자 등록·병원 전송은 수행하지 않았습니다.'));return detail;}
  const detail=node('details',undefined,'qv-source');detail.append(node('summary','인식 원문 · API 전송 본문'));
  detail.append(node('p',(a.manual?'직접 입력 원문: ':'확정 인식 원문: ')+(a.sourceFinals||a.fragments).join(' ')),node('p','API 전송 본문: '+((a.apiText??a.fragments.join(' '))||'없음')),node('p',endLabel(a)));
  if(a.copiedFrom)detail.append(node('p','가져온 미반영 인식 원문: '+a.copiedFrom));
  if(a.excludedFinals?.length)detail.append(node('p','종료 명령 뒤 확정되어 본문에 넣지 않은 내용: '+a.excludedFinals.join(' ')));
  return detail;
}
function markEnd(a,reason) {
  if(!a)return;
  a.endReason=a.endReason||reason;a.endedAt=a.endedAt||Date.now();
  $('recording-ended').textContent=endLabel(a);$('recording-ended').hidden=false;
}
function addUnsentHistory(a,message) {
  const item=node('article',undefined,'qv-msg is-user quick-recording-log');
  item.append(node('p',(a.sourceFinals||a.fragments).join(' ')||'인식문 없음','qv-bubble'),node('div',message,'qv-meta'),sourceDetail(a));
  $('quick-history').append(item);timelineEnd();
}
function say(text,prefix='') {
  if(!text||!VoiceCapture.capabilities().tts||capture.active)return;
  try{capture.speak(text);}catch(err){error(prefix+err.message);}
}
function feedback(result,a) {
  const action=result.command?.action, tapOnly=result.operational_effect==='confirmation_requires_tap';
  const text=a.target==='answer'?answerSummary(result):action==='submit'?'시험 화면에서는 환자정보를 전송하지 않습니다.':tapOnly?(result.speak_text||'확인은 화면에서 눌러 주세요.'):result.speak_text||(action==='edit'?'수정한 값을 반영했습니다.':action==='confirm'?'현재 초안을 확인했습니다.':action==='undo'?'직전 변경을 되돌렸습니다.':'');
  $('quick-feedback').textContent=text;$('quick-feedback').hidden=!text;
  // Commands and notices answer in the conversation; answers are summarised by the bubble's tag.
  if(a.target!=='answer')appendApp(text);
  // Read aloud only with the microphone closed: requested readbacks, else the server's next question once.
  let spoken='';
  if($('critical-autoread').checked&&criticalPending().length&&!a.trigger&&action!=='missing'){
    readCritical(criticalPending(),true);$('critical-panel').open=true;return;
  }
  if(['review','missing'].includes(action)||tapOnly)spoken=result.speak_text||'';
  else if($('followup-autoread').checked){
    const q=currentQuestion(), key=q?q.id+'|'+q.question:'';
    if(q&&key!==lastSpoken){spoken=q.question;lastSpoken=key;}
    // The server's speak_text would repeat a deferred question; only say that answers were set aside.
    else if(!q&&a.target==='answer')spoken=deferred.size?'알아듣지 못한 답은 보류했습니다. 검토할 때 확인해 주세요.':result.speak_text||'';
  }
  say(spoken,'정보 처리는 완료했지만 읽어주기를 시작하지 못했습니다. ');
}
function settle(a,result) {
  unsentVoice='';
  if(a.target==='correction'){criticalEditor=null;$('critical-status').textContent='수정한 값이 저장됐습니다. 바뀐 항목을 다시 복창 확인하세요.';}
  if(a.manual){manualAppliedText=a.apiText;$('recording-review-status').textContent='검토한 내용을 초안에 반영했습니다. 같은 본문은 다시 보내지 않습니다.';
    if(a.copiedFrom&&a.copiedFrom===unappliedText){unappliedText='';$('recording-excluded-text').textContent='';$('recording-excluded').hidden=true;}}
  if(a.target==='answer'&&a.question){
    if(result.answer?.accepted)deferred.delete(a.question.id);
    else deferred.set(a.question.id,a.pick?.text||a.fragments.join(' '));
  }
  recent=computeRecent(a.before);heard('');
  addHistory(a,result);render();renderChanges();feedback(result,a);state('처리 완료');
  $('quick-status').textContent=a.target!=='answer'?'자동으로 정리했습니다. 빠지거나 잘못된 값은 원문과 비교해 주세요.':result.answer?.accepted?'답변을 처리했습니다. 다음 질문이나 결과를 확인하세요.':'알아듣지 못한 답은 보류했습니다. 검토할 때 고치면 됩니다.';
  afterTrigger(a);
  if(a.trigger?.action==='recommend'&&$('critical-autoread').checked)readCritical(criticalPending(),true);
  persistRecovery();
}
async function failure(err) {
  pauseRecommendation(attempt);
  if(!client.pending)unsentVoice=attempt?.manual||attempt?.unsentRecovered?'':attempt?.apiText||attempt?.fragments?.join(' ')||'';
  state('처리 확인 필요');error(err.message);
  if(err.status===409&&client.session){
    // A changed draft or question is re-read, never answered against the stale question.
    try{await client.sync();recent=null;error(`${err.message} 최신 초안과 질문을 다시 불러왔습니다. 확인한 뒤 다시 말해 주세요.`);}
    catch(syncError){error(`${err.message} 최신 초안을 불러오지 못했습니다. ${syncError.message}`);}
  }
  $('quick-status').textContent='원문은 유지했습니다. 결과가 불확실한 요청은 같은 요청으로 재시도하세요.';persistRecovery();render();
}
async function send(a,request) {
  busy=true;a.before=snapshot();state('정보 정리 중');
  $('quick-status').textContent=a.target==='answer'?'질문에 대한 답을 서버에서 확인하고 있습니다.':'긴 문장을 서버에서 한 번에 처리하고 있습니다.';controls();
  try{settle(a,await request());}catch(err){await failure(err);}
  finally{busy=false;controls();}
  await performPatientAction(a);
}
async function finalize(a) {
  if(attempt!==a||a.cancelled||a.finished||a.failed||capture.active)return;
  if(a.patientEpoch!==undefined&&a.patientEpoch!==patientEpoch)return;
  if(stopping&&(a.target==='verification'||['new_patient','previous_patient','handoff_summary'].includes(a.trigger?.action)))return;
  a.finished=true;clearInterval(ticker);markEnd(a,'recognizer');
  if(a.target==='verification'){
    const text=a.fragments.join(' '),reply=classifyCriticalReply(text);heard('');
    if(!matchesCritical(a.verification)){if(text.trim())keepUnapplied(text);unsentVoice='';persistRecovery();$('critical-status').textContent='확인 중 기록이 바뀌었습니다. 최신 값을 다시 확인해 주세요.';controls();return;}
    if(reply==='confirm'){const accepted=await acknowledgeCritical(a.verification);if(!accepted&&text.trim())keepUnapplied(text);unsentVoice='';persistRecovery();state(accepted?'복창 확인 처리':'복창 재확인 필요');}
    else if(reply==='correct'){unsentVoice='';const item=criticalItems().find(item=>item.id===a.verification.items[0].id);if(item)openCriticalEditor(item);persistRecovery();state('항목 수정');}
    else{if(text.trim())keepUnapplied(text);unsentVoice='';$('critical-status').textContent='맞아요 또는 아니요로만 확인합니다. 추가로 인식한 내용은 미반영으로 보존했습니다.';state('복창 확인 필요');persistRecovery();}
    controls();return;
  }
  if(a.trigger){
    // Recognition may split a negation/report across finals during stop drain:
    // "종료" / "하지 마" must not execute as a confirmed stop instruction.
    const complete=[a.triggerSource,...a.sourceFinals.slice(a.triggerFinalCount)].join(' ');
    const checked=findVoiceTrigger(complete);
    if(!checked||checked.action!==a.trigger.action||checked.prefix!==a.trigger.prefix){
      a.invalidTrigger=true;a.apiText='';a.trigger=null;
      recommendationReady=false;$('recommendation-prep').hidden=true;
      unappliedText=[a.unappliedBefore||'',a.sourceFinals.join(' ')].filter(Boolean).join(' ');
      unsentVoice='';persistRecovery();
      $('recording-excluded-text').textContent=unappliedText;$('recording-excluded').hidden=false;
      markEnd(a,'voice');addUnsentHistory(a,'명령을 부정하거나 인용한 표현으로 확인되어 실행하지 않았습니다. 전체 인식문을 미반영 검토칸에 남겼습니다.');
      heard('');state('명령 실행 안 함');$('quick-interim').textContent='전체 인식문을 검토한 뒤 직접 반영하세요. 서버에는 전송하지 않았습니다.';controls();return;
    }
    a.trigger=checked;a.stopCommand=checked.command;
    a.excludedFinals=checked.suffix.trim()?[checked.suffix]:[];
    unappliedText=[a.unappliedBefore||'',...a.excludedFinals].filter(Boolean).join(' ');
    $('recording-excluded-text').textContent=unappliedText;$('recording-excluded').hidden=!unappliedText;
    markEnd(a,'voice');
  }
  const text=a.fragments.join(' ');
  a.apiText=text;
  $('quick-interim').textContent=a.limited?`${a.maxSeconds}초가 지나 자동으로 마쳤습니다. 이어서 말하려면 다시 누르세요.`:a.stopCommand?'명령을 제외하고 앞의 내용만 정리합니다.':'말하기를 마쳤습니다. 이어서 말하려면 다시 누르세요.';
  if(text.length>RECORDING_LIMITS.text){pauseRecommendation(a);unsentVoice=a.unsentRecovered?'':text;persistRecovery();a.apiText='';addUnsentHistory(a,'4000자를 넘어 전송하지 않았습니다. 원문을 복사해 나누어 입력하세요.');heard('');state('4000자 초과 · 전송 안 함');error('최종 본문은 4000자까지입니다. 인식 원문을 기록에 남겼습니다. 내용을 나누어 다시 말하세요.');controls();return;}
  if(!text.trim()&&a.trigger){a.apiText='';addUnsentHistory(a,a.trigger.action==='stop'?'종료 명령만 있어 API에 보내지 않았습니다.':a.trigger.action==='recommend'?'추천 명령만 있어 새 본문을 API에 보내지 않았습니다.':'화면 명령만 있어 본문을 API에 보내지 않았습니다.');heard('');state('말하기 종료');$('quick-interim').textContent='명령만 인식했습니다. 환자 본문과 새 초안을 만들지 않았습니다.';
    unsentVoice='';if(a.trigger.action!=='stop')afterTrigger(a);persistRecovery();controls();await performPatientAction(a);return;}
  if(!text.trim()){heard('');state('인식문 없음');error('최종 인식문이 없습니다. 마이크와 브라우저 음성 인식 연결을 확인한 뒤 다시 말해 주세요.');controls();return;}
  if(a.target!=='answer'){await send(a,()=>flow.apply(text,a.mode));return;}
  // A one-breath answer may use a lower-ranked recognizer hypothesis that fits the question.
  a.pick=chooseAnswer(a.question,!a.stopCommand&&a.fragments.length===1?a.alternatives[0]:[text]);
  a.apiText=a.pick.text;
  if(a.pick.rank>0)heard(a.pick.text);
  await send(a,()=>flow.answer(a.question,a.pick.text,{unavailable:a.pick.unavailable}));
}
const capture=new VoiceCapture({
  onInterim(text){if(capture.active&&!attempt?.stopCommand&&!attempt?.failed&&!attempt?.cancelled)$('quick-interim').textContent=text||'듣고 있습니다…';},
  onFinal(text,alternatives){
    const a=attempt;if(!a||a.cancelled||a.finished||a.failed||!capture.active)return;
    a.sourceFinals.push(text);
    if(a.target==='verification'){if(text.trim())a.fragments.push(text);unsentVoice=a.fragments.join(' ');persistRecovery();heard(a.sourceFinals.join(' '));return;}
    if(a.stopCommand){a.excludedFinals.push(text);keepUnapplied(text);heard(a.sourceFinals.join(' '));persistRecovery();return;}
    const combined=[...a.fragments,text].join(' ');
    const stop=findVoiceTrigger(combined);
    if(stop){a.fragments=stop.prefix.trim()?[stop.prefix]:[];a.alternatives=stop.prefix.trim()?[[stop.prefix]]:[];}
    else if(text.trim()){a.fragments.push(text);a.alternatives.push(alternatives?.length?alternatives:[text]);}
    heard(a.sourceFinals.join(' '));
    unsentVoice=a.fragments.join(' ');
    if(stop){a.trigger=stop;a.triggerSource=combined;a.triggerFinalCount=a.sourceFinals.length;a.stopCommand=stop.command;
      if(stop.suffix?.trim()){a.excludedFinals.push(stop.suffix);keepUnapplied(stop.suffix);}
      persistRecovery();press=null;void finish('voice');}
    else {persistRecovery();if(a.fragments.join(' ').length>=RECORDING_LIMITS.text){press=null;void finish('text_limit');}}
  },
  onState(next){
    if(next==='listening'&&attempt){attempt.listening=true;state(attempt.target==='answer'?'답변을 듣고 있습니다':'듣고 있습니다');buzz();timelineEnd();}
    if(next==='idle'){clearInterval(ticker);const ending=attempt;if(ending?.endReason){ending.endedAt=Date.now();markEnd(ending,ending.endReason);}queueMicrotask(()=>{if(ending)finalize(ending);});}
    controls();
  },
  onError(err){
    if(err.code==='tts-failed'){error('읽어주기를 완료하지 못했습니다. 저장된 초안은 유지됩니다. '+err.message);controls();return;}
    if(attempt){attempt.failed=true;pauseRecommendation(attempt);if(attempt.unsentRecovered)unsentVoice='';markEnd(attempt,'error');}persistRecovery();clearInterval(ticker);state('음성 연결 확인 필요');error(err.message);$('quick-interim').textContent='최종 인식문은 그대로 남겨두었습니다.';controls();
  }
});

// Start cue: vibration only. A beep would play while the microphone opens and could be transcribed.
function buzz() {const nav=globalThis.navigator;if(nav?.userActivation?.hasBeenActive!==false)nav?.vibrate?.(30);}
function tick(a) {
  if(attempt!==a||!capture.active)return;
  const seconds=Math.floor((performance.now()-a.started)/1000);
  $('quick-time').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
  if(seconds>=a.maxSeconds&&!stopping){a.limited=true;press=null;void finish('limit');}
}
async function record(target='transcript',held=false,verification=null) {
  if(busy||stopping||flow.job||capture.active||recovering||recoveryBlocked||criticalEditor)return;
  const question=target==='answer'?currentQuestion():null;
  if(target==='answer'&&!question)return;
  try{ensureActivePatient();}catch(err){error(err.message);return;}
  error('');capture.stopSpeaking();
  if(unsentVoice){keepUnapplied(unsentVoice);unsentVoice='';}
  const a={target,mode,question,verification,patientEpoch,held,maxSeconds:target==='verification'?10:RECORDING_LIMITS[recordingMode],fragments:[],sourceFinals:[],excludedFinals:[],unappliedBefore:unappliedText,alternatives:[],started:performance.now(),listening:false};attempt=a;
  $('recording-ended').hidden=true;
  // Unapplied text survives subsequent recordings until explicitly reviewed.
  heard('');$('quick-interim').textContent='마이크 연결을 기다리고 있습니다…';$('quick-time').textContent='00:00';state('마이크 준비 중');
  // While answering, bias toward every option of that question (never a specific number).
  try{
    phraseCount=capture.setPhrases(target==='verification'?['맞아요','아니요']:question?[...hintPlan.applied,...answerVocabulary(question)]:hintPlan.applied).length;
    const starting=capture.start('browser',question?{alternatives:5}:undefined);controls();
    await starting;if(attempt!==a||a.cancelled||!capture.active)return;
    a.started=performance.now();ticker=setInterval(()=>tick(a),500);
    $('quick-interim').textContent=!held?'이어서 말하세요. 마치면 버튼을 한 번 더 누르세요.':target==='answer'?'질문에 대한 답을 말하고 손을 떼세요.':'누른 채로 이어서 말하고, 다 말하면 손을 떼세요.';
    hintStatus();
  }catch(err){if(a.cancelled)return;a.failed=true;pauseRecommendation(a);markEnd(a,'error');releaseCapture();clearInterval(ticker);error(err.message);state('음성 연결 확인 필요');}
  finally{controls();}
}
function releaseCapture() {try{capture.abort();}catch(err){error('마이크 연결 해제 오류: '+err.message);}}
async function finish(reason='button') {
  if(!capture.active||stopping)return;
  if(!attempt?.listening){cancel(attempt?.held?'마이크가 준비되기 전에 손을 뗐습니다. 버튼을 누른 채로 잠시 기다린 뒤 말하세요.':undefined);return;}
  const a=attempt;markEnd(a,reason);
  state('말하기 종료 중');stopping=true;controls();
  try{await capture.stop();if(capture.active)throw new Error('음성 인식이 종료되지 않아 연결을 해제했습니다.');}
  catch(err){a.failed=true;pauseRecommendation(a);releaseCapture();markEnd(a,'error');clearInterval(ticker);state('음성 연결 확인 필요');error(err.message);$('quick-interim').textContent='최종 인식문은 그대로 남겨두었습니다.';}
  finally{stopping=false;controls();}
  await finalize(a);
}
function cancel(message='이번 말하기는 초안에 반영하지 않았습니다.'){press=null;if(attempt){attempt.cancelled=true;markEnd(attempt,'cancel');}unsentVoice='';releaseCapture();clearInterval(ticker);heard('');state('취소됨');$('quick-interim').textContent=message;persistRecovery();controls();}
// Hold remains selectable. Pointer/keyboard gestures each consume the trailing click.
function pressStart(target) {
  if(press||busy||stopping||flow.job||capture.active||$(target==='answer'?'followup-record':'quick-record').disabled)return;
  press={target,at:performance.now()};
  return record(target,true).catch(err=>{error(err.message);controls();});
}
function pressEnd(target) {
  if(!press||press.target!==target)return;
  const held=performance.now()-press.at;press=null;
  if(held<HOLD_MS){if(capture.active)cancel('짧게 눌렀습니다. 버튼을 누른 채로 말하고, 다 말한 뒤 손을 떼세요.');return;}
  return finish('release');
}
function toggle(target) {
  if(busy||stopping||flow.job)return;
  if(capture.active)return attempt?.target===target?finish().catch(err=>{error(err.message);controls();}):undefined;
  return record(target).catch(err=>{error(err.message);controls();});
}
const pttKeys=new Set([' ','Spacebar','Enter']);
const resetGestures=[];
function bindPtt(id,target) {
  const button=$(id);let gesture=null, suppressUntil=-1;
  const suppress=()=>{suppressUntil=performance.now()+1000;};
  button.onpointerdown=event=>{
    if(event.button>0||event.isPrimary===false||gesture||button.disabled)return;
    gesture={type:'pointer',id:event.pointerId};suppress();
    try{button.setPointerCapture?.(event.pointerId);}catch{/* Capture is optional. */}
    return recordingMode==='hold'?pressStart(target):toggle(target);
  };
  button.onpointerup=event=>{
    if(gesture?.type!=='pointer'||(event?.pointerId!==undefined&&gesture.id!==event.pointerId))return;
    gesture=null;suppress();return recordingMode==='hold'?pressEnd(target):undefined;
  };
  button.onpointercancel=()=>{if(gesture?.type!=='pointer')return;gesture=null;suppress();if(recordingMode==='hold')cancel();};
  button.onkeydown=event=>{
    if(!pttKeys.has(event.key))return;event.preventDefault();
    if(event.repeat||gesture||button.disabled)return;
    gesture={type:'key',key:event.key};suppress();return recordingMode==='hold'?pressStart(target):toggle(target);
  };
  button.onkeyup=event=>{
    if(!pttKeys.has(event.key))return;event.preventDefault();
    if(gesture?.type!=='key'||gesture.key!==event.key)return;
    gesture=null;suppress();return recordingMode==='hold'?pressEnd(target):undefined;
  };
  button.oncontextmenu=event=>event.preventDefault();
  const resetGesture=()=>{const hadGesture=!!gesture;gesture=null;suppress();if(hadGesture&&recordingMode==='hold'&&capture.active&&attempt?.target===target&&!stopping)cancel('버튼에서 포커스가 벗어나 이번 말하기를 취소했습니다.');};
  button.onblur=resetGesture;
  button.onlostpointercapture=()=>{if(gesture?.type==='pointer')resetGesture();};
  resetGestures.push(resetGesture);
  button.onclick=()=>{if(performance.now()<=suppressUntil){suppressUntil=-1;return;}if(button.disabled)return;return toggle(target);};
}
// Tapped answers (option chips, "미측정 · 모름") go through the same answer API as spoken ones.
function tapAnswer(text,options={}) {
  const q=currentQuestion();if(!q||locked())return;
  error('');capture.stopSpeaking();
  const a={target:'answer',question:q,button:true,fragments:[text],finished:true};attempt=a;
  return send(a,()=>flow.answer(q,text,options));
}
function togglePanel(buttonId,panelId,openText,closedText) {
  const panel=$(panelId), open=panel.hidden;panel.hidden=!open;
  $(buttonId).setAttribute('aria-expanded',String(open));
  if(openText)$(buttonId).textContent=open?openText:closedText;
}
bindPtt('quick-record','transcript');
bindPtt('followup-record','answer');
window.addEventListener('blur',()=>{for(const reset of resetGestures)reset();});
for(const next of ['toggle','hold'])$('recording-'+next).onclick=()=>{if(locked())return;recordingMode=next;press=null;$('quick-interim').textContent=idleHint();controls();};
$('recording-excluded-copy').onclick=()=>{if(locked())return;manualCopiedSource=$('recording-excluded-text').textContent;$('recording-review').value=manualCopiedSource;$('settings').hidden=false;$('settings-toggle').setAttribute('aria-expanded','true');$('recording-manual').open=true;$('recording-review').focus?.();$('recording-review-status').textContent='직접 입력 검토칸으로 가져왔습니다. 검토한 뒤 반영 버튼을 누르세요. 자동 전송하지 않았습니다.';persistRecovery();controls();};
$('recording-review').oninput=()=>{if(!$('recording-review').value.trim())manualCopiedSource='';persistRecovery();controls();};
$('critical-read').onclick=()=>{if(!locked())readCritical(criticalItems());};
$('critical-save').onclick=()=>saveCriticalEditor();
$('critical-cancel').onclick=()=>{if(locked())return;criticalEditor=null;persistRecovery();controls();};
$('critical-autoread').onchange=()=>{if($('critical-autoread').checked&&!locked())readCritical(criticalPending());};
$('recovery-retry').onclick=()=>resumeRecovery();
$('recommendation-continue').onclick=()=>{
  if(locked()||criticalEditor||!recommendationReady||!client.session||criticalPending().length)return;
  try{
    writeDraftHandoff(sessionStorage,{serverOrigin:client.server,actorId:actor,sessionId:client.session.id,
      version:client.session.version,unappliedText:handoffText(),intent:'recommend'});
    location.assign('./?voice_handoff=1');
  }catch(err){error('초안을 이어갈 준비를 완료하지 못했습니다. 이 화면의 기록은 유지됩니다. '+err.message);}
};
$('recording-review-apply').onclick=()=>{
  if(locked()||criticalEditor)return;
  const text=$('recording-review').value.trim();
  if(!text||text.length>RECORDING_LIMITS.text){error('직접 입력 본문은 1~4000자여야 합니다.');controls();return;}
  if(text===manualAppliedText)return;
  const a={manual:true,target:'transcript',mode:'dictation',fragments:[text],sourceFinals:[text],apiText:text,copiedFrom:manualCopiedSource,finished:true,endReason:'manual',endedAt:Date.now()};
  attempt=a;error('');capture.stopSpeaking();
  return send(a,()=>flow.apply(text,'dictation'));
};
$('quick-cancel').onclick=()=>cancel();
$('settings-toggle').onclick=()=>togglePanel('settings-toggle','settings');
$('summary-toggle').onclick=()=>togglePanel('summary-toggle','summary-detail','접기','전체 보기 ›');
for(const next of ['auto','dictation','command'])$('mode-'+next).onclick=()=>{if(locked()||(next==='auto'&&!autoAvailable))return;mode=next;$('mode-hint').textContent=modeHints[next];controls();};
$('quick-new').onclick=()=>newPatient();
$('patient-new').onclick=()=>newPatient();
$('patient-previous').onclick=()=>previousPatient();
$('patient-switch').onclick=()=>switchPatient($('patient-select').value);
$('patient-remove').onclick=()=>removeCurrentPatient();
$('patient-reset-invalid').onclick=()=>resetInvalidPatientStore();
$('quick-readback').onclick=()=>say(client.session?.speak_text);
$('followup-speak').onclick=()=>{const q=currentQuestion();if(!q||locked())return;lastSpoken=q.id+'|'+q.question;say(q.question);};
$('followup-deferred-retry').onclick=()=>{if(locked()||!deferred.size)return;deferred.clear();render();const q=currentQuestion();if(q&&$('followup-autoread').checked){lastSpoken=q.id+'|'+q.question;say(q.question);}};
$('followup-unavailable').onclick=()=>tapAnswer('미측정',{unavailable:true});
$('quick-retry').onclick=async()=>{if(busy||stopping||capture.active)return;const a=attempt;busy=true;error('');controls();try{const result=await flow.retry();if(a)settle(a,result);else render();$('quick-status').textContent='같은 요청으로 저장 결과를 확인했습니다.';}catch(err){await failure(err);}finally{busy=false;controls();}await performPatientAction(a);};
window.addEventListener('pagehide',()=>{persistRecovery();press=null;if(attempt)attempt.cancelled=true;releaseCapture();capture.stopSpeaking();clearInterval(ticker);});
window.addEventListener('storage',event=>{if(event.key!==PATIENT_RECORDS_KEY||event.newValue===patientBookRaw)return;
  recoveryBlocked=true;recoveryConflict=true;if(attempt)attempt.failed=true;releaseCapture();
  recoveryMessage('다른 탭에서 복구 기록이 변경되어 이 탭의 저장을 멈췄습니다. 현재 원문을 복사하거나 최신 탭에서 계속하세요.');controls();});
function hintStatus() {
  const count=phraseCount, excluded=hintPlan.excluded.length;
  const text={unsupported:'문맥 힌트: 이 브라우저는 지원하지 않아 일반 인식을 사용합니다.',pending:`문맥 힌트 ${count}개를 다음 말하기부터 브라우저 인식에 요청합니다.`,applied:`문맥 힌트 ${count}개를 브라우저 인식에 전달했습니다. 정확도 개선 여부는 측정하지 않았습니다.`,rejected:'문맥 힌트: 브라우저가 지원하지 않아 일반 인식으로 다시 시작했습니다.'}[capture.phraseStatus]||'';
  $('hint-status').textContent=text&&excluded?`${text} 값이 들어간 표현 ${excluded}개(정상·없음·숫자 등)는 정상 소견 쪽으로 끌릴 수 있어 제외했습니다.`:text;
}
function serverChip(text,tone) {$('server-chip').textContent=text;$('server-chip').className='qv-server'+(tone?' is-'+tone:'');}
async function checkServer() {
  let caps;
  try{caps=await client.api('/api/voice/capabilities');}
  catch{$('server-status').textContent='서버 음성 기능 정보를 읽지 못했습니다. 서버 실행 상태를 확인하세요.';serverChip('서버 연결 안 됨','bad');return;}
  autoAvailable=(Array.isArray(caps.transcript_modes)?caps.transcript_modes:[]).includes('auto');
  if(!autoAvailable&&mode==='auto'&&!locked()){mode='dictation';$('mode-hint').textContent=modeHints.dictation;}
  const current=caps.voice_revision===VOICE_REVISION;
  $('server-status').textContent=current?`서버 음성 기능 ${VOICE_REVISION} 확인. 자동 판단, 확인 질문, 변경 전후 표시를 사용합니다.`:`서버 음성 기능 버전이 다릅니다(${caps.voice_revision||'표시 없음'}). 이전 코드가 실행 중이면 자동 판단·확인 질문·변경 표시가 동작하지 않을 수 있습니다.`;
  $('server-status').className=current?'':'server-mismatch';
  serverChip(current?'음성 V2':'버전 다름',current?'ok':'warn');
  let terms={};
  try{terms=await client.api('/api/voice/terms');}catch{/* Hints are optional; plain recognition still works. */}
  const all=[...new Set([...(caps.speech_context_hints||[]),...(terms.speech_recognition_hints||[])].filter(h=>typeof h==='string').map(h=>h.trim()).filter(Boolean))];
  hintPlan={applied:all.filter(h=>!VALUE_HINT.test(h)),excluded:all.filter(h=>VALUE_HINT.test(h))};
  phraseCount=capture.setPhrases(hintPlan.applied).length;hintStatus();controls();
}
if(!VoiceCapture.capabilities().browser){state('지원 브라우저 필요');error('이 브라우저는 실시간 음성 인식을 지원하지 않습니다. 지원 브라우저에서 열어 주세요.');}
render();
resumeRecovery();
checkServer();
