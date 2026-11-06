import {readPatientRecords,PATIENT_RECORDS_KEY} from './patient-records.mjs';
import {readDraftRecovery,RECOVERY_KEY} from './draft-recovery.mjs';

const STEPS = {input:'말하기',review:'내용 확인',submit:'등록',hospital:'병원 연결'};

/** Quick restores its own active notebook at the page origin; never infer a patient from URL hints. */
export function canReturnToConversation({fromVoiceHandoff,session,actorId,serverOrigin,pageOrigin,tabStorage,patientStorage}={}){
  try{
    if(!fromVoiceHandoff||!session?.id||session.state!=='DRAFT'||session.patient_id||serverOrigin!==pageOrigin||
        (session.actor_id!==undefined&&session.actor_id!==actorId))return false;
    if(tabStorage?.getItem('paramedic.voice.session')!==session.id||tabStorage.getItem('paramedic.voice.actor')!==actorId||
        tabStorage.getItem('paramedic.voice.server')!==serverOrigin)return false;
    const result=readPatientRecords(patientStorage,pageOrigin);
    if(result.status!=='valid')return false;
    const active=result.book.records.find(record=>record.id===result.book.activeId);
    if(!active?.checkpoint)return false;
    const recovery=readDraftRecovery({getItem:key=>key===RECOVERY_KEY?active.checkpoint:null},pageOrigin);
    const p=recovery.payload;
    return recovery.status==='valid'&&!p.pending&&p.sessionId===session.id&&p.actorId===actorId&&p.serverOrigin===serverOrigin&&
      p.version<=session.version&&patientStorage.getItem(PATIENT_RECORDS_KEY)===result.raw;
  }catch{return false;}
}

