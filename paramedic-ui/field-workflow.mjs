import {patientNavigation} from './main-followup.mjs';

// A downstream view over the conversation's client. It owns no API client,
// patient identity, microphone or request ledger.
const LABELS={age:'나이',age_group:'연령대',sex:'성별',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상',consciousness:'의식',breathing:'호흡',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',onset_at:'증상 발생 시각',avpu:'AVPU',chief_complaint:'주증상 분류',age_category:'연령 구분',pre_ktas:'Pre-KTAS',stemi_suspected:'심전도 ST 상승',injury_region:'손상 부위',labor:'진통·양막 파열',pregnancy_weeks:'임신 주수',seizure_ongoing:'경련 지속',bleeding_risk:'출혈 경향',high_risk_mechanism:'고위험 손상 기전',pain_score:'통증 점수',gcs:'GCS'};
const ENUMS={sex:{male:'남성',female:'여성',unknown:'미확인'},consciousness:{alert:'명료',impaired:'저하',unresponsive:'반응 없음',unknown:'미확인'},breathing:{normal:'정상',abnormal:'비정상',absent:'호흡 없음',unknown:'미확인'},avpu:{A:'A 명료',V:'V 목소리 반응',P:'P 통증 반응',U:'U 무반응',unknown:'미확인'},chief_complaint:{cardiac:'흉통·심장',stroke:'뇌졸중 의심',altered_seizure:'의식저하·경련',dyspnea:'호흡곤란',trauma:'외상',bleeding:'출혈',abdominal:'복통·소화기',obstetric:'산과·분만',toxic_env:'중독·환경',other:'기타'},age_category:{adult:'성인',child:'소아',neonate:'신생아',pregnant:'임신 중',unknown:'미확인'},pre_ktas:{1:'1',2:'2',3:'3',4:'4',5:'5',unclassified:'미분류'},stemi_suspected:{yes:'확인됨',no:'없음',unmeasured:'미측정'},injury_region:{head:'머리',torso:'몸통',limb:'팔다리',multiple:'여러 곳'},labor:{yes:'있음',no:'없음'},seizure_ongoing:{yes:'지속 중',no:'멈춤'},bleeding_risk:{yes:'있음',no:'없음'},high_risk_mechanism:{yes:'있음',no:'없음'}};
const LISTS=new Set(['symptoms','denied_symptoms','uncertain_symptoms']);
const CORE=new Set(['age','sex','symptoms','consciousness','breathing','systolic_bp','diastolic_bp','pulse','spo2']);
const STEPS=new Set(['review','submit','hospital']);
const clone=value=>JSON.parse(JSON.stringify(value));
const textValue=(key,value)=>value===undefined?'':Array.isArray(value)?value.join(', '):key==='onset_at'?new Date(value*1000-new Date(value*1000).getTimezoneOffset()*60000).toISOString().slice(0,19):String(value);

