import {VoiceClient, requestId, normalizeServer} from './voice-client.mjs';
import {VoiceCapture} from './speech.mjs';
import {captureDescription,localAudioTestMetadata} from './speech-test-context.mjs';
import {readDraftHandoff,clearDraftHandoff} from './draft-handoff.mjs';
import {MainFollowup,patientNavigation} from './main-followup.mjs';
import {MainTreatmentReview} from './main-treatment-review.mjs';
import {MainRecovery,findMainRecovery,emptyMainInput} from './main-recovery.mjs';
import {initStagedReview,canReturnToConversation} from './staged-review.mjs';

const $ = id => document.getElementById(id);
const labels = {age:'나이',age_group:'연령대',sex:'성별',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상',consciousness:'의식 상태',breathing:'호흡 상태',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',onset_at:'증상 발생 시각',blood_pressure:'혈압'};
const enums = {sex:{male:'남성',female:'여성',unknown:'미확인'},consciousness:{alert:'명료',impaired:'저하',unresponsive:'반응 없음',unknown:'미확인'},breathing:{normal:'정상',abnormal:'비정상',absent:'호흡 없음',unknown:'미확인'}};
const listFields = new Set(['symptoms','denied_symptoms','uncertain_symptoms']);
Object.assign(labels,{avpu:'의식 AVPU',chief_complaint:'주증상 분류',age_category:'연령 구분',pre_ktas:'Pre-KTAS',stemi_suspected:'심전도 ST 상승',injury_region:'손상 부위',labor:'진통·양막 파열',pregnancy_weeks:'임신 주수',seizure_ongoing:'경련 지속',bleeding_risk:'출혈 경향',high_risk_mechanism:'고위험 손상 기전',pain_score:'통증 점수',gcs:'GCS'});
Object.assign(enums,{avpu:{A:'A 명료',V:'V 목소리 반응',P:'P 통증 반응',U:'U 무반응',unknown:'미확인'},chief_complaint:{cardiac:'흉통·심장',stroke:'뇌졸중 의심',altered_seizure:'의식저하·경련',dyspnea:'호흡곤란',trauma:'외상',bleeding:'출혈',abdominal:'복통·소화기',obstetric:'산과·분만',toxic_env:'중독·환경',other:'기타'},age_category:{adult:'성인',child:'소아',neonate:'신생아',pregnant:'임신 중',unknown:'미확인'},pre_ktas:{1:'1',2:'2',3:'3',4:'4',5:'5',unclassified:'미분류'},stemi_suspected:{yes:'확인됨',no:'없음',unmeasured:'미측정'},injury_region:{head:'머리',torso:'몸통',limb:'팔다리',multiple:'여러 곳'},labor:{yes:'있음',no:'없음'},seizure_ongoing:{yes:'지속 중',no:'멈춤'},bleeding_risk:{yes:'있음',no:'없음'},high_risk_mechanism:{yes:'있음',no:'없음'}});
const storage = {get(key){try{return sessionStorage.getItem('paramedic.voice.'+key);}catch(_){return null;}},set(key,value){try{sessionStorage.setItem('paramedic.voice.'+key,value);}catch(_){}}};
const previousIdentity={actor:storage.get('actor'),session:storage.get('session'),server:storage.get('server')};
const params = new URLSearchParams(location.search);
const arrivedFromConversation=params.get('voice_handoff')==='1';
const actorFromQuery=params.get('actor_id');
const actorQueryValid=!actorFromQuery||(/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/.test(actorFromQuery)&&actorFromQuery.length<=80);
let actor = (actorQueryValid&&actorFromQuery) || storage.get('actor') || 'crew-' + requestId();
let handoffPending=params.get('voice_handoff')==='1', handoffFailed=false, recommendationReady=false, handoffUnapplied=false, recommendationAwaitingSubmit=false;
if(!handoffPending)storage.set('actor', actor);
let client, capabilities, catalog, currentPatient = null, tasks = 0, dirty = false, connecting = false, gpsBusy = false;
let captureMode = 'dictation', captureProvider = 'browser', finalCount = 0, startedAt = 0, ticker;
let canRead = VoiceCapture.capabilities().tts;
let localAudioUrl=null,localAudioMetadata=null;
let mainFollowup=null,treatmentReview=null,captureQuestion=null,lastQuestionSpoken='';
let recovery=null,recoveryError='',recoveryFieldConflict=false,recoveryCopy=null;
const stagedReview=initStagedReview({document,onBackToConversation(){
  if(blocked()||dirty||handoffUnapplied||handoffPending||handoffFailed||!canReturnToConversationNow())return false;
  const url=new URL('./quick-test.html',location.href);url.searchParams.set('flow','field');
  location.assign(url.href);return true;
}});
function canReturnToConversationNow(){
  try{return canReturnToConversation({fromVoiceHandoff:arrivedFromConversation,session:client?.session,actorId:actor,serverOrigin:client?.server,pageOrigin:location.origin,tabStorage:sessionStorage,patientStorage:localStorage});}
  catch{return false;}
}

