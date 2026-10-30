import {VoiceClient, requestId, normalizeServer} from './voice-client.mjs';
import {VoiceCapture} from './speech.mjs';

const $ = id => document.getElementById(id);
const labels = {age:'나이',age_group:'연령대',sex:'성별',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상',consciousness:'의식 상태',breathing:'호흡 상태',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',onset_at:'증상 발생 시각',blood_pressure:'혈압'};
const enums = {sex:{male:'남성',female:'여성',unknown:'미확인'},consciousness:{alert:'명료',impaired:'저하',unresponsive:'반응 없음',unknown:'미확인'},breathing:{normal:'정상',abnormal:'비정상',absent:'호흡 없음',unknown:'미확인'}};
const listFields = new Set(['symptoms','denied_symptoms','uncertain_symptoms']);
const storage = {get(key){try{return sessionStorage.getItem('paramedic.voice.'+key);}catch(_){return null;}},set(key,value){try{sessionStorage.setItem('paramedic.voice.'+key,value);}catch(_){}}};
const params = new URLSearchParams(location.search);
const actor = storage.get('actor') || 'crew-' + requestId();
storage.set('actor', actor);
let client, capabilities, catalog, currentPatient = null, tasks = 0, dirty = false, connecting = false, gpsBusy = false;
let captureMode = 'dictation', captureProvider = 'browser', finalCount = 0, startedAt = 0, ticker;
let canRead = VoiceCapture.capabilities().tts;