export function initFieldWorkflow({document,host,getContext,isBlocked=()=>false,canConfirm=()=>true,onBusy=()=>{},onResult=()=>{},onError=()=>{},onChange=()=>{},onStep=()=>{}}={}){
  if(!document||!host||typeof getContext!=='function')throw Error('통합 기록 화면의 연결 정보가 없습니다.');
  const refs={},inputs=new Map();
  let step=null,owner=null,patient=null,localBusy=false,loadingReferences=null,referencesServer=null,hospitalLoading=null;
  let catalog=null,capabilities=null,hospitals=[],editorVersion=null,editorIdentity=null,customStatus='';
  let form={lat:'',lon:'',consent:false,requiredCapabilities:[],editor:{},editorDirty:false};
  function make(tag,text,id,className){const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(id){el.id='fw-'+id;refs[id]=el;}if(className)el.className=className;return el;}
  function button(text,id,handler,kind='secondary'){const el=make('button',text,id,'fw-button fw-'+kind);el.type='button';el.addEventListener('click',handler);return el;}
  function field(label,input){const box=make('label',undefined,null,'fw-field');box.append(make('span',label),input);return box;}
  const title=make('h2','이송 기록 검토','title');
  const status=make('p','','status','fw-status');status.setAttribute('role','status');
  const review=make('section',undefined,'review','fw-panel');
  review.append(make('p','확인한 값만 기록하세요. 빈 항목은 미확인으로 남기며 정상이나 0으로 채우지 않습니다.',null,'fw-help'));
  const editor=make('div',undefined,'fields','fw-fields');
  const extra=make('details',undefined,'extra','fw-extra');extra.append(make('summary','추가 항목 · 응급도'));
  const extraFields=make('div',undefined,'extra-fields','fw-fields');extra.append(extraFields);
  const warnings=make('div',undefined,'warnings','fw-warnings');
  const editorStatus=make('p','','editor-status','fw-help');editorStatus.setAttribute('role','status');
  const save=button('수정 내용 저장','save',()=>saveFields(),'primary');
  const discard=button('서버 값으로 되돌리기','discard',()=>{if(blocked())return;form.editorDirty=false;form.editor={};editorVersion=null;editorIdentity=null;renderFields();changed();});
  const confirm=button('현재 기록 확인 완료','confirm',()=>confirmRecord(),'primary');
  review.append(editor,extra,warnings,editorStatus,make('div',undefined,'editor-actions','fw-actions'),confirm);
  refs['editor-actions'].append(save,discard);
  const registration=make('section',undefined,'registration','fw-panel');
  registration.append(make('p','현재 기록을 확인한 뒤 위치·필요 진료·동의를 확인하고 등록하세요.',null,'fw-help'));
  const locationActions=make('div',undefined,null,'fw-actions');
  const gps=button('현재 위치 확인','gps',()=>locate());
  const demo=button('시연 위치 사용','demo-location',()=>{if(blocked())return;form.lat='35.1595';form.lon='126.8526';refs['location-status'].textContent='광주 시연 좌표 · 실제 GPS 위치가 아닙니다';syncForm();changed();});
  locationActions.append(gps,demo);registration.append(locationActions);
  const locationStatus=make('p','위치 확인을 누르거나 좌표를 직접 입력하세요.','location-status','fw-help');locationStatus.setAttribute('role','status');registration.append(locationStatus);
  const coords=make('div',undefined,null,'fw-fields');
  for(const [key,label]of [['lat','위도'],['lon','경도']]){const input=make('input',undefined,key);input.type='number';input.step='any';input.inputMode='decimal';input.placeholder='미입력';input.addEventListener('input',()=>{form[key]=input.value;changed();});coords.append(field(label,input));}
  registration.append(coords);
  const required=make('select',undefined,'required-capabilities');required.multiple=true;required.size=4;
  required.addEventListener('change',()=>{form.requiredCapabilities=[...required.selectedOptions].map(option=>option.value);changed();});
  registration.append(field('직접 확인한 필요 진료 (복수 선택)',required),make('p','선택하지 않은 진료는 미확인입니다. 후보 병원은 수용 확답이 아닙니다.',null,'fw-help'));
  const consent=make('input',undefined,'consent');consent.type='checkbox';consent.checked=false;consent.addEventListener('change',()=>{form.consent=consent.checked;changed();});registration.append(field('환자정보 수집·병원 전달 동의와 입력값을 확인했습니다.',consent));
  const submit=button('환자 등록','submit',()=>submitRecord(),'primary');registration.append(submit);
  const hospital=make('section',undefined,'hospital','fw-panel');
  const patientInfo=make('div',undefined,'patient','fw-patient');
  const refresh=button('후보 새로고침','refresh',()=>loadHospital());
  const links=make('nav',undefined,'links','fw-actions');links.setAttribute('aria-label','같은 환자 진행');
  for(const [key,label]of [['progress','병원 문의·이송 진행'],['handoff','인계 공유·병원 재확인'],['change','이송 중 환자정보 변경']]){const link=make('a',label,key,'fw-button fw-secondary');link.addEventListener('click',event=>{if(blocked()||form.editorDirty){event.preventDefault();event.stopPropagation();}});links.append(link);}
  refs.handoff.hidden=true;
  const candidates=make('div',undefined,'candidates','fw-candidates');
  hospital.append(patientInfo,links,make('p','후보는 조건과 이동시간을 비교한 목록입니다. 병원의 수용 응답과 담당자의 목적지 확정은 별도로 필요합니다.',null,'fw-help'),refresh,candidates);
  host.classList.add('fw-workflow');host.append(title,status,review,registration,hospital);host.hidden=true;

  function context(){const c=getContext();if(!c?.client||!c?.flow)return null;return {...c,session:c.client.session??c.session};}
  function token(c=context()){return c?{client:c.client,flow:c.flow,actor:c.actor,epoch:c.epoch,recordId:c.recordId,sid:c.session?.id||null,version:c.session?.version||0}:null;}
  function same(t,{version=true}={}){const c=context();return !!t&&!!c&&c.client===t.client&&c.flow===t.flow&&c.actor===t.actor&&c.epoch===t.epoch&&c.recordId===t.recordId&&(c.session?.id||null)===t.sid&&(!version||(c.session?.version||0)===t.version)&&(c.session?.actor_id===undefined||c.session.actor_id===c.actor);}
  function blocked(){return localBusy||!!isBlocked()||!!context()?.client.pending;}
  function changed(){customStatus='';paint();onChange();}
  function error(err){const value=err instanceof Error?err:Error(String(err));customStatus=value.message;status.textContent=value.message;onError(value);}
  function setStep(next){if(next!==null&&!STEPS.has(next))return false;const changedStep=step!==next;step=next;host.hidden=next===null;paint();onStep(next);if(next!==null&&changedStep){host.scrollIntoView?.({block:'start'});title.tabIndex=-1;title.focus?.();}return true;}
  function validCoords(){return form.lat.trim()!==''&&form.lon.trim()!==''&&Number.isFinite(Number(form.lat))&&Math.abs(Number(form.lat))<=90&&Number.isFinite(Number(form.lon))&&Math.abs(Number(form.lon))<=180;}
  function dirtyStale(){return form.editorDirty&&editorVersion!==(context()?.session?.version||0);}
  function canSubmit(){const c=context();return !!c?.session&&c.session.state==='DRAFT'&&!c.session.patient_id&&c.session.confirmed===true&&!blocked()&&!form.editorDirty&&canConfirm()&&validCoords()&&form.consent;}
  function syncForm(){refs.lat.value=form.lat;refs.lon.value=form.lon;refs.consent.checked=form.consent;for(const value of form.requiredCapabilities)if(![...refs['required-capabilities'].children].some(option=>option.value===value)){const option=make('option',value+' (보관된 선택)');option.value=value;refs['required-capabilities'].append(option);}for(const option of refs['required-capabilities'].children)option.selected=form.requiredCapabilities.includes(option.value);}
  function paint(){
    const c=context(),session=c?.session,locked=blocked();host.hidden=step===null;
    refs.review.hidden=step!=='review';refs.registration.hidden=step!=='submit';refs.hospital.hidden=step!=='hospital';
    title.textContent=step==='review'?'내용 확인 · 이송 기록':step==='submit'?'위치·동의 확인 후 환자 등록':'병원 연결';
    for(const input of inputs.values())input.disabled=locked||session?.state!=='DRAFT';
    save.disabled=locked||session?.state!=='DRAFT'||!form.editorDirty||dirtyStale();discard.disabled=locked||!form.editorDirty;
    confirm.disabled=locked||session?.state!=='DRAFT'||!Object.keys(session?.fields||{}).length||form.editorDirty||!canConfirm();
    for(const key of ['lat','lon','consent','required-capabilities','gps','demo-location'])refs[key].disabled=locked||session?.state!=='DRAFT'||!!session?.patient_id;
    submit.disabled=!canSubmit();submit.hidden=!!session?.patient_id||session?.state==='SUBMITTED';
    refresh.disabled=locked||!session?.patient_id;
    for(const link of refs.links.children){link.setAttribute('aria-disabled',String(locked||form.editorDirty));if(locked||form.editorDirty)link.setAttribute('tabindex','-1');else link.removeAttribute('tabindex');}
    editorStatus.textContent=dirtyStale()?'편집 중 기록이 갱신됐습니다. 입력은 유지했습니다. 내용을 별도로 보관한 뒤 서버 값으로 되돌리고 최신 기록에서 다시 수정하세요.':form.editorDirty?'수정 내용은 아직 저장되지 않았습니다. 저장 후 현재 기록을 다시 확인하세요.':session?.confirmed?'현재 버전 확인 완료':'값을 원문과 비교한 뒤 현재 기록 확인을 완료하세요.';
    status.textContent=customStatus||(locked?'현재 작업의 저장 결과를 확인하고 있습니다.':step==='review'?'같은 초안의 입력값을 확인합니다.':step==='submit'?'등록 버튼을 눌러야 환자가 등록됩니다. 병원 문의는 자동 시작하지 않습니다.':'같은 환자의 후보와 진행 상태를 확인하세요.');
  }
  function renderFields(){
    const c=context(),session=c?.session;if(!session)return;
    const identity=session.id+'|'+(session.version||0)+'|'+(catalog?.supported_fields||[]).join(',')+'|'+(capabilities?.r1_fields||[]).join(',');
    if(editorIdentity===identity||form.editorDirty&&inputs.size&&editorIdentity&&!editorIdentity.startsWith(session.id+'|'+(session.version||0)+'|'))return;
    if(!form.editorDirty){form.editor={};editorVersion=session.version;}
    refs.fields.replaceChildren();refs['extra-fields'].replaceChildren();inputs.clear();
    const keys=[...new Set([...(catalog?.supported_fields||[]),...(capabilities?.r1_fields||[]),...Object.keys(session.fields||{})])].filter(key=>Object.hasOwn(LABELS,key));
    for(const key of keys){
      let input;if(ENUMS[key]){input=make('select');for(const [value,label]of [['','미확인 · 값 비움'],...Object.entries(ENUMS[key])]){const option=make('option',label);option.value=value;input.append(option);}}
      else{input=make(LISTS.has(key)?'textarea':'input');if(LISTS.has(key))input.rows=2;else{input.type=key==='age_group'?'text':key==='onset_at'?'datetime-local':'number';input.step=['temperature','spo2','glucose'].includes(key)?'any':'1';}input.placeholder='미확인';}
      input.id='fw-field-'+key;input.dataset.fwField=key;input.value=Object.hasOwn(form.editor,key)?form.editor[key]:textValue(key,session.fields[key]);form.editor[key]=input.value;
      input.addEventListener('input',()=>{form.editor[key]=input.value;form.editorDirty=true;changed();});inputs.set(key,input);
      const box=field(LABELS[key]||key,input);const metadata=session.field_metadata?.[key];
      if(metadata?.needs_clarification)box.append(make('small','재확인 필요 · 이전 관찰값일 수 있습니다.','', 'fw-warning'));
      if(metadata?.evidence?.length){const detail=make('details');detail.append(make('summary','입력 근거'),make('p',metadata.evidence.join(' / ')));box.append(detail);}
      (CORE.has(key)||session.fields[key]!==undefined?refs.fields:refs['extra-fields']).append(box);
    }
    refs.extra.hidden=!refs['extra-fields'].children.length;
    warnings.replaceChildren(...(session.warnings||[]).map(value=>make('p',value,null,'fw-warning')));editorIdentity=identity;paint();
  }
  async function loadReferences(){
    const c=context();if(!c||loadingReferences||referencesServer===c.client.server)return;
    const t=token(c),job={token:t};loadingReferences=job;
    try{const [terms,caps,list]=await Promise.all([c.client.api('/api/voice/terms'),c.client.api('/api/voice/capabilities'),c.client.api('/api/hospitals')]);if(!same(t))return;
      catalog=terms;capabilities=caps;hospitals=Array.isArray(list)?list:[];referencesServer=c.client.server;
      const names=[...new Set([...hospitals.flatMap(h=>h.capabilities||[]),...form.requiredCapabilities])].sort();refs['required-capabilities'].replaceChildren(...names.map(name=>{const option=make('option',name);option.value=name;option.selected=form.requiredCapabilities.includes(name);return option;}));renderFields();renderPatient();
    }catch(err){if(same(t,{version:false}))error(Error('입력 항목과 진료 목록을 불러오지 못했습니다. '+err.message));}
    finally{if(loadingReferences===job){loadingReferences=null;paint();if(step!==null&&!same(t,{version:false})&&context()?.client.server!==referencesServer)void loadReferences();}}
  }
  async function run(operation,action,accepted){
    const c=context();if(!c||blocked())return false;const t=token(c);customStatus='';localBusy=true;onBusy(true);paint();
    try{const result=await action(c);if(!same(t,{version:false}))return false;
      if(result.session&&(result.session.id!==t.sid||result.session.actor_id!==undefined&&result.session.actor_id!==t.actor))throw Error('처리 결과의 초안 또는 작성자가 다릅니다.');
      if(accepted)accepted(result);handleResult(result,operation);await onResult(result);onChange();return true;
    }catch(err){if(same(t,{version:false}))error(err);return false;}
    finally{localBusy=false;onBusy(false);if(same(t,{version:false})){renderFields();paint();if(step==='hospital'&&operation==='submit')void loadHospital();}}
  }
  async function saveFields(){
    const c=context();if(!c?.session||blocked()||!form.editorDirty||dirtyStale())return false;
    const changes={},unset=[];
    try{for(const [key,rawValue]of Object.entries(form.editor)){const raw=rawValue.trim(),old=c.session.fields[key];if(!raw){if(old!==undefined)unset.push(key);continue;}
      const value=LISTS.has(key)?raw.split(/[,\n]/).map(v=>v.trim()).filter(Boolean):key==='pre_ktas'&&/^\d$/.test(raw)?Number(raw):ENUMS[key]||key==='age_group'?raw:key==='onset_at'?Math.floor(new Date(raw).getTime()/1000):Number(raw);
      if(typeof value==='number'&&!Number.isFinite(value))throw Error((LABELS[key]||key)+' 값을 확인하세요.');if(JSON.stringify(value)!==JSON.stringify(old))changes[key]=value;}
      if(!Object.keys(changes).length&&!unset.length){form.editorDirty=false;editorIdentity=null;renderFields();changed();return true;}
      return run('review-fields',ctx=>ctx.flow.reviewFields({changes,unset_fields:unset}),()=>{form.editorDirty=false;form.editor={};editorIdentity=null;editorVersion=null;});
    }catch(err){error(err);return false;}
  }
  async function confirmRecord(){const c=context();if(!c?.session||blocked()||form.editorDirty||!canConfirm()||c.session.state!=='DRAFT'||!Object.keys(c.session.fields||{}).length)return false;return run('confirm',ctx=>ctx.flow.confirm(),()=>setStep('submit'));}
  async function submitRecord(){if(!canSubmit())return false;const body={consent:true,lat:Number(form.lat),lon:Number(form.lon),required_capabilities:[...form.requiredCapabilities],resource_demands:{ed_bed:1}};return run('submit',ctx=>ctx.flow.submit(body),result=>{patient=result.patient||null;setStep('hospital');});}
  async function locate(){
    const c=context();if(!c||blocked()||c.session?.state!=='DRAFT'||c.session.patient_id)return false;const t=token(c),geo=document.defaultView?.navigator?.geolocation||globalThis.navigator?.geolocation;
    if(!geo){error(Error('위치를 가져올 수 없습니다. 위도·경도를 직접 입력하세요.'));return false;}
    localBusy=true;onBusy(true);paint();locationStatus.textContent='현재 위치 확인 중…';
    try{const position=await new Promise((resolve,reject)=>geo.getCurrentPosition(resolve,reject,{enableHighAccuracy:true,timeout:10000,maximumAge:0}));if(!same(t))return false;
      const {latitude,longitude,accuracy}=position.coords;if(!Number.isFinite(latitude)||!Number.isFinite(longitude)||Math.abs(latitude)>90||Math.abs(longitude)>180)throw Error('위치 값이 올바르지 않습니다.');
      form.lat=latitude.toFixed(6);form.lon=longitude.toFixed(6);syncForm();locationStatus.textContent=`GPS 위치 확인${Number.isFinite(accuracy)?' · 오차 약 '+Math.round(accuracy)+'m':''}`;onChange();return true;
    }catch(err){if(same(t))error(Error('위치를 확인하지 못했습니다. 좌표를 직접 입력하세요.'));return false;}
    finally{localBusy=false;onBusy(false);paint();}
  }
  function renderPatient(){
    const c=context(),pid=c?.session?.patient_id;patientInfo.replaceChildren();refs.links.hidden=!patient||patient.id!==pid;
    if(!patient||patient.id!==pid)return;
    patientInfo.append(make('strong',patient.id),make('p',`환자정보 v${patient.version} · ${patient.state==='WAITING'?'병원 탐색 대기':patient.state}`));
    refs.progress.href=patientNavigation(c.client.server,pid,c.actor);
    const handoff=new URL('/handoff-exchange.html',c.client.server);handoff.search=new URLSearchParams({role:'owner',actor_id:c.actor,pid,sid:c.session.id}).toString();refs.handoff.href=handoff.href;
    refs.handoff.hidden=referencesServer!==c.client.server||!capabilities?.handoff_exchange_revision;
    refs.change.hidden=!['CONFIRMED','IN_TRANSIT'].includes(patient.state);
    if(!refs.change.hidden){const url=new URL(patientNavigation(c.client.server,pid,c.actor,'/patient/transport-change.html'));if(patient.destination_hospital_id)url.searchParams.set('hid',patient.destination_hospital_id);refs.change.href=url.href;}
  }
  async function loadHospital(){
    const c=context();if(!c?.session?.patient_id||hospitalLoading||blocked())return false;const t=token(c),pid=c.session.patient_id,job={token:t};hospitalLoading=job;refresh.disabled=true;
    try{const [next,result]=await Promise.all([c.client.api('/api/patients/'+encodeURIComponent(pid)),c.client.api('/api/patients/'+encodeURIComponent(pid)+'/candidates')]);
      if(!same(t)||context()?.session?.patient_id!==pid)return false;patient=next;renderPatient();candidates.replaceChildren();
      for(const candidate of (result.candidates||[]).slice(0,6)){const seconds=candidate.travel?.duration_seconds;const travel=typeof seconds==='number'&&Number.isFinite(seconds)&&seconds>=0?`이동 약 ${Math.ceil(seconds/60)}분`:'이동시간 미확인';const item=make('article',undefined,null,'fw-candidate');item.append(make('h3',candidate.name),make('strong',travel),make('p',candidate.address||'주소 정보 없음'));
        for(const reason of (candidate.reasons||[]).slice(0,3))item.append(make('p',reason));const detail=make('details');detail.append(make('summary',`확인 항목 ${(candidate.checks||[]).length}개`));for(const check of candidate.checks||[])detail.append(make('p',check));item.append(detail);candidates.append(item);}
      if(!candidates.children.length)candidates.append(make('p','현재 조건으로 확인할 후보가 없습니다. 조건과 병원 정보를 다시 확인하세요.'));return true;
    }catch(err){if(same(t))error(Error('병원 후보를 불러오지 못했습니다. '+err.message));return false;}
    finally{if(hospitalLoading===job){hospitalLoading=null;paint();if(step==='hospital'&&!same(t,{version:false})&&context()?.session?.patient_id&&!blocked())void loadHospital();}}
  }
  function handleResult(result,operation){const c=context();if(!c?.session)return false;if(result?.patient&&result.patient.id===c.session.patient_id)patient=result.patient;
    if(c.session.patient_id&&c.session.state==='SUBMITTED'){setStep('hospital');renderPatient();if(!hospitalLoading)queueMicrotask(()=>loadHospital());}
    else if(operation==='confirm'||step==='review'&&c.session.confirmed&&result?.session?.confirmed)setStep('submit');return true;}
  function update(){
    const c=context(),t=token(c);if(!c)return;
    if(owner&&!(owner.client===t.client&&owner.actor===t.actor&&owner.epoch===t.epoch&&owner.recordId===t.recordId&&owner.sid===t.sid)){reset(false);}
    owner=t;renderFields();syncForm();renderPatient();if(c.session?.state==='SUBMITTED'&&c.session.patient_id){if(step!==null&&step!=='hospital')setStep('hospital');if(step==='hospital'&&(!patient||!candidates.children.length)&&!hospitalLoading&&!blocked())void loadHospital();}
    if(step==='submit'&&!c.session?.confirmed)setStep('review');paint();
  }
  function selectStep(next){const c=context();if(!c?.session||blocked()||!STEPS.has(next))return false;if(next==='submit'&&(!c.session.confirmed||form.editorDirty)||next==='hospital'&&!c.session.patient_id)return false;
    update();setStep(next);void loadReferences();if(next==='hospital')void loadHospital();return true;}
  function close(){if(blocked()||form.editorDirty)return false;return setStep(null);}
  function reset(notify=true){step=null;owner=null;patient=null;editorIdentity=null;editorVersion=null;customStatus='';loadingReferences=null;hospitalLoading=null;inputs.clear();form={lat:'',lon:'',consent:false,requiredCapabilities:[],editor:{},editorDirty:false};refs.fields.replaceChildren();refs['extra-fields'].replaceChildren();candidates.replaceChildren();syncForm();host.hidden=true;if(notify){onStep(null);onChange();}}
  function exportUIState(){const c=context();return {sessionId:c?.session?.id||null,version:form.editorDirty?editorVersion||0:c?.session?.version||0,step,lat:form.lat,lon:form.lon,consent:form.consent,requiredCapabilities:[...form.requiredCapabilities],editor:{...form.editor},editorDirty:form.editorDirty};}
  function restoreUIState(ui){const c=context();if(!ui||typeof ui!=='object'||ui.sessionId!==(c?.session?.id||null)||!Number.isSafeInteger(ui.version)||ui.version<0||ui.version>(c?.session?.version||0)||!([null,...STEPS].includes(ui.step))||typeof ui.lat!=='string'||ui.lat.length>128||typeof ui.lon!=='string'||ui.lon.length>128||typeof ui.consent!=='boolean'||!Array.isArray(ui.requiredCapabilities)||ui.requiredCapabilities.length>128||ui.requiredCapabilities.some(v=>typeof v!=='string'||v.length>256)||!ui.editor||typeof ui.editor!=='object'||Array.isArray(ui.editor)||Object.keys(ui.editor).some(key=>!Object.hasOwn(LABELS,key)||typeof ui.editor[key]!=='string'||ui.editor[key].length>16000)||typeof ui.editorDirty!=='boolean')return false;
    form={lat:ui.lat,lon:ui.lon,consent:ui.consent,requiredCapabilities:[...ui.requiredCapabilities],editor:{...ui.editor},editorDirty:ui.editorDirty};editorVersion=ui.version;editorIdentity=null;owner=token(c);step=c?.session?.state==='SUBMITTED'?'hospital':ui.step==='submit'&&!c?.session?.confirmed?'review':ui.step;
    renderFields();syncForm();paint();onStep(step);if(step!==null){void loadReferences();if(step==='hospital')void loadHospital();}return true;}
  return {open:selectStep,selectStep,close,update,reset,handleResult,hasUnapplied:()=>form.editorDirty,exportUIState,restoreUIState,exportUI:exportUIState,restoreUI:restoreUIState,getStage:()=>step,state:()=>({step,busy:localBusy||!!hospitalLoading,editorDirty:form.editorDirty,editorStale:dirtyStale(),canSubmit:canSubmit(),patient:patient?clone(patient):null})};
}