const node = (tag, text, className) => {const result=document.createElement(tag);if(text!==undefined)result.textContent=text;if(className)result.className=className;return result;};
function message(text, isError=false) {const target=$(isError?'error':'notice');target.textContent=text;target.hidden=!text;if(isError)$('notice').hidden=true;}
function clearError(){ $('error').hidden=true; $('error').textContent=''; }
function recoveryInput(){
  const fields={};if(dirty)for(const input of $('fields-editor').querySelectorAll('[data-field]'))fields[input.dataset.field]=input.value;
  return {transcript:$('transcript-input').value,command:$('command-input').value,answer:$('main-answer').value,mode:$('voice-mode').value,changeKind:$('change-kind').value,fields,dirty,baseVersion:client?.session?.version||0,lat:$('lat').value,lon:$('lon').value,requiredCapabilities:[...$('required-capabilities').selectedOptions].map(o=>o.value),consent:$('consent').checked};
}
function recoveryStatus(){
  $('main-recovery-status').textContent=recoveryError|| (recoveryFieldConflict?'서버 버전이 변경되어 저장한 항목 편집을 자동 적용하지 않았습니다. 아래 원본을 확인하세요.':client?.pending?'처리 여부 미확인 요청을 보존했습니다. 서버 최신 기록과 별개로 같은 요청 다시 시도를 명시적으로 누르세요.':recovery?.record.sessionId?'서버에서 저장한 초안을 조회했습니다. 로컬 입력창은 이미 반영된 원문도 포함할 수 있으며 자동 전송하지 않습니다.':'새 기록 시작 또는 환자 불러오기를 명시적으로 선택하세요. 자동 등록·전송은 하지 않습니다.');
  $('main-recovery-save').hidden=!recoveryError||!recovery;
  $('main-recovery-copy').hidden=!recoveryCopy;
  $('main-recovery-copy').value=recoveryCopy?JSON.stringify(recoveryCopy,null,2):'';
  $('main-recovery-discard-edits').hidden=!recoveryFieldConflict;
}
function saveRecovery(outcome){
  if(!recovery)return;
  try{recovery.update({session:client?.session,ui:recoveryFieldConflict?recovery.record.ui:recoveryInput(),pending:client?.pending||null,outcome:outcome||(client?.pending?'pending':recovery.record.outcome)});recoveryError='';}
  catch(error){recoveryError=error.message;recoveryStatus();throw error;}
  recoveryStatus();
}
function saveInput(){try{saveRecovery();}catch(error){message(error.message,true);}controls();}
function attachRecovery(next,selected=null,patientId=null){
  const store=handoffStorage();
  const manager=new MainRecovery({storage:store,pageOrigin:location.origin,serverOrigin:next.server,actorId:next.actorId,patientId,selected});
  next.beforeWrite=request=>{
    if(recovery!==manager||client!==next||next.server!==normalizeServer($('server-url').value.trim())||next.actorId!==actor||(recoveryFieldConflict&&JSON.stringify(request)!==JSON.stringify(manager.record.pending)))throw Error('현재 화면과 저장한 요청의 범위를 먼저 확인하세요.');
    persistRestoredIdentity(next.actorId,next.session?.id||'',next.server);
    manager.beforeWrite(request,{session:next.session,ui:recoveryFieldConflict?manager.record.ui:recoveryInput()});
  };
  recovery=manager;recoveryError='';return manager;
}
function restoreRecoveryInput(ui){
  for(const [id,key]of [['transcript-input','transcript'],['command-input','command'],['main-answer','answer'],['voice-mode','mode'],['change-kind','changeKind'],['lat','lat'],['lon','lon']])$(id).value=ui[key];
  for(const option of $('required-capabilities').options)option.selected=ui.requiredCapabilities.includes(option.value);
  $('consent').checked=ui.consent;
  if(ui.dirty){
    if(ui.baseVersion!==client?.session?.version){recoveryFieldConflict=true;recoveryCopy=ui;}
    else{for(const input of $('fields-editor').querySelectorAll('[data-field]'))if(Object.hasOwn(ui.fields,input.dataset.field))input.value=ui.fields[input.dataset.field];dirty=true;}
  }
  recoveryStatus();controls();
}
function remember(){if(client?.session&&!handoffPending&&!handoffFailed){storage.set('actor',actor);storage.set('session',client.session.id);storage.set('server',client.server);}}
function handoffStorage(){try{return sessionStorage;}catch(_){return null;}}
function persistRestoredIdentity(nextActor,nextSession,nextServer){
  const target=handoffStorage(),values={'paramedic.voice.actor':nextActor,'paramedic.voice.session':nextSession,'paramedic.voice.server':nextServer},before={};
  try{
    if(!target)throw Error('Storage unavailable');
    for(const key of Object.keys(values))before[key]=target.getItem(key);
    for(const [key,value]of Object.entries(values))target.setItem(key,value);
    if(Object.entries(values).some(([key,value])=>target.getItem(key)!==value))throw Error('Storage verification failed');
  }catch(_){
    for(const [key,value]of Object.entries(before)){try{if(value===null)target.removeItem(key);else target.setItem(key,value);}catch(_){}}
    throw Error('복원한 기록의 연결 정보를 저장하지 못했습니다. 원래 음성 초안 연결은 유지했습니다. 저장 권한을 확인한 뒤 다시 시도하세요.');
  }
}
function removeHandoffParameter({newPatient=false}={}){
  const query=new URLSearchParams(location.search);query.delete('voice_handoff');params.delete('voice_handoff');
  if(newPatient){for(const key of ['pid','entry']){query.delete(key);params.delete(key);}$('resume-patient').value='';}
  window.history.replaceState(null,'',location.pathname+(query.size?'?'+query.toString():'')+(location.hash||''));
}
function selectPatientParameter(patientId){
  const query=new URLSearchParams(location.search);query.set('pid',patientId);params.set('pid',patientId);
  window.history.replaceState(null,'',location.pathname+'?'+query.toString()+(location.hash||''));$('resume-patient').value=patientId;
}
function renderRecommendation(){
  $('recommendation-prep').hidden=!handoffPending&&!handoffFailed&&!recommendationReady;
  $('recommendation-prep').dataset.state=handoffFailed?'error':recommendationReady?'ready':'loading';
  $('recommendation-title').textContent=handoffFailed?'음성 초안을 불러오지 못했습니다':recommendationReady?'저장한 초안으로 병원 추천 준비':'저장한 음성 초안 확인 중';
  if(!handoffFailed)$('recommendation-status').textContent=recommendationReady?'초안을 검토하고 기록 확인을 완료한 뒤, 위치·동의를 확인해 환자를 명시적으로 등록하세요. 등록 후 기존 병원 후보 화면에서 계속합니다.':'서버에 저장된 동일 초안과 버전을 확인합니다. 새 환자를 자동으로 등록하거나 문의하지 않습니다.';
  $('handoff-new-case').hidden=!handoffFailed;
  $('handoff-pending-note').hidden=!handoffUnapplied;
  $('discard-handoff-text').hidden=!handoffUnapplied;
}
function draft(){return client?.session?.state==='DRAFT';}
function linkedWaiting(){return !!client?.session?.patient_id&&currentPatient?.state==='WAITING';}
function ensureModels(){if(client&&mainFollowup?.client!==client){mainFollowup=new MainFollowup(client);treatmentReview=new MainTreatmentReview(client);lastQuestionSpoken='';}}
function invalidateTreatment(){if(treatmentReview?.review){treatmentReview.invalidate();$('treatment-detail').textContent='초안이나 선택이 바뀌었습니다. 변경안을 다시 조회하세요.';}$('treatment-confirm').checked=false;$('treatment-reduction').checked=false;}
function blocked(){return tasks>0 || connecting || gpsBusy || capture.active || !!client?.pending || !!recoveryError || recoveryFieldConflict;}
function allowedSubmit(){
  if(!client?.session?.confirmed || !draft() || blocked() || dirty || handoffUnapplied)return false;
  if(client.session.patient_id)return false;
  return $('consent').checked && $('lat').value.trim()!=='' && $('lon').value.trim()!=='' &&
    Number.isFinite(Number($('lat').value)) && Math.abs(Number($('lat').value))<=90 &&
    Number.isFinite(Number($('lon').value)) && Math.abs(Number($('lon').value))<=180;
}
function controls(){
  const locked=blocked(), supported=VoiceCapture.capabilities();
  for(const id of ['connect-server','new-case','load-patient','sample-button'])$(id).disabled=locked||dirty;
  $('new-case').disabled=tasks>0||connecting||gpsBusy||capture.active||!!client?.pending||dirty||recoveryFieldConflict;
  $('connect-server').disabled=tasks>0||connecting||capture.active||!!client?.pending||dirty||handoffUnapplied;
  $('load-patient').disabled=tasks>0||connecting||gpsBusy||capture.active||!!client?.pending||dirty||recoveryFieldConflict||handoffPending||handoffFailed;
  $('sample-button').disabled=locked||dirty||handoffUnapplied;
  $('handoff-new-case').disabled=$('new-case').disabled;
  $('discard-handoff-text').disabled=locked||!handoffUnapplied;
  $('server-url').disabled=locked; $('resume-patient').disabled=locked;
  for(const id of ['send-transcript','send-command','voice-mode','speech-provider'])$(id).disabled=locked||!draft()||dirty;
  if(handoffUnapplied)$('send-command').disabled=true;
  $('audio-input-profile').disabled=locked||!draft()||dirty||$('speech-provider').value==='browser';
  $('audio-input-status').textContent=$('speech-provider').value==='browser'?captureDescription({route:'browser_managed'}):capture.inputDiagnostics?.route==='pcm_capture'?`${capture.active?'현재':'마지막'} ${captureDescription(capture.inputDiagnostics)}`:'다음 PCM 녹음에서 요청값과 기기가 보고한 실제 설정을 확인합니다.';
  const provider=$('speech-provider').value, available=provider==='browser'?supported.browser:supported.recording&&(provider==='local_wav'||capabilities?.speech?.configured);
  $('record-button').disabled = !capture.active && (locked||!draft()||dirty||!available||handoffUnapplied);
  $('record-button').textContent=capture.active?'녹음 마치기':provider==='local_wav'?'시험용 오디오 녹음 시작':'음성 입력 시작';
  $('record-button').classList.toggle('is-recording',capture.active);
  $('record-button').setAttribute('aria-pressed',String(capture.active));
  $('save-fields').disabled=locked||!draft()||!dirty;
  if($('discard-fields'))$('discard-fields').disabled=locked||!dirty;
  $('undo-button').disabled=locked||!draft()||!client?.session?.can_undo||dirty;
  $('confirm-button').disabled=locked||!draft()||!Object.keys(client?.session?.fields||{}).length||dirty||handoffUnapplied;
  $('submit-button').disabled=!allowedSubmit();
  $('submit-button').hidden=!!client?.session?.patient_id;
  $('readback-button').disabled=locked||!client?.session||dirty;
  $('readback-field').disabled=locked||!client?.session;
  $('refresh-session').disabled=tasks>0||capture.active||!client?.session||!!client?.pending||dirty;
  $('refresh-candidates').disabled=locked||!currentPatient||recommendationAwaitingSubmit;
  $('retry-request').hidden=!client?.pending;
  $('retry-request').disabled=tasks>0||capture.active;
  for(const id of ['transcript-input','command-input','main-answer'])$(id).disabled=locked||!client?.session;
  $('main-recovery-save').disabled=tasks>0||connecting||capture.active;
  recoveryStatus();
  for(const control of $('fields-editor').querySelectorAll('input,select,textarea'))control.disabled=locked||!draft();
  $('change-kind').disabled=locked||!draft();
  for(const id of ['lat','lon','gps-button','use-demo-location','required-capabilities','consent'])$(id).disabled=locked||!draft()||!!client?.session?.patient_id;
  const q=mainFollowup?.current();
  for(const id of ['main-answer','main-answer-send','main-answer-unavailable','main-question-defer'])$(id).disabled=locked||!draft()||dirty||handoffUnapplied||!q;
  $('main-answer-record').disabled=(!capture.active&&(locked||!draft()||dirty||handoffUnapplied||!q||!supported.browser))||(capture.active&&!captureQuestion);
  $('main-answer-record').textContent=capture.active&&captureQuestion?'답변 녹음 마치기':'음성으로 답하기';$('main-answer-record').setAttribute('aria-pressed',String(capture.active&&!!captureQuestion));
  $('main-question-speak').disabled=locked||!q||!canRead;
  $('main-question-retry').disabled=locked||!draft()||dirty;
  for(const button of $('main-question-options').children)button.disabled=locked||!draft()||dirty||handoffUnapplied;
  const canReview=linkedWaiting()&&draft()&&!locked&&!dirty&&!handoffUnapplied;
  for(const id of ['treatment-load','treatment-mode','treatment-preview','treatment-confirm','treatment-reduction','treatment-reason'])$(id).disabled=!canReview;
  const review=treatmentReview?.review;
  $('treatment-apply').disabled=!canReview||!review?.can_apply||!client?.session?.confirmed||!$('treatment-confirm').checked||((review?.reductions||[]).length>0&&!$('treatment-reduction').checked);
  renderRecommendation();
  stagedReview.update({session:client?.session,patient:recommendationAwaitingSubmit?null:currentPatient,pending:client?.pending,locked,dirty,unapplied:handoffUnapplied,handoffLoading:handoffPending,handoffFailed,recoveryError:!!recoveryError,recoveryConflict:recoveryFieldConflict,canReturnToConversation:canReturnToConversationNow()});
}
function renderFields(){
  const values=client?.session?.fields||{};
  $('fields-editor').replaceChildren();
  const optional=node('details',undefined,'additional-fields');
  const extraGrid=node('div',undefined,'field-grid');
  optional.append(node('summary','추가 항목 입력'),extraGrid);
  const core=new Set(['age','sex','symptoms','consciousness','breathing']);
  for(const key of new Set([...(catalog?.supported_fields||[]),...(capabilities?.r1_fields||[]),...Object.keys(values)])){
    const field=node('div',undefined,'field');
    const label=node('label',labels[key]||key);label.htmlFor='field-'+key;
    let input;
    if(enums[key]){
      input=node('select');input.add(new Option('기록 없음',''));
      for(const [value,name]of Object.entries(enums[key]))input.add(new Option(name,value));
    }else if(listFields.has(key)){input=node('textarea');input.rows=2;input.placeholder='표준 증상을 쉼표로 구분';}
    else{input=node('input');input.type=key==='age_group'?'text':key==='onset_at'?'datetime-local':'number';input.placeholder='미확인';input.step=['temperature','spo2','glucose'].includes(key)?'any':'1';}
    input.id='field-'+key;input.dataset.field=key;
    const value=values[key];
    input.value=value===undefined?'':Array.isArray(value)?value.join(', '):key==='onset_at'?new Date(value*1000-new Date(value*1000).getTimezoneOffset()*60000).toISOString().slice(0,19):String(value);
    input.addEventListener('input',()=>{dirty=true;invalidateTreatment();$('approval-status').textContent='수정 중 · 서버에 아직 반영되지 않았습니다';saveInput();});
    field.append(label,input);
    const spec=catalog?.terms?.measurements?.[key];
    if(spec)field.append(node('small',spec.unit));
    if(key==='onset_at')field.append(node('small','이 기기의 현지 시각 · 음성으로 “20분 전 발생”도 가능'));
    if(key==='age_group')field.append(node('small','예: 60대 · 정확한 나이로 추정하지 않음'));
    const evidence=client?.session?.field_metadata?.[key]?.evidence;
    if(evidence?.length){const detail=node('details');detail.append(node('summary','입력 근거'),node('p',evidence.join(' / ')));field.append(detail);}
    if(client?.session?.field_metadata?.[key]?.needs_clarification){const unmeasured=(client.session.followup?.items||[]).some(q=>q.answer_status==='unavailable'&&q.fields?.includes(key));field.append(node('small',unmeasured?'미측정·확인 불가 · 이전 값은 현재 확인값이 아닙니다.':'재확인 필요 · 이전 관찰값일 수 있습니다.','warning-note'));}
    if(core.has(key)||value!==undefined)$('fields-editor').append(field);
    else extraGrid.append(field);
  }
  if(extraGrid.children.length)$('fields-editor').append(optional);
  dirty=false;
}
function render(){
  const s=client?.session;
  ensureModels();
  if(treatmentReview?.review&&s&&(treatmentReview.review.session_id!==s.id||treatmentReview.review.session_version!==s.version))invalidateTreatment();
  mainFollowup?.observe(s);
  remember();renderFields();
  $('session-meta').textContent=s?`${s.patient_id?'환자 '+s.patient_id:'새 환자 초안'} · 버전 ${s.version}`:'새 기록을 준비하고 있습니다';
  $('approval-status').textContent=!s?'아직 기록 없음':s.state==='SUBMITTED'?'환자정보 반영 완료':s.confirmed?'현재 버전 확인 완료':'내용 확인이 필요합니다';
  const step=currentPatient&&!recommendationAwaitingSubmit?'hospital':s?.confirmed?'submit':Object.keys(s?.fields||{}).length?'review':'input';
  const steps={input:'01 음성으로 기록',review:'02 정보 확인·수정',submit:'03 확인 후 저장',hospital:'04 병원 후보 확인'};
  $('workflow-step').textContent=steps[step];
  if(!stagedReview.active)for(const el of document.querySelectorAll('[data-step]'))el.classList.toggle('is-current',el.dataset.step===step);
  $('field-warnings').replaceChildren();
  if(s?.baseline_summary?.symptoms)$('field-warnings').append(node('p','기존 기록 증상: '+s.baseline_summary.symptoms,'baseline-note'));
  for(const text of s?.warnings||[])$('field-warnings').append(node('p',text,'warning-note'));
  $('transcript-history').replaceChildren();
  for(const u of (s?.utterances||[]).slice(-6).reverse()){
    const item=node('div',undefined,'history-item');item.append(node('small',u.mode==='command'?'음성 명령':'최종 인식문'),node('p',u.transcript));$('transcript-history').append(item);
  }
  if(!s?.utterances?.length)$('transcript-history').append(node('p','최종 인식문이 여기에 남습니다.','empty-state'));
  renderFollowup();renderTreatment();renderPatient();controls();
}
function renderFollowup(){
  const s=client?.session,q=mainFollowup?.current(),plan=s?.followup;
  $('main-followup').hidden=!q;
  $('main-question-text').textContent=q?.question||'';$('main-question-hint').textContent=q?.answer_hint||'';
  $('main-question-progress').textContent=plan?`남은 ${plan.unanswered_count??'?'}개 · 검토 보류 ${mainFollowup?.deferred.size||0}개`:'';
  $('main-question-options').replaceChildren();
  const options={consciousness:['명료','저하','반응 없음'],breathing:['정상','비정상','없음'],sex:['남성','여성']};
  for(const text of options[q?.id]||[]){const button=node('button',text,'button button-secondary button-small');button.type='button';button.addEventListener('click',()=>answerMain(text).catch(()=>{}));$('main-question-options').append(button);}
  const unavailable=(plan?.items||[]).filter(item=>item.answer_status==='unavailable').length;
  $('main-followup-status').textContent=!plan?'':`${plan.complete?'질문 목록 기록 완료 · 원문과 비교해 확인하세요.':q?'현재 질문에 답하거나 검토 때 확인으로 남길 수 있습니다.':'지금 더 묻지 않습니다.'} 미측정·확인 불가 ${unavailable}개 · 보류 ${mainFollowup?.deferred.size||0}개. 빈 값을 0이나 정상으로 채우지 않습니다.`;
  $('main-question-retry').hidden=!mainFollowup?.deferred.size;
  $('main-change-items').replaceChildren();
  for(const change of mainFollowup?.changes||[]){const value=v=>v==null?'미확인':Array.isArray(v)?v.join(', '):String(v);$('main-change-items').append(node('li',`${labels[change.field]||change.field}: ${value(change.previous)} → ${value(change.current)}${change.direction?` · ${{increased:'증가',decreased:'감소',unchanged:'동일'}[change.direction]||change.direction}`:''}`));}
  $('main-changes').hidden=!$('main-change-items').children.length;
}
const treatmentLabels={...labels,treatment_profile_id:'치료 프로필',required_capabilities:'필요 진료',resource_demands:'필요 자원',explicit_required_capabilities:'직접 지정 진료',explicit_resource_demands:'직접 지정 자원',priority:'우선순위',priority_source:'우선순위 출처'};
const treatmentResourceLabels={ed_bed:'진료 자리',special_team:'전문팀'};
const treatmentCodeLabels={VOICE_SESSION_SUBMITTED:'이미 반영한 초안',INVALID_STATE:'현재 환자 상태에서는 적용 불가',VOICE_PATIENT_VERSION_CONFLICT:'환자 정보 버전 변경 · 다시 조회 필요',VOICE_CONFIRMATION_REQUIRED:'현재 음성 초안 확인 필요',TREATMENT_SOURCE_NEEDS_CLARIFICATION:'제안 근거 입력 재확인 필요',LEGACY_EXPLICIT_CONDITIONS_REQUIRE_REVIEW:'기존 직접 지정 조건 재확인 필요',UNSUPPORTED_SOLVER_RESOURCE:'현재 계산 모델 지원 밖 자원 있음'};
const treatmentField=key=>treatmentLabels[key]?`${treatmentLabels[key]} (${key})`:key;
const treatmentResource=key=>treatmentResourceLabels[key]?`${treatmentResourceLabels[key]} (${key})`:key;
function treatmentValue(field,value){
  if(value==null)return field==='treatment_profile_id'?'없음':'미확인·미등록';
  if(Array.isArray(value))return value.length?value.map(v=>treatmentValue(field,v)).join(' · '):'없음';
  if(typeof value==='object')return Object.keys(value).length?Object.entries(value).map(([key,qty])=>`${field.endsWith('resource_demands')?treatmentResource(key):treatmentField(key)}: ${treatmentValue(key,qty)}`).join(' · '):'없음';
  const text=enums[field]?.[value]||(field==='priority_source'?{expert:'전문가 확인',self:'자기신고',unknown:'미확인'}[value]:null);
  return text&&text!==String(value)?`${text} (${value})`:String(value);
}
function conditionsText(value){return value?Object.entries(value).map(([key,v])=>`${treatmentField(key)}: ${treatmentValue(key,v)}`).join('\n'):'미확인';}
function treatmentChanges(rows,{source=false}={}){
  return rows?.length?rows.map(row=>`${treatmentField(row.field)}: ${treatmentValue(row.field,row.previous)} → ${treatmentValue(row.field,row.current)}${source?` · 삭제 ${row.removed?'예':'아니오'} · 재확인 필요 ${row.needs_clarification?'예':'아니오'}`:''}`).join('\n'):'없음';
}
function treatmentReductions(rows){
  return rows?.length?rows.map(row=>{
    if(Array.isArray(row.removed))return `${treatmentField(row.field)}에서 제외: ${treatmentValue(row.field,row.removed)}`;
    if(row.reduced)return Object.entries(row.reduced).map(([key,v])=>`${treatmentResource(key)} 감소: ${treatmentValue(key,v.previous)} → ${treatmentValue(key,v.current)}`).join('\n');
    return `${treatmentField(row.field)} 하향: ${treatmentValue(row.field,row.previous)} → ${treatmentValue(row.field,row.current)}`;
  }).join('\n'):'없음';
}
function formatTreatment(r){
  const codes=values=>values?.length?values.map(code=>treatmentCodeLabels[code]?`${treatmentCodeLabels[code]} (${code})`:code).join('\n'):'없음';
  return `현재 조건\n${conditionsText(r.current)}\n\n검토할 조건\n${conditionsText(r.proposed)}\n\n변경 전후\n${treatmentChanges(r.changes)}\n\n근거 입력 변화\n${treatmentChanges(r.source_changes,{source:true})}\n\n감소·제거\n${treatmentReductions(r.reductions)}\n\n계산 지원 외 자원\n${r.unsupported_solver_resources?.length?r.unsupported_solver_resources.map(treatmentResource).join(' · '):'없음'}\n\n적용 제한\n${codes(r.blockers)}\n\n추가 확인 안내\n${codes(r.warnings)}`;
}
function renderTreatment(){
  const visible=linkedWaiting()&&draft();$('main-treatment').hidden=!visible;
  if(!visible)return;
  const r=treatmentReview?.review;
  $('treatment-reduction-label').hidden=!r?.reductions?.length;
  if(r){$('treatment-mode').value=r.selection.mode;$('treatment-confirm').checked=false;$('treatment-reduction').checked=false;$('treatment-detail').textContent=formatTreatment(r);$('treatment-status').textContent=`초안 v${r.session_version} · 환자 v${r.patient_version} · ${r.can_apply?'적용 전 별도 확인 필요':'초안 확인 또는 제한 해소 후 다시 조회'}${treatmentReview.notice?' · '+treatmentReview.notice:''}`;}
  else if(client.session.treatment_review?.review_required)$('treatment-status').textContent='새 입력이 치료조건에 영향을 줄 수 있습니다. 변경안을 조회하고 명시적으로 확인하세요.';
}
async function speakNextQuestion(){
  const q=mainFollowup?.current();if(!q||!canRead||capture.active||!$('main-question-autoread').checked)return;
  const key=`${client.session.id}:${q.id}:${q.question}`;if(lastQuestionSpoken===key)return;
  lastQuestionSpoken=key;capture.speak(q.question);
}
async function answerMain(text,{unavailable=false,question=mainFollowup?.current()}={}){
  return task(async()=>{if(dirty||handoffUnapplied)throw Error('수정 중이거나 미반영인 문장을 먼저 확인하세요.');capture.stopSpeaking();const result=await mainFollowup.answer(question,text,{unavailable});$('main-answer').value='';message(result.answer?.accepted||result.answer?.unavailable?'질문 답변을 기록했습니다.':'답변 원문을 보존했습니다. 해석하지 못한 항목은 검토 보류로 남깁니다.');return result;}).then(async result=>{await speakNextQuestion();await history();return result;});
}
async function task(action){
  tasks++;controls();
  try{const result=await action();render();saveRecovery(client?.pending?'pending':'saved');return result;}
  catch(error){
    if(capture.active)capture.abort();
    if(error.status===409&&client?.session&&!client.pending){try{const before=recovery?.record.ui,mode=$('treatment-mode').value;invalidateTreatment();await client.sync();if(client.session.patient_id)currentPatient=await client.api('/api/patients/'+encodeURIComponent(client.session.patient_id));if(currentPatient?.state==='WAITING'&&draft())await treatmentReview.load(currentPatient,mode);render();if(before?.dirty&&before.baseVersion!==client.session.version){recoveryFieldConflict=true;recoveryCopy=before;}$('treatment-status').textContent='충돌 후 최신 초안·환자·변경안을 조회했습니다. 감소 항목을 포함해 다시 확인하세요.';}catch(_){}}
    try{saveRecovery(client?.pending?'pending':'terminal_error');}catch(_){}
    message(error.message||'처리하지 못했습니다.',true);throw error;
  }finally{tasks--;controls();}
}
function bindAsync(id,fn){$(id).addEventListener('click',()=>{clearError();Promise.resolve().then(fn).catch(()=>{});});}
function write(action,extra={},method='POST'){return task(()=>client.write(action,extra,method));}
async function history(){
  if(!client?.session)return;
  const owner=client,sid=client.session.id;
  const result=await owner.api(`/api/voice/sessions/${sid}/history?actor_id=${encodeURIComponent(actor)}`);
  if(client!==owner||client.session?.id!==sid)return;
  $('history-list').replaceChildren();
  for(const row of result.revisions.slice(-6).reverse()){
    const names=Object.keys({...row.before_fields,...row.after_fields}).filter(k=>JSON.stringify(row.before_fields[k])!==JSON.stringify(row.after_fields[k])).map(k=>labels[k]||k);
    $('history-list').append(node('div',`v${row.version} · ${{dictation:'음성 기록',correction:'정정',observation:'새 관찰',undo:'되돌리기'}[row.kind]||row.kind} · ${names.join(', ')||'원문 확인'}`,'history-item'));
  }
}
async function commandFeedback(result){
  if(!result.command)return;
  if(result.command.action==='submit'){message('내용을 확인한 뒤 저장 버튼을 눌러 주세요.');return;}
  if(!['review','edit','add','confirm','undo'].includes(result.command.action))return;
  let spoken=result.speak_text;
  if(!spoken){const latest=await client.readback(result.command.field||undefined);spoken=latest.speak_text;}
  $('readback-text').textContent=spoken;
  if(canRead&&!capture.active){try{capture.speak(spoken);}catch(error){message(error.message,true);}}
}
function resetCase(){
  clearLocalAudio();
  handoffUnapplied=false;recommendationAwaitingSubmit=false;
  currentPatient=null;dirty=false;$('candidate-list').replaceChildren(node('p','환자정보를 저장하면 후보 병원을 확인할 수 있습니다.','empty-state'));
  mainFollowup=null;treatmentReview=null;lastQuestionSpoken='';captureQuestion=null;invalidateTreatment();$('patient-navigation').hidden=true;
  $('patient-result').replaceChildren();$('transcript-input').value='';$('command-input').value='';$('main-answer').value='';$('treatment-reason').value='';$('readback-text').textContent='';$('interim-text').textContent='';$('history-list').replaceChildren();
  $('consent').checked=false;$('lat').value='';$('lon').value='';$('location-status').textContent='현재 위치를 확인해 주세요';
  for(const option of $('required-capabilities').options)option.selected=false;
}
async function connect({forceNew=false,patientIdOverride=null,loadExisting=false}={}){
  if(tasks>0||connecting||gpsBusy||capture.active||!!client?.pending||dirty||recoveryFieldConflict)return;
  connecting=true;controls();
  try{
    const server=normalizeServer($('server-url').value.trim());
    let handoff=null;
    if(handoffPending){
      const result=readDraftHandoff(handoffStorage(),server);
      if(result.status!=='valid')throw Error(result.status==='expired'?'음성 초안 연결의 10분 유효시간이 지났습니다. 원래 화면에서 다시 이어가거나 새 기록을 명시적으로 시작하세요.':'음성 초안 연결 정보가 없거나 서버 주소가 일치하지 않습니다. 원래 화면을 확인하거나 새 기록을 명시적으로 시작하세요.');
      handoff=result.payload;
    }
    const next=new VoiceClient({server,actorId:handoff?.actorId||actor});
    const [caps,terms,hospitals]=await Promise.all([next.api('/api/voice/capabilities'),next.api('/api/voice/terms'),next.api('/api/hospitals')]);
    capabilities=caps;catalog=terms;
    $('connection-status').textContent='서버 연결됨';$('connection-status').dataset.state='connected';
    $('required-capabilities').replaceChildren();
    for(const name of [...new Set(hospitals.flatMap(h=>h.capabilities||[]))].sort())$('required-capabilities').add(new Option(name,name));
    $('readback-field').replaceChildren(new Option('전체 요약',''));
    for(const field of terms.readback_fields||terms.supported_fields)$('readback-field').add(new Option(labels[field]||field,field));
    const supported=VoiceCapture.capabilities();
    $('speech-provider').value=supported.browser?'browser':caps.speech.configured&&supported.recording?'azure':'local_wav';
    $('speech-provider').querySelector('[value="browser"]').disabled=!supported.browser;
    $('speech-provider').querySelector('[value="azure"]').disabled=!caps.speech.configured||!supported.recording;
    $('speech-provider').querySelector('[value="local_wav"]').disabled=!supported.recording;
    $('capabilities-note').textContent=caps.speech.configured?'필요 진료 분야는 직접 확인해 선택하세요. 녹음 파일 인식 사용 가능.':'서버 음성인식 키 미설정 · 브라우저 인식, 직접 입력 또는 서버 전송 없는 로컬 오디오 시험을 사용할 수 있습니다.';
    if(handoff){
      await next.sync(handoff.sessionId);
      if(next.session?.id!==handoff.sessionId||!Number.isSafeInteger(next.session?.version)||next.session.version<handoff.version)throw Error('서버 초안의 ID 또는 버전이 저장한 기록과 일치하지 않습니다. 새 빈 초안으로 대체하지 않았습니다.');
      if(next.session.state!=='DRAFT'||(next.session.actor_id!==undefined&&next.session.actor_id!==handoff.actorId))throw Error('서버 초안의 상태 또는 작성자가 이어갈 음성 기록과 일치하지 않습니다. 새 빈 초안으로 대체하지 않았습니다.');
      const restoredPatient=next.session.patient_id?await next.api('/api/patients/'+encodeURIComponent(next.session.patient_id)):null;
      persistRestoredIdentity(handoff.actorId,next.session.id,next.server);
      resetCase();actor=handoff.actorId;client=next;handoffPending=false;handoffFailed=false;recommendationReady=true;
      attachRecovery(next,null,next.session.patient_id??null);recovery.update({session:next.session});
      currentPatient=restoredPatient;recommendationAwaitingSubmit=true;
      $('transcript-input').value=handoff.unappliedText;handoffUnapplied=!!handoff.unappliedText;
      remember();clearDraftHandoff(handoffStorage());removeHandoffParameter();
    }else{
      const requestedPatient=!forceNew?(patientIdOverride||params.get('pid')||undefined):undefined;
      if(requestedPatient!==undefined&&!/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(requestedPatient))throw Error('환자 ID 연결이 올바르지 않습니다.');
      const selected=!forceNew?findMainRecovery(handoffStorage(),{pageOrigin:location.origin,serverOrigin:server,actorId:actor,...(requestedPatient!==undefined?{patientId:requestedPatient}:{})}):null;
      if(selected?.expired)throw Error('임시 저장본의 12시간 유효기간이 지났습니다. 자동 복원·전송하지 않았습니다. 현재 탭의 보존 기록을 확인하세요.');
      const saved=!forceNew&&!loadExisting&&!selected&&previousIdentity.actor===actor&&previousIdentity.server===server?previousIdentity.session:null;
      if(selected?.record.sessionId||saved){
        const sid=selected?.record.sessionId||saved;await next.sync(sid);
        if(selected)MainRecovery.prototype.assertSession.call({record:selected.record},next.session);
        else if(next.session?.id!==sid||!Number.isSafeInteger(next.session?.version)||next.session.version<1||!['DRAFT','SUBMITTED'].includes(next.session.state)||(next.session.actor_id!==undefined&&next.session.actor_id!==actor))throw Error('저장한 초안의 ID·작성자·버전을 확인하지 못했습니다. 새 빈 초안으로 바꾸지 않았습니다.');
        if(requestedPatient!==undefined&&(next.session.patient_id??null)!==requestedPatient){if(selected)throw Error('연결한 환자와 보존한 초안의 환자가 다릅니다. 다른 기록을 적용하거나 새 초안을 만들지 않았습니다.');else next.session=null;}
      }
      const expectedPatient=next.session?.patient_id||requestedPatient;
      const restoredPatient=expectedPatient?await next.api('/api/patients/'+encodeURIComponent(expectedPatient)):null;
      if(restoredPatient&&restoredPatient.id!==expectedPatient)throw Error('서버 환자 응답의 범위가 다릅니다.');
      const startLinked=loadExisting&&next.session?.state==='SUBMITTED'&&!selected?.record.pending;
      const savedInput=startLinked?null:selected?.record.ui;
      resetCase();recoveryFieldConflict=false;recoveryCopy=null;client=next;
      if(startLinked)next.session=null;
      attachRecovery(next,startLinked?null:selected,requestedPatient??next.session?.patient_id??null);
      next.pending=!startLinked&&selected?.record.pending?JSON.parse(JSON.stringify(selected.record.pending)):null;
      if(next.session)recovery.update({session:next.session});
      currentPatient=restoredPatient;
      if(forceNew)removeHandoffParameter({newPatient:true});
      else if(loadExisting)selectPatientParameter(requestedPatient);
      if(forceNew||(loadExisting&&!next.session&&!next.pending)){await next.create(loadExisting?requestedPatient:undefined);saveRecovery('saved');}
      recommendationAwaitingSubmit=next.session?.state==='DRAFT'&&!!next.session.patient_id;
      render();if(savedInput)restoreRecoveryInput(savedInput);
    }
    if(next.session?.patient_id){if(!currentPatient)currentPatient=await next.api('/api/patients/'+encodeURIComponent(next.session.patient_id));renderPatient();if(!recommendationAwaitingSubmit)await candidates();}
    if(handoff)render();else controls();
    if(handoff){saveRecovery('saved');}
    message(handoffUnapplied?'저장된 초안을 불러왔습니다. 명령 뒤 문장은 아직 미반영이며, 내용을 검토한 뒤 문장 반영 또는 제외를 선택하세요.':recommendationReady?'저장된 초안을 불러왔습니다. 내용 검토부터 계속하세요.':client?.pending?'보존한 요청은 자동 전송하지 않았습니다. 원문과 범위를 확인한 뒤 같은 요청 다시 시도를 누르세요.':next.session?'동일 범위의 서버 기록과 로컬 입력창을 불러왔습니다.':'새 기록 시작 또는 기존 환자 불러오기를 선택하세요.');
    if(params.get('pid')&&!next.session)message('같은 이송 ID를 준비했습니다. 기존 환자 불러오기를 눌러 현재 상태를 확인하세요. 새 환자는 등록하지 않았습니다.');
  }catch(error){
    recoveryError=error.message;
    if(handoffPending){handoffFailed=true;client=null;$('session-meta').textContent='음성 초안 복원 실패 · 새 빈 초안을 만들지 않았습니다';$('recommendation-status').textContent=error.message;}
    $('connection-status').textContent='연결 확인 필요';$('connection-status').dataset.state='error';message(error.message,true);
  }
  finally{connecting=false;controls();}
}
const capture=new VoiceCapture({
  onInterim(text){$('interim-text').textContent=text||'듣고 있습니다…';},
  onFinal(text){
    finalCount++;$('transcript-input').value=text;$('interim-text').textContent='최종 문장 확인 · 서버 처리 중';
    saveInput();
    const extra={utterance_id:requestId(),text,is_final:true,mode:captureMode};
    const question=captureQuestion;captureQuestion=null;
    if(question){(async()=>{if(capture.active)await capture.stop();await answerMain(text,{question});})().catch(()=>{});return;}
    task(async()=>{
      // Close the microphone before reading a follow-up so app speech is never recorded as an answer.
      if(capture.active)await capture.stop();
      const result=await client.write('transcripts',extra);
      message('음성 기록을 초안에 반영했습니다.');
      await commandFeedback(result);return result;
    }).then(async()=>{await history();await speakNextQuestion();}).catch(()=>{});
  },
  onState(state){
    if(state==='limit'){finishRecording().catch(()=>{});return;}
    if(state==='idle'){clearInterval(ticker);$('recording-state').textContent='마이크 꺼짐';}
    else $('recording-state').textContent=state==='listening'?'실시간 인식 중':captureProvider==='local_wav'?'로컬 시험 녹음 중 · 서버 전송 없음':'녹음 중 · 종료 후 텍스트 변환';
    controls();
  },
  onError(error){clearInterval(ticker);message(error.message,true);controls();}
});
async function finishRecording(){
  if(!capture.active)return;
  const audio=await capture.stop();clearInterval(ticker);controls();
  if(audio&&captureProvider==='local_wav'){
    const recordingId='AUDIO-'+requestId();
    localAudioMetadata={...localAudioTestMetadata(capture.inputDiagnostics),recording_id:recordingId,wav_filename:recordingId+'.wav'};
    localAudioUrl=URL.createObjectURL(new Blob([Uint8Array.from(atob(audio.audio_base64),char=>char.charCodeAt(0))],{type:'audio/wav'}));
    $('audio-test-download').href=localAudioUrl;$('audio-test-download').download=localAudioMetadata.wav_filename;$('audio-test-download').hidden=false;$('audio-test-metadata').hidden=false;
    message('로컬 오디오 시험을 마쳤습니다. 다음 녹음 전에 소음 환경 입력 비교에서 WAV와 설정 JSON을 내려받으세요. 음성 인식이나 오디오 서버 전송은 실행하지 않았습니다.');
  }
  else if(audio){await write('audio',{...audio,utterance_id:requestId(),mode:captureMode});message('녹음 결과를 확인하고 잘못 인식된 값을 수정해 주세요.');await history();await speakNextQuestion();}
  else if(finalCount===0)message('최종 인식문이 없습니다. 마이크 권한과 음성인식 지원을 확인하거나 문장을 입력해 주세요.');
}
bindAsync('record-button',async()=>{
  if(capture.active){await finishRecording();return;}
  clearLocalAudio();capture.stopSpeaking();captureQuestion=null;captureMode=$('voice-mode').value;captureProvider=$('speech-provider').value;finalCount=0;
  await capture.start(captureProvider==='local_wav'?'azure':captureProvider,{audioProfile:$('audio-input-profile').value});startedAt=Date.now();controls();
  if(!capture.active)return;
  ticker=setInterval(()=>{$('recording-state').textContent=`${captureProvider==='browser'?'실시간 인식':'녹음'} · ${Math.floor((Date.now()-startedAt)/1000)}초`;},1000);
});
bindAsync('connect-server',connect);
async function newCase(){
  handoffPending=false;handoffFailed=false;recommendationReady=false;
  await connect({forceNew:true});
}
bindAsync('new-case',newCase);
bindAsync('handoff-new-case',newCase);
bindAsync('discard-handoff-text',()=>{if(blocked())return;handoffUnapplied=false;$('transcript-input').value='';saveInput();message('명령 뒤 미반영 문장을 제외했습니다. 저장된 초안은 변경하지 않았습니다.');controls();});
bindAsync('load-patient',async()=>{
  const id=$('resume-patient').value.trim();if(!/^[\w-]+$/.test(id))throw Error('불러올 환자 ID를 입력하세요.');
  await connect({patientIdOverride:id,loadExisting:true});
});
bindAsync('send-transcript',async()=>{const text=$('transcript-input').value;await write('transcripts',{utterance_id:requestId(),text,is_final:true,mode:$('voice-mode').value});if($('transcript-input').value===text)handoffUnapplied=false;controls();message(handoffUnapplied?'보낸 문장은 반영했습니다. 그동안 검토칸에서 변경한 문장은 아직 미반영입니다.':'문장 내용을 초안에 반영했습니다.');await history();await speakNextQuestion();});
bindAsync('send-command',async()=>{await task(async()=>{const result=await client.write('commands',{text:$('command-input').value});message('명령을 처리했습니다.');await commandFeedback(result);});await history();});
$('fields-form').addEventListener('submit',event=>{
  event.preventDefault();clearError();
  const changes={},unset=[];
  try{
    for(const input of $('fields-editor').querySelectorAll('[data-field]')){
      const key=input.dataset.field,raw=input.value.trim(),old=client.session.fields[key];
      if(!raw){if(old!==undefined)unset.push(key);continue;}
      const value=listFields.has(key)?raw.split(/[,\n]/).map(v=>v.trim()).filter(Boolean):key==='pre_ktas'&&/^\d$/.test(raw)?Number(raw):enums[key]||key==='age_group'?raw:key==='onset_at'?Math.floor(new Date(raw).getTime()/1000):Number(raw);
      if(JSON.stringify(value)!==JSON.stringify(old))changes[key]=value;
    }
    if(!Object.keys(changes).length&&!unset.length){dirty=false;render();return;}
    write('fields',{changes,unset_fields:unset,change_kind:$('change-kind').value},'PATCH').then(()=>{message('수정 내용을 초안에 반영했습니다. 다시 확인해 주세요.');return history();}).catch(()=>{});
  }catch(error){message(error.message,true);}
});
if($('discard-fields'))bindAsync('discard-fields',()=>{dirty=false;render();saveRecovery();});
bindAsync('undo-button',async()=>{await write('undo');message('직전 변경을 되돌렸습니다.');await history();});
bindAsync('refresh-session',()=>task(async()=>{await client.sync();await history();message('최신 초안을 불러왔습니다.');}));
bindAsync('retry-request',()=>task(async()=>{
  const pending=client.pending,path='/api/voice/sessions/'+encodeURIComponent(client.session?.id||'');
  const retriedTranscript=pending?.method==='POST'&&pending.path===path+'/transcripts'&&['dictation','auto'].includes(pending.body?.mode)&&typeof pending.body.text==='string';
  const retriedSubmit=pending?.method==='POST'&&(pending.path===path+'/submit'||pending.path===path+'/treatment-review/apply');
  const wasUnapplied=handoffUnapplied,pendingText=retriedTranscript?pending.body.text:null;
  const answerJob=mainFollowup?.flow.job;
  const restoredAnswer=!answerJob&&pending?.path===path+'/followup/answers'?{questionId:pending.body.question_id,text:pending.body.text}:null;
  const result=answerJob?await mainFollowup.flow.retry():await client.retry();
  if(answerJob){if(!result.answer?.accepted&&!result.answer?.unavailable)mainFollowup.deferred.set(answerJob.questionId,answerJob.text);mainFollowup.observe();}
  if(restoredAnswer){mainFollowup.observe();if(!result.answer?.accepted&&!result.answer?.unavailable)mainFollowup.deferred.set(restoredAnswer.questionId,restoredAnswer.text);else if($('main-answer').value===restoredAnswer.text)$('main-answer').value='';}
  if(wasUnapplied&&retriedTranscript&&$('transcript-input').value===pendingText)handoffUnapplied=false;
  if(retriedSubmit&&result.patient){currentPatient=await client.api('/api/patients/'+encodeURIComponent(result.patient.id));recommendationAwaitingSubmit=false;renderPatient();await candidates();}
  message(handoffUnapplied?(retriedTranscript?'같은 요청으로 보낸 문장의 반영을 확인했습니다. 현재 검토칸의 다른 문장은 아직 미반영입니다.':'같은 요청의 처리 결과를 확인했습니다. 검토칸의 미반영 문장을 확인해 주세요.'):'같은 요청으로 처리 결과를 확인했습니다.');
}));
bindAsync('readback-button',()=>task(async()=>{
  const result=await client.readback($('readback-field').value);$('readback-text').textContent=result.speak_text;
  if(canRead)capture.speak(result.speak_text);else message('이 브라우저는 읽어주기를 지원하지 않습니다. 화면에서 내용을 확인하세요.');
}));
bindAsync('stop-speaking',()=>capture.stopSpeaking());
bindAsync('confirm-button',async()=>{const mode=$('treatment-mode').value;await write('confirm',{confirmed:true});if(linkedWaiting())await task(async()=>{invalidateTreatment();await treatmentReview.load(currentPatient,mode);});message(linkedWaiting()?'현재 초안을 확인했습니다. 치료조건 변경안을 별도로 확인하고 적용하세요.':'현재 초안을 확인했습니다. 위치와 동의를 확인한 뒤 저장하세요.');});
bindAsync('submit-button',()=>task(async()=>{
  if(!client.session.confirmed)throw Error('현재 초안을 먼저 확인하세요.');
  if(client.session.patient_id)throw Error('기존 환자는 치료조건 변경안의 명시 확인·적용을 사용하세요.');
  const context=client.session.patient_id?{}:{consent:$('consent').checked,lat:Number($('lat').value),lon:Number($('lon').value),required_capabilities:[...$('required-capabilities').selectedOptions].map(o=>o.value),resource_demands:{ed_bed:1}};
  const result=await client.write('submit',{context,start_inquiry:false});currentPatient=result.patient;recommendationAwaitingSubmit=false;renderPatient();await candidates();
  message(result.operational_effect==='voice_record_saved_no_patient_change'?'기록 확인을 저장했습니다. 기존 병원 응답은 유지됩니다.':'환자정보를 서버에 저장했습니다. 후보 병원의 수용 확인은 별도로 필요합니다.');
}));
function renderPatient(){
  $('patient-result').replaceChildren();$('patient-navigation').hidden=!currentPatient;if(!currentPatient)return;
  $('patient-result').append(node('span','저장된 환자','badge'),node('strong',currentPatient.id),node('p',`환자정보 v${currentPatient.version} · ${currentPatient.state==='WAITING'?'병원 탐색 대기':currentPatient.state}`));
  if(currentPatient.lat!==null&&currentPatient.lon!==null){$('lat').value=currentPatient.lat;$('lon').value=currentPatient.lon;$('location-status').textContent='저장된 환자 위치';}
  $('patient-progress-link').href=patientNavigation(client.server,currentPatient.id,actor);
  let exchangeLink=$('patient-handoff-exchange-link');
  if(!exchangeLink){exchangeLink=node('a','인계 공유·병원 재확인','button button-secondary');exchangeLink.id='patient-handoff-exchange-link';$('patient-navigation').append(exchangeLink);}
  const exchangeUrl=new URL('/handoff-exchange.html',normalizeServer(client.server));
  exchangeUrl.search=new URLSearchParams({role:'owner',actor_id:actor,pid:currentPatient.id,
    ...(client.session?.id?{sid:client.session.id}:{})}).toString();
  exchangeLink.href=exchangeUrl.href;
  exchangeLink.hidden=!capabilities?.handoff_exchange_revision;
  const enroute=['CONFIRMED','IN_TRANSIT'].includes(currentPatient.state);$('patient-change-link').hidden=!enroute;
  if(enroute){const link=new URL(patientNavigation(client.server,currentPatient.id,actor,'/patient/transport-change.html'));if(currentPatient.destination_hospital_id)link.searchParams.set('hid',currentPatient.destination_hospital_id);$('patient-change-link').href=link.href;}
}
async function candidates(){
  if(!currentPatient||recommendationAwaitingSubmit)return;
  const owner=client,pid=currentPatient.id;
  const result=await owner.api('/api/patients/'+encodeURIComponent(pid)+'/candidates');
  if(client!==owner||currentPatient?.id!==pid)return;
  $('candidate-list').replaceChildren();
  for(const candidate of result.candidates.slice(0,6)){
    const item=node('article',undefined,'candidate-card');
    item.append(node('span','수용 확인 필요','badge'),node('h3',candidate.name),node('strong',`이동 약 ${Math.ceil(candidate.travel.duration_seconds/60)}분`));
    item.append(node('p',candidate.address||'주소 정보 없음'),node('small',`이동 추정: ${candidate.travel.provider}`));
    for(const reason of candidate.reasons.slice(0,3))item.append(node('p',reason,'candidate-reason'));
    const detail=node('details');detail.append(node('summary',`확인 항목 ${candidate.checks.length}개`));
    for(const check of candidate.checks)detail.append(node('p',check));item.append(detail);$('candidate-list').append(item);
  }
  if(!result.candidates.length)$('candidate-list').append(node('p','현재 조건으로 확인할 후보가 없습니다. 진료 조건과 병원 정보를 다시 확인하세요.','empty-state'));
}
bindAsync('refresh-candidates',()=>task(candidates));
bindAsync('main-answer-send',()=>answerMain($('main-answer').value));
bindAsync('main-answer-unavailable',()=>answerMain('미측정',{unavailable:true}));
bindAsync('main-question-defer',()=>{if(blocked()||dirty)return;mainFollowup.defer();renderFollowup();controls();});
bindAsync('main-question-retry',()=>{if(blocked()||dirty)return;mainFollowup.retryDeferred();renderFollowup();controls();});
bindAsync('main-question-speak',()=>{const q=mainFollowup?.current();if(q&&!capture.active&&canRead)capture.speak(q.question);});
bindAsync('main-answer-record',async()=>{if(capture.active){await finishRecording();return;}if(blocked()||dirty||handoffUnapplied)return;capture.stopSpeaking();captureQuestion=mainFollowup.current();if(!captureQuestion)return;captureProvider='browser';finalCount=0;await capture.start('browser');controls();});
bindAsync('treatment-load',()=>task(async()=>{const mode=$('treatment-mode').value;invalidateTreatment();await client.sync();currentPatient=await client.api('/api/patients/'+encodeURIComponent(client.session.patient_id));await treatmentReview.load(currentPatient,mode);}));
bindAsync('treatment-preview',()=>task(async()=>{invalidateTreatment();await treatmentReview.preview(currentPatient,$('treatment-mode').value);}));
bindAsync('treatment-apply',()=>task(async()=>{const result=await treatmentReview.apply(currentPatient,{confirmed:$('treatment-confirm').checked,allowReduction:$('treatment-reduction').checked,reason:$('treatment-reason').value.trim()});currentPatient=await client.api('/api/patients/'+encodeURIComponent(result.patient.id));recommendationAwaitingSubmit=false;renderPatient();await candidates();$('treatment-status').textContent=result.reevaluation?`재평가 ${result.reevaluation.status} · 워커 ${result.reevaluation.availability?.worker_state||'미확인'} · 목적지 자동 변경 없음`:'';message('확인한 새 관찰과 치료조건을 적용했습니다. 수용 응답과 추천을 진행 화면에서 다시 확인하세요.');}));
for(const id of ['treatment-confirm','treatment-reduction'])$(id).addEventListener('change',controls);
$('treatment-mode').addEventListener('change',()=>{invalidateTreatment();controls();});
bindAsync('use-demo-location',()=>{$('lat').value='35.1595';$('lon').value='126.8526';$('location-status').textContent='광주 시연 좌표 · 실제 GPS 위치가 아닙니다';saveInput();});
bindAsync('gps-button',()=>new Promise((resolve,reject)=>{
  if(!navigator.geolocation){const error=Error('이 브라우저에서 위치를 가져올 수 없습니다. 좌표를 직접 입력하세요.');message(error.message,true);reject(error);return;}
  gpsBusy=true;controls();$('location-status').textContent='현재 위치 확인 중…';
  navigator.geolocation.getCurrentPosition(position=>{$('lat').value=position.coords.latitude.toFixed(6);$('lon').value=position.coords.longitude.toFixed(6);$('location-status').textContent=`GPS 위치 확인 · 오차 약 ${Math.round(position.coords.accuracy)}m`;gpsBusy=false;saveInput();resolve();},()=>{message('위치 권한이나 연결을 확인하고 좌표를 직접 입력하세요.',true);gpsBusy=false;controls();reject(Error('위치 확인 실패'));},{enableHighAccuracy:true,timeout:10000,maximumAge:0});
}));
for(const id of ['lat','lon','consent','transcript-input','command-input','main-answer','voice-mode','change-kind','required-capabilities'])$(id).addEventListener('input',saveInput);
bindAsync('main-recovery-save',()=>{saveRecovery();message('임시 저장 상태를 다시 확인했습니다. 보존한 요청은 명시적으로 재시도하세요.');controls();});
bindAsync('main-recovery-discard-edits',()=>{recoveryFieldConflict=false;recoveryCopy=null;dirty=false;render();saveRecovery();message('보존한 항목 편집을 제외하고 서버의 최신 기록을 사용합니다. 자동 전송하지 않았습니다.');});
bindAsync('sample-button',()=>{$('transcript-input').value='65세 남성. 흉통. 호흡곤란 없음. 의식 명료. 혈압 110에 70. 맥박 90. 산소포화도 97. 증상은 20분 전 발생.';saveInput();message('시연 문장을 넣었습니다. 음성 인식 결과가 아닙니다. 문장 반영을 눌러 시험하세요.');});
function clearLocalAudio(){if(localAudioUrl)URL.revokeObjectURL(localAudioUrl);localAudioUrl=null;localAudioMetadata=null;$('audio-test-download').hidden=true;$('audio-test-metadata').hidden=true;}
$('audio-test-metadata').addEventListener('click',()=>{if(!localAudioMetadata)return;const url=URL.createObjectURL(new Blob([JSON.stringify(localAudioMetadata,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='voice-input-settings-'+Date.now()+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
window.addEventListener('pagehide',()=>{if(!connecting)try{saveRecovery();}catch(_){}capture.abort();capture.stopSpeaking();clearInterval(ticker);clearLocalAudio();});
$('server-url').value=params.get('server')||location.origin;
$('resume-patient').value=params.get('pid')||'';
$('recording-state').textContent='마이크 꺼짐';
$('speech-provider').addEventListener('change',controls);
$('audio-input-profile').addEventListener('change',controls);
if(actorQueryValid)connect();else{message('작업자 ID는 영문·숫자로 시작하는 80자 이내 ID여야 합니다. 진행 화면의 연결을 다시 확인하세요.',true);controls();}