const node = (tag, text, className) => {const result=document.createElement(tag);if(text!==undefined)result.textContent=text;if(className)result.className=className;return result;};
function message(text, isError=false) {const target=$(isError?'error':'notice');target.textContent=text;target.hidden=!text;if(isError)$('notice').hidden=true;}
function clearError(){ $('error').hidden=true; $('error').textContent=''; }
function remember(){if(client?.session){storage.set('session',client.session.id);storage.set('server',client.server);}}
function draft(){return client?.session?.state==='DRAFT';}
function blocked(){return tasks>0 || connecting || gpsBusy || capture.active || !!client?.pending;}
function allowedSubmit(){
  if(!client?.session?.confirmed || !draft() || blocked() || dirty)return false;
  if(client.session.patient_id)return currentPatient?.state==='WAITING';
  return $('consent').checked && $('lat').value.trim()!=='' && $('lon').value.trim()!=='' &&
    Number.isFinite(Number($('lat').value)) && Math.abs(Number($('lat').value))<=90 &&
    Number.isFinite(Number($('lon').value)) && Math.abs(Number($('lon').value))<=180;
}
function controls(){
  const locked=blocked(), supported=VoiceCapture.capabilities();
  for(const id of ['connect-server','new-case','load-patient','sample-button'])$(id).disabled=locked||dirty;
  $('server-url').disabled=locked; $('resume-patient').disabled=locked;
  for(const id of ['send-transcript','send-command','voice-mode','speech-provider'])$(id).disabled=locked||!draft()||dirty;
  $('record-button').disabled = !capture.active && (locked||!draft()||dirty||($('speech-provider').value==='browser'?!supported.browser:!supported.recording||!capabilities?.speech?.configured));
  $('record-button').textContent=capture.active?'녹음 마치기':'음성 입력 시작';
  $('record-button').classList.toggle('is-recording',capture.active);
  $('record-button').setAttribute('aria-pressed',String(capture.active));
  $('save-fields').disabled=locked||!draft()||!dirty;
  if($('discard-fields'))$('discard-fields').disabled=locked||!dirty;
  $('undo-button').disabled=locked||!draft()||!client?.session?.can_undo||dirty;
  $('confirm-button').disabled=locked||!draft()||!Object.keys(client?.session?.fields||{}).length||dirty;
  $('submit-button').disabled=!allowedSubmit();
  $('readback-button').disabled=locked||!client?.session||dirty;
  $('readback-field').disabled=locked||!client?.session;
  $('refresh-session').disabled=tasks>0||capture.active||!client?.session||!!client?.pending||dirty;
  $('refresh-candidates').disabled=locked||!currentPatient;
  $('retry-request').hidden=!client?.pending;
  $('retry-request').disabled=tasks>0||capture.active;
  for(const control of $('fields-editor').querySelectorAll('input,select,textarea'))control.disabled=locked||!draft();
  $('change-kind').disabled=locked||!draft();
  for(const id of ['lat','lon','gps-button','use-demo-location','required-capabilities','consent'])$(id).disabled=locked||!draft()||!!client?.session?.patient_id;
}
function renderFields(){
  const values=client?.session?.fields||{};
  $('fields-editor').replaceChildren();
  const optional=node('details',undefined,'additional-fields');
  const extraGrid=node('div',undefined,'field-grid');
  optional.append(node('summary','추가 항목 입력'),extraGrid);
  const core=new Set(['age','sex','symptoms','consciousness','breathing']);
  for(const key of catalog?.supported_fields||[]){
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
    input.addEventListener('input',()=>{dirty=true;$('approval-status').textContent='수정 중 · 서버에 아직 반영되지 않았습니다';controls();});
    field.append(label,input);
    const spec=catalog?.terms?.measurements?.[key];
    if(spec)field.append(node('small',spec.unit));
    if(key==='onset_at')field.append(node('small','이 기기의 현지 시각 · 음성으로 “20분 전 발생”도 가능'));
    if(key==='age_group')field.append(node('small','예: 60대 · 정확한 나이로 추정하지 않음'));
    const evidence=client?.session?.field_metadata?.[key]?.evidence;
    if(evidence?.length){const detail=node('details');detail.append(node('summary','입력 근거'),node('p',evidence.join(' / ')));field.append(detail);}
    if(core.has(key)||value!==undefined)$('fields-editor').append(field);
    else extraGrid.append(field);
  }
  if(extraGrid.children.length)$('fields-editor').append(optional);
  dirty=false;
}
function render(){
  const s=client?.session;
  remember();renderFields();
  $('session-meta').textContent=s?`${s.patient_id?'환자 '+s.patient_id:'새 환자 초안'} · 버전 ${s.version}`:'새 기록을 준비하고 있습니다';
  $('approval-status').textContent=!s?'아직 기록 없음':s.state==='SUBMITTED'?'환자정보 반영 완료':s.confirmed?'현재 버전 확인 완료':'내용 확인이 필요합니다';
  const step=currentPatient?'hospital':s?.confirmed?'submit':Object.keys(s?.fields||{}).length?'review':'input';
  const steps={input:'01 음성으로 기록',review:'02 정보 확인·수정',submit:'03 확인 후 저장',hospital:'04 병원 후보 확인'};
  $('workflow-step').textContent=steps[step];
  for(const el of document.querySelectorAll('[data-step]'))el.classList.toggle('is-current',el.dataset.step===step);
  $('field-warnings').replaceChildren();
  if(s?.baseline_summary?.symptoms)$('field-warnings').append(node('p','기존 기록 증상: '+s.baseline_summary.symptoms,'baseline-note'));
  for(const text of s?.warnings||[])$('field-warnings').append(node('p',text,'warning-note'));
  $('transcript-history').replaceChildren();
  for(const u of (s?.utterances||[]).slice(-6).reverse()){
    const item=node('div',undefined,'history-item');item.append(node('small',u.mode==='command'?'음성 명령':'최종 인식문'),node('p',u.transcript));$('transcript-history').append(item);
  }
  if(!s?.utterances?.length)$('transcript-history').append(node('p','최종 인식문이 여기에 남습니다.','empty-state'));
  controls();
}
async function task(action){
  tasks++;controls();
  try{const result=await action();render();return result;}
  catch(error){
    if(capture.active)capture.abort();
    if(error.status===409&&client?.session&&!client.pending){try{await client.sync();render();}catch(_){}}
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
  currentPatient=null;dirty=false;$('candidate-list').replaceChildren(node('p','환자정보를 저장하면 후보 병원을 확인할 수 있습니다.','empty-state'));
  $('patient-result').replaceChildren();$('transcript-input').value='';$('command-input').value='';$('readback-text').textContent='';$('interim-text').textContent='';$('history-list').replaceChildren();
  $('consent').checked=false;$('lat').value='';$('lon').value='';$('location-status').textContent='현재 위치를 확인해 주세요';
  for(const option of $('required-capabilities').options)option.selected=false;
}
async function connect(){
  if(blocked())return;
  connecting=true;controls();
  try{
    const server=normalizeServer($('server-url').value.trim());
    const next=new VoiceClient({server,actorId:actor});
    const [caps,terms,hospitals]=await Promise.all([next.api('/api/voice/capabilities'),next.api('/api/voice/terms'),next.api('/api/hospitals')]);
    client=next;capabilities=caps;catalog=terms;
    $('connection-status').textContent='서버 연결됨';$('connection-status').dataset.state='connected';
    $('required-capabilities').replaceChildren();
    for(const name of [...new Set(hospitals.flatMap(h=>h.capabilities||[]))].sort())$('required-capabilities').add(new Option(name,name));
    $('readback-field').replaceChildren(new Option('전체 요약',''));
    for(const field of terms.readback_fields||terms.supported_fields)$('readback-field').add(new Option(labels[field]||field,field));
    const supported=VoiceCapture.capabilities();
    $('speech-provider').value=supported.browser?'browser':'azure';
    $('speech-provider').querySelector('[value="browser"]').disabled=!supported.browser;
    $('speech-provider').querySelector('[value="azure"]').disabled=!caps.speech.configured||!supported.recording;
    $('capabilities-note').textContent=caps.speech.configured?'필요 진료 분야는 직접 확인해 선택하세요. 녹음 파일 인식 사용 가능.':'필요 진료 분야는 직접 확인해 선택하세요. 서버 음성인식 키 미설정 · 지원 브라우저의 실시간 인식 또는 문장 입력을 사용하세요.';
    resetCase();
    const saved=storage.get('server')===server?storage.get('session'):null;
    if(saved){try{await next.sync(saved);}catch(_){await next.create();}}
    else await next.create();
    if(next.session.patient_id){currentPatient=await next.api('/api/patients/'+encodeURIComponent(next.session.patient_id));renderPatient();await candidates();}
    render();message('음성으로 말하거나 인식문을 입력해 기록을 시작하세요.');
  }catch(error){$('connection-status').textContent='연결 확인 필요';$('connection-status').dataset.state='error';message(error.message,true);}
  finally{connecting=false;controls();}
}
const capture=new VoiceCapture({
  onInterim(text){$('interim-text').textContent=text||'듣고 있습니다…';},
  onFinal(text){
    finalCount++;$('transcript-input').value=text;$('interim-text').textContent='최종 문장 확인 · 서버 처리 중';
    const extra={utterance_id:requestId(),text,is_final:true,mode:captureMode};
    task(async()=>{
      // A command ends listening before TTS so the app cannot transcribe its own reply.
      if(captureMode==='command'&&capture.active)await capture.stop();
      const result=await client.write('transcripts',extra);
      message('음성 기록을 초안에 반영했습니다.');
      await commandFeedback(result);return result;
    }).then(history).catch(()=>{});
  },
  onState(state){
    if(state==='limit'){finishRecording().catch(()=>{});return;}
    if(state==='idle'){clearInterval(ticker);$('recording-state').textContent='마이크 꺼짐';}
    else $('recording-state').textContent=state==='listening'?'실시간 인식 중':'녹음 중 · 종료 후 텍스트 변환';
    controls();
  },
  onError(error){clearInterval(ticker);message(error.message,true);controls();}
});
async function finishRecording(){
  if(!capture.active)return;
  const audio=await capture.stop();clearInterval(ticker);controls();
  if(audio){await write('audio',{...audio,utterance_id:requestId(),mode:captureMode});message('녹음 결과를 확인하고 잘못 인식된 값을 수정해 주세요.');await history();}
  else if(finalCount===0)message('최종 인식문이 없습니다. 마이크 권한과 음성인식 지원을 확인하거나 문장을 입력해 주세요.');
}
bindAsync('record-button',async()=>{
  if(capture.active){await finishRecording();return;}
  capture.stopSpeaking();captureMode=$('voice-mode').value;captureProvider=$('speech-provider').value;finalCount=0;
  await capture.start(captureProvider);startedAt=Date.now();controls();
  if(!capture.active)return;
  ticker=setInterval(()=>{$('recording-state').textContent=`${captureProvider==='browser'?'실시간 인식':'녹음'} · ${Math.floor((Date.now()-startedAt)/1000)}초`;},1000);
});
bindAsync('connect-server',connect);
bindAsync('new-case',()=>task(async()=>{capture.abort();capture.stopSpeaking();resetCase();await client.create();message('새 환자 초안을 만들었습니다.');}));
bindAsync('load-patient',()=>task(async()=>{
  const id=$('resume-patient').value.trim();if(!/^[\w-]+$/.test(id))throw Error('불러올 환자 ID를 입력하세요.');
  const patient=await client.api('/api/patients/'+encodeURIComponent(id));
  resetCase();await client.create(id);currentPatient=patient;renderPatient();
  if(patient.state!=='WAITING')message('목적지가 확정된 건은 현재 읽기·초안 작성만 가능합니다.');
}));
bindAsync('send-transcript',async()=>{await write('transcripts',{utterance_id:requestId(),text:$('transcript-input').value,is_final:true,mode:'dictation'});message('문장 내용을 초안에 반영했습니다.');await history();});
bindAsync('send-command',async()=>{await task(async()=>{const result=await client.write('commands',{text:$('command-input').value});message('명령을 처리했습니다.');await commandFeedback(result);});await history();});
$('fields-form').addEventListener('submit',event=>{
  event.preventDefault();clearError();
  const changes={},unset=[];
  try{
    for(const input of $('fields-editor').querySelectorAll('[data-field]')){
      const key=input.dataset.field,raw=input.value.trim(),old=client.session.fields[key];
      if(!raw){if(old!==undefined)unset.push(key);continue;}
      const value=listFields.has(key)?raw.split(/[,\n]/).map(v=>v.trim()).filter(Boolean):enums[key]||key==='age_group'?raw:key==='onset_at'?Math.floor(new Date(raw).getTime()/1000):Number(raw);
      if(JSON.stringify(value)!==JSON.stringify(old))changes[key]=value;
    }
    if(!Object.keys(changes).length&&!unset.length){dirty=false;render();return;}
    write('fields',{changes,unset_fields:unset,change_kind:$('change-kind').value},'PATCH').then(()=>{message('수정 내용을 초안에 반영했습니다. 다시 확인해 주세요.');return history();}).catch(()=>{});
  }catch(error){message(error.message,true);}
});
if($('discard-fields'))bindAsync('discard-fields',()=>{dirty=false;render();});
bindAsync('undo-button',async()=>{await write('undo');message('직전 변경을 되돌렸습니다.');await history();});
bindAsync('refresh-session',()=>task(async()=>{await client.sync();await history();message('최신 초안을 불러왔습니다.');}));
bindAsync('retry-request',()=>task(async()=>{
  const result=await client.retry();
  if(result.patient){currentPatient=result.patient;renderPatient();await candidates();}
  message('같은 요청으로 처리 결과를 확인했습니다.');
}));
bindAsync('readback-button',()=>task(async()=>{
  const result=await client.readback($('readback-field').value);$('readback-text').textContent=result.speak_text;
  if(canRead)capture.speak(result.speak_text);else message('이 브라우저는 읽어주기를 지원하지 않습니다. 화면에서 내용을 확인하세요.');
}));
bindAsync('stop-speaking',()=>capture.stopSpeaking());
bindAsync('confirm-button',async()=>{await write('confirm',{confirmed:true});message('현재 초안을 확인했습니다. 위치와 동의를 확인한 뒤 저장하세요.');});
bindAsync('submit-button',()=>task(async()=>{
  if(!client.session.confirmed)throw Error('현재 초안을 먼저 확인하세요.');
  const context=client.session.patient_id?{}:{consent:$('consent').checked,lat:Number($('lat').value),lon:Number($('lon').value),required_capabilities:[...$('required-capabilities').selectedOptions].map(o=>o.value),resource_demands:{ed_bed:1}};
  const result=await client.write('submit',{context,start_inquiry:false});currentPatient=result.patient;renderPatient();await candidates();
  message(result.operational_effect==='voice_record_saved_no_patient_change'?'기록 확인을 저장했습니다. 기존 병원 응답은 유지됩니다.':'환자정보를 서버에 저장했습니다. 후보 병원의 수용 확인은 별도로 필요합니다.');
}));
function renderPatient(){
  $('patient-result').replaceChildren();if(!currentPatient)return;
  $('patient-result').append(node('span','저장된 환자','badge'),node('strong',currentPatient.id),node('p',`환자정보 v${currentPatient.version} · ${currentPatient.state==='WAITING'?'병원 탐색 대기':currentPatient.state}`));
  if(currentPatient.lat!==null&&currentPatient.lon!==null){$('lat').value=currentPatient.lat;$('lon').value=currentPatient.lon;$('location-status').textContent='저장된 환자 위치';}
}
async function candidates(){
  if(!currentPatient)return;
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
bindAsync('use-demo-location',()=>{$('lat').value='35.1595';$('lon').value='126.8526';$('location-status').textContent='광주 시연 좌표 · 실제 GPS 위치가 아닙니다';controls();});
bindAsync('gps-button',()=>new Promise((resolve,reject)=>{
  if(!navigator.geolocation){const error=Error('이 브라우저에서 위치를 가져올 수 없습니다. 좌표를 직접 입력하세요.');message(error.message,true);reject(error);return;}
  gpsBusy=true;controls();$('location-status').textContent='현재 위치 확인 중…';
  navigator.geolocation.getCurrentPosition(position=>{$('lat').value=position.coords.latitude.toFixed(6);$('lon').value=position.coords.longitude.toFixed(6);$('location-status').textContent=`GPS 위치 확인 · 오차 약 ${Math.round(position.coords.accuracy)}m`;gpsBusy=false;controls();resolve();},()=>{message('위치 권한이나 연결을 확인하고 좌표를 직접 입력하세요.',true);gpsBusy=false;controls();reject(Error('위치 확인 실패'));},{enableHighAccuracy:true,timeout:10000,maximumAge:0});
}));
for(const id of ['lat','lon','consent'])$(id).addEventListener('input',controls);
bindAsync('sample-button',()=>{$('transcript-input').value='65세 남성. 흉통. 호흡곤란 없음. 의식 명료. 혈압 110에 70. 맥박 90. 산소포화도 97. 증상은 20분 전 발생.';message('시연 문장을 넣었습니다. 음성 인식 결과가 아닙니다. 문장 반영을 눌러 시험하세요.');});
window.addEventListener('pagehide',()=>{capture.abort();capture.stopSpeaking();clearInterval(ticker);});
$('server-url').value=params.get('server')||location.origin;
$('recording-state').textContent='마이크 꺼짐';
connect();
