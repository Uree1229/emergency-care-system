import {DISPLAY_FIELDS,MAX_TRANSCRIPT,VoiceIntakeStore,extractVoiceDisplay,newFlowToken,validFlowToken,validPatientId} from './voice-intake-core.mjs';

export function mountVoiceIntake({document,window,storage,now=()=>Date.now(),makeToken=()=>newFlowToken()}={}){
  const $=id=>document.getElementById(id),input=$('transcript'),status=$('status'),next=$('next');
  const store=new VoiceIntakeStore({storage,now});
  const params=new URL(window.location.href).searchParams,requested=params.get('flow_token'),contextPatient=params.get('patient_id');
  let record=validFlowToken(requested)?store.load(requested):null;
  const freshToken=avoid=>{for(let i=0;i<4;i++){const value=makeToken();if(validFlowToken(value)&&value!==avoid&&!store.load(value))return value;}throw new Error('새 음성 문진 흐름을 만들지 못했습니다. 다시 시도하세요.');};
  const wrongPatient=contextPatient!==null&&(!validPatientId(contextPatient)||record?.patient_id!==null&&record?.patient_id!==undefined&&record.patient_id!==contextPatient);
  if(wrongPatient)record=null;
  let token=record?.flow_token||freshToken(requested),recognition=null,analysisReady=!!record&&!!record.transcript.trim();
  const announce=text=>{status.textContent=text;};
  const setUrl=()=>{const url=new URL(window.location.href);url.searchParams.set('flow_token',token);if(record?.patient_id!==null&&record?.patient_id!==undefined)url.searchParams.set('patient_id',record.patient_id);else url.searchParams.delete('patient_id');window.history?.replaceState(null,'',url);};
  const disableNext=()=>{next.setAttribute('aria-disabled','true');next.classList.add('disabled');$('voice-confirm').checked=false;};
  const updateControls=()=>{
    $('start').disabled=!!recognition||!SR;$('stop').disabled=!recognition;input.readOnly=!!recognition;
    $('analyze').disabled=!!recognition;$('confirm').disabled=!!recognition||!analysisReady;$('voice-confirm').disabled=!!recognition||!analysisReady;
  };
  const fields=()=>Object.fromEntries(DISPLAY_FIELDS.map(k=>[k,$(k).value]));
  const save=confirmed=>{
    const updated={version:2,flow_token:token,created_at:record?.created_at??now(),updated_at:now(),patient_id:record?.patient_id??null,transcript:input.value,...fields(),confirmed};
    record=updated;
    try{record=store.save(updated);return true;}catch(e){disableNext();announce(e.message);return false;}
  };
  const invalidate=()=>{
    disableNext();if(!record)return true;
    const saved=save(false);if(!saved)store.remove(token);return saved;
  };
  const enableNext=()=>{next.href='/emergency?flow_token='+encodeURIComponent(token);next.setAttribute('aria-disabled','false');next.classList.remove('disabled');};
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  setUrl();disableNext();
  if(record){input.value=record.transcript;for(const k of DISPLAY_FIELDS)$(k).value=record[k];$('result').hidden=false;if(record.confirmed){$('voice-confirm').checked=true;enableNext();}}
  if($('bound-patient'))$('bound-patient').textContent=record?.patient_id!==null&&record?.patient_id!==undefined?`연결 대상: ${record.patient_id==='0'?'미등록 환자':`환자 ${record.patient_id}`} · 다른 환자에게 재사용하지 않습니다.`:'환자 대상은 다음 단계에서 별도로 확인합니다.';
  if(!SR)announce('이 브라우저는 음성인식을 지원하지 않습니다. 텍스트를 직접 입력할 수 있습니다.');
  else if(!record)announce('대기 중');
  if(wrongPatient)announce('다른 환자의 음성 문진을 불러오지 않았습니다. 새로운 흐름에서 문진을 시작하세요.');
  updateControls();
  for(const field of [input,...DISPLAY_FIELDS.map(k=>$(k))])field.addEventListener('beforeinput',event=>{
    if(!invalidate()){event.preventDefault();field.readOnly=true;}
  });
  input.addEventListener('input',()=>{analysisReady=false;invalidate();$('result').hidden=true;updateControls();});
  for(const key of DISPLAY_FIELDS)$(key).addEventListener('input',()=>{invalidate();});
  $('analyze').addEventListener('click',()=>{
    if(recognition)return;
    if(!input.value.trim()||input.value.length>MAX_TRANSCRIPT){disableNext();announce(`원문을 1~${MAX_TRANSCRIPT}자 범위로 입력해 주세요.`);return;}
    if(record&&!store.load(token)){
      token=freshToken(token);record=null;setUrl();
    }
    const display=extractVoiceDisplay(input.value);
    for(const key of DISPLAY_FIELDS)$(key).value=display[key];
    $('alert').hidden=!display.alert;$('review-note').hidden=!display.reviewNeeded;$('result').hidden=false;
    analysisReady=true;disableNext();updateControls();
    if(save(false))announce(display.reviewNeeded?'부정·과거·불확실하거나 대상이 모호한 문장은 분류를 보류했습니다. 원문과 표시 내용을 확인·수정해 주세요.':'표시 내용을 확인·수정한 뒤 확인 버튼을 눌러 주세요.');
  });
  $('confirm').addEventListener('click',()=>{
    if(recognition||!analysisReady||!$('voice-confirm').checked){announce('원문과 표시 내용을 확인하고 확인란을 선택해 주세요.');return;}
    if(save(true)){enableNext();announce('내용을 확인했습니다. 다음 화면에서 이 음성 정보의 환자를 별도로 확인합니다.');}
  });
  next.addEventListener('click',event=>{
    const durable=store.load(token);
    if(next.getAttribute('aria-disabled')==='true'||!durable?.confirmed||JSON.stringify(durable)!==JSON.stringify(record)){
      event.preventDefault();disableNext();announce('보관 정보가 만료되거나 변경됐습니다. 원문을 다시 분석·확인해 주세요.');
    }
  });
  $('start').addEventListener('click',()=>{
    if(!SR||recognition)return;
    if(!invalidate())return;analysisReady=false;$('result').hidden=true;
    const engine=new SR(),prefix=input.value;recognition=engine;
    engine.lang='ko-KR';engine.continuous=true;engine.interimResults=true;
    const finish=()=>{if(recognition!==engine)return;recognition=null;updateControls();};
    engine.onstart=()=>{if(recognition===engine)announce('듣고 있습니다. 끝나면 중지를 누르고 문장을 확인하세요.');};
    engine.onresult=event=>{
      if(recognition!==engine)return;
      const parts=[];
      for(let i=0;i<event.results.length;i++){const text=event.results[i]?.[0]?.transcript;if(typeof text==='string')parts.push(text);}
      input.value=prefix+(prefix&&parts.length?' ':'')+parts.join(' ');invalidate();
    };
    engine.onerror=event=>{if(recognition!==engine)return;finish();announce('음성인식 오류: '+event.error+' · 마이크 권한을 확인하거나 텍스트를 직접 입력하세요.');};
    engine.onend=()=>{if(recognition!==engine)return;finish();announce('음성인식 종료. 원문을 확인하고 분석하세요.');};
    updateControls();try{engine.start();}catch(e){finish();announce('음성인식을 시작하지 못했습니다: '+e.message);}
  });
  $('stop').addEventListener('click',()=>{try{recognition?.stop();}catch(e){announce('음성인식을 중지하지 못했습니다: '+e.message);}});
  $('clear').addEventListener('click',()=>{
    const engine=recognition;recognition=null;try{engine?.abort();}catch{}
    const removed=store.remove(token);token=freshToken(token);record=null;analysisReady=false;setUrl();input.value='';$('result').hidden=true;disableNext();updateControls();
    announce(removed?'초기화했습니다. 새로운 음성 문진 흐름입니다.':'기존 보관본의 삭제를 확인하지 못했습니다. 새 흐름으로 분리했으며 이전 정보는 전달하지 않습니다.');
  });
  return {store,get token(){return token;}};
}

if(typeof document!=='undefined'&&document.getElementById('transcript')){
  let storage=null;try{storage=window.sessionStorage;}catch{}
  try{mountVoiceIntake({document,window,storage});}catch(e){document.getElementById('status').textContent=e.message;}
}