/** Presentation only: this module never confirms, submits, retries, or writes a draft. */
export function initStagedReview({document,onBackToConversation}={}) {
  const location=document.defaultView?.location||globalThis.location;
  const query=new URLSearchParams(location?.search||'');
  const active=query.get('view')!=='full'&&(document.documentElement?.dataset.fieldEntry==='true'||query.get('flow')==='field'||query.get('voice_handoff')==='1');
  if(!active)return {active:false,update(){}};
  const $=id=>document.getElementById(id);
  const make=(tag,text,className)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(className)el.className=className;return el;};
  const main=$('main-content'),review=$('review-section'),hospital=$('hospital-section');
  const heading=main.querySelector('.page-header');
  document.body.classList.add('field-flow');
  const nav=make('nav',null,'staged-nav');nav.setAttribute('aria-label','환자 이송 진행 단계');
  const buttons=new Map();
  for(const [index,[step,label]]of Object.entries(STEPS).entries()){
    const button=make('button',null,'staged-step');button.type='button';button.dataset.stagedStep=step;
    button.append(make('span',String(index+1),'staged-number'),make('span',label));
    button.addEventListener('click',()=>select(step));nav.append(button);buttons.set(step,button);
  }
  heading.after(nav);
  const status=make('p',null,'staged-status');status.id='staged-status';status.setAttribute('role','status');nav.after(status);
  const settings=make('details',null,'staged-settings');settings.id='staged-settings';settings.append(make('summary','설정 · 기록 및 연결 정보'));
  const settingsBody=make('div',null,'staged-settings-body');settings.append(settingsBody);
  const footer=main.querySelector('.page-footer');main.insertBefore(settings,footer);
  const sidebar=document.querySelector('.sidebar');if(sidebar)settingsBody.append(sidebar);
  const full=make('a','전체 화면 보기','text-button');full.id='staged-full-view';
  const fullQuery=new URLSearchParams(query);fullQuery.set('view','full');full.href=`${location?.pathname||'./'}?${fullQuery}`;
  full.addEventListener('click',event=>{if(blocked()){event.preventDefault();event.stopPropagation();}});settingsBody.append(full);
  const recovery=$('main-recovery-status')?.closest('section');if(recovery)settingsBody.append(recovery);
  const history=$('history-list')?.closest('.history-panel');if(history)settingsBody.append(history);
  const sessionBar=$('session-meta')?.closest('.session-bar');if(sessionBar)settingsBody.append(sessionBar);
  const source=make('details',null,'staged-source');source.id='staged-source';source.append(make('summary','인식 원문 · 직접 입력'));
  const sourceNote=make('p','아직 반영하지 않은 문장은 직접 검토한 뒤 반영하거나 제외하세요.','warning-note');sourceNote.id='staged-source-note';sourceNote.hidden=true;source.append(sourceNote);
  for(const el of [document.querySelector('.transcript-heading'),$('transcript-input'),$('transcript-help'),$('send-transcript'),$('discard-handoff-text')])if(el)source.append(el);
  review.insertBefore(source,$('main-followup'));
  const more=make('details',null,'staged-review-details');more.append(make('summary','읽어주기 · 변경 이력 · 기존 환자 조건'));
  for(const el of [document.querySelector('.readback-section'),$('main-changes'),$('main-treatment')])if(el)more.append(el);
  review.insertBefore(more,$('review-confirm-actions'));
  const patientArea=make('div',null,'staged-patient');patientArea.id='staged-patient';
  patientArea.append($('patient-result'),$('patient-navigation'));hospital.querySelector('.panel-heading').after(patientArea);
  for(const link of document.querySelectorAll('#patient-navigation a'))link.addEventListener('click',guardLink,true);
  // The exchange link may be created later by renderPatient; delegation covers it too.
  $('patient-navigation').addEventListener('click',guardLink,true);
  const panels={review,submit:$('submit-section'),hospital};
  let state={},selected='review',identity=null,wasConfirmed=false,previousPatient=null;
  function blocked(){return !!(state.pending||state.locked||state.dirty||state.unapplied||state.handoffLoading||state.handoffFailed||state.recoveryError||state.recoveryConflict);}
  function guardLink(event){if(blocked()){event.preventDefault();event.stopPropagation();}}
  function canSelect(step){return !blocked()&&((step==='input'&&state.canReturnToConversation===true&&!state.patient&&!state.session?.patient_id)||step==='review'||(step==='submit'&&!!state.session?.confirmed)||(step==='hospital'&&!!state.patient));}
  function select(step){
    if(!canSelect(step))return false;
    if(step==='input')return onBackToConversation?.()!==false;
    selected=step;paint();panels[step]?.scrollIntoView?.({block:'start',behavior:'smooth'});return true;
  }
  function paint(){
    $('input-section').hidden=true;
    for(const [step,panel]of Object.entries(panels))panel.hidden=selected!==step;
    // Preserve the original source controls, including their existing disabled guards.
    const forceSource=!!(state.unapplied||state.pending||state.recoveryError||state.recoveryConflict);
    sourceNote.hidden=!state.unapplied;
    source.classList.toggle('is-unapplied',!!state.unapplied);
    if(forceSource){source.open=true;main.insertBefore(source,document.querySelector('.workspace-grid'));}
    else if(source.parentNode!==review)review.insertBefore(source,$('main-followup'));
    if(recovery){
      const urgent=!!(state.pending||state.recoveryError||state.recoveryConflict||!$('main-recovery-copy').hidden);
      if(urgent)main.insertBefore(recovery,document.querySelector('.workspace-grid'));
      else if(recovery.parentNode!==settingsBody)settingsBody.append(recovery);
    }
    const blockedNow=blocked();
    for(const [step,button]of buttons){button.disabled=!canSelect(step);button.classList.toggle('is-current',selected===step);if(selected===step)button.setAttribute('aria-current','step');else button.removeAttribute('aria-current');}
    for(const item of document.querySelectorAll('[data-step]'))item.classList.toggle('is-current',item.dataset.step===selected);
    document.body.classList.toggle('field-has-unapplied',!!state.unapplied);
    document.body.classList.toggle('field-navigation-blocked',blockedNow);
    for(const link of document.querySelectorAll('#patient-navigation a')){link.setAttribute('aria-disabled',String(blockedNow));if(blockedNow)link.setAttribute('tabindex','-1');else link.removeAttribute('tabindex');}
    const text=state.pending?'저장 결과 확인이 필요합니다. 같은 요청 다시 시도를 먼저 진행하세요.':state.handoffFailed?'초안을 불러오지 못했습니다. 복원 안내를 확인하세요.':state.handoffLoading?'저장한 초안을 확인하고 있습니다.':state.unapplied?'미반영 원문을 먼저 반영하거나 제외하세요.':state.recoveryError||state.recoveryConflict?'보존한 원문과 복구 안내를 먼저 확인하세요.':state.dirty?'수정한 내용을 저장하거나 되돌린 뒤 계속하세요.':state.locked?'현재 작업을 마친 뒤 다음 단계로 이동할 수 있습니다.':selected==='review'?'입력된 내용을 검토한 뒤 아래에서 기록 확인을 완료하세요.':selected==='submit'?'위치와 동의를 확인한 뒤 환자 등록을 눌러 주세요.':'같은 환자의 진행 화면에서 병원 문의와 응답을 확인하세요.';
    status.textContent=text;$('workflow-step').textContent=`${Object.keys(STEPS).indexOf(selected)+1} ${STEPS[selected]}`;
  }
  return {active:true,select,update(next){
    state=next||{};
    const nextIdentity=state.session?.id||null,patientId=state.patient?.id||null,confirmed=!!state.session?.confirmed;
    if(identity!==nextIdentity||previousPatient!==patientId){selected=patientId?'hospital':confirmed?'submit':'review';}
    else if(confirmed&&!wasConfirmed)selected='submit';
    else if(!confirmed&&wasConfirmed&&selected==='submit')selected='review';
    if(selected==='hospital'&&!patientId)selected=confirmed?'submit':'review';
    if(selected==='submit'&&!confirmed)selected='review';
    identity=nextIdentity;previousPatient=patientId;wasConfirmed=confirmed;paint();
  }};
}
