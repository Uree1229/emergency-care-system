import {VoiceIntakeStore,validFlowToken,validPatientId} from './voice-intake-core.mjs';

const labels=[['conditions','기존 질환 언급'],['areas','통증 부위'],['symptoms','현재 증상 표시'],['onset','발생 시점 표시'],['transcript','환자·보호자 진술 원문']];
const signature=record=>record?JSON.stringify(record):'';
export function mountVoiceFlow({document,storage,now=()=>Date.now()}={}){
  const root=document.getElementById('voice-intake-summary');if(!root)return null;
  const store=new VoiceIntakeStore({storage,now}),flow=root.dataset.flowToken||'';
  const select=root.dataset.bindSelect?document.getElementById(root.dataset.bindSelect):null;
  const connect=document.getElementById('common-connect-form');
  const restored=connect?.querySelector('[name="voice_payload"]')?.value||'',restoredFlag=connect?.querySelector('[name="voice_confirmed"]')?.value||'';
  let shown='',attached='',reviewRequired=false,excluded=false;
  const patient=()=>select?select.value:root.dataset.patientId||'';
  const node=(tag,text)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;return el;};
  const hidden=(name,value)=>{
    if(!connect)return;let field=connect.querySelector(`[name="${name}"]`);
    if(!field){field=node('input');field.type='hidden';field.name=name;connect.appendChild(field);}field.value=value;
  };
  const clearPayload=()=>{hidden('voice_payload','');hidden('voice_confirmed','');};
  const eligible=(record,pid)=>record?.confirmed&&validPatientId(pid)&&record.patient_id===pid;
  function entryLinks(record,pid){
    for(const link of document.querySelectorAll?.('[data-voice-intake-link]')||[]){
      link.href=record&&(record.patient_id===null||record.patient_id===pid)?'/voice-intake?flow_token='+encodeURIComponent(flow)+(validPatientId(pid)?'&patient_id='+encodeURIComponent(pid):''):'/voice-intake';
    }
  }
  function render(record){
    clearPayload();root.replaceChildren();root.hidden=!flow;attached='';shown=signature(record);
    const pid=patient();entryLinks(record,pid);
    if(!flow)return;
    if(!validFlowToken(flow))root.appendChild(node('p','음성 문진 흐름을 확인하지 못해 음성 정보는 전달하지 않습니다.'));
    else if(!record)root.appendChild(node('p','이 흐름의 음성 보관본이 없거나 만료됐습니다. 음성 정보는 전달하지 않습니다.'));
    else if(record.patient_id!==null&&record.patient_id!==pid){
      root.appendChild(node('p','이 음성 문진은 다른 환자에 연결되어 있습니다. 현재 환자의 정보로 표시하거나 전달하지 않습니다. 새 음성 문진을 시작하세요.'));
    }else{
      root.appendChild(node('h3','음성 문진 · 사용자 확인용 보조 정보'));
      root.appendChild(node('p','진단이나 검증된 병력이 아닙니다. 의식·호흡·증상 선택이나 우선순위를 자동 변경하지 않습니다.'));
      for(const [key,label] of labels){const row=node('div'),title=node('strong',label+': '),value=node('span',record[key]);value.style.whiteSpace='pre-wrap';row.append(title,value);root.appendChild(row);}
      const edit=node('a','원문·표시 내용 수정 및 확인');edit.href='/voice-intake?flow_token='+encodeURIComponent(flow)+(validPatientId(pid)?'&patient_id='+encodeURIComponent(pid):'');root.appendChild(edit);
      if(!record.confirmed)root.appendChild(node('p','아직 내용 확인 전입니다. 음성 정보는 전송에 포함되지 않습니다.'));
      if(record.patient_id===null){
        const label=node('label'),check=node('input');check.type='checkbox';check.id='voice-patient-confirm';check.disabled=!validPatientId(pid)||!record.confirmed;
        label.append(check,node('span',' 선택 환자'+(validPatientId(pid)?` (${pid==='0'?'미등록 환자':pid})`:'')+'의 음성 정보임을 확인합니다.'));
        root.appendChild(label);
        check.addEventListener('change',()=>{
          if(!check.checked)return;
          const latest=store.load(flow);
          if(signature(latest)!==shown){reviewRequired=true;render(latest);return;}
          try{const bound=store.bind(flow,patient(),latest);render(bound);}catch(e){check.checked=false;clearPayload();root.appendChild(node('p',e.message));}
        });
      }else root.appendChild(node('p',`연결 대상: ${pid==='0'?'미등록 환자':`환자 ${pid}`} · 이 환자의 음성 정보임을 확인했습니다. 다른 환자에게 재사용하지 않습니다.`));
      if(eligible(record,pid)&&!reviewRequired&&!excluded){attached=JSON.stringify(record);hidden('voice_payload',attached);hidden('voice_confirmed','yes');}
    }
    if(reviewRequired){
      root.appendChild(node('p','음성 보관 내용이 변경되었거나 만료됐습니다. 접수 전에 최신 내용을 다시 확인하거나 음성 정보 제외를 직접 선택하세요.'));
      if(eligible(record,pid)){
        const accept=node('button','표시된 최신 음성 정보를 확인했습니다');accept.type='button';accept.id='voice-reconfirm';
        accept.addEventListener('click',()=>{
          const latest=store.load(flow);if(signature(latest)!==shown){render(latest);return;}
          reviewRequired=false;excluded=false;render(latest);
        });root.appendChild(accept);
      }
      const skip=node('button','음성 정보를 제외하고 접수');skip.type='button';skip.id='voice-exclude';
      skip.addEventListener('click',()=>{reviewRequired=false;excluded=true;render(store.load(flow));});root.appendChild(skip);
    }
    if(excluded)root.appendChild(node('p','이번 접수에서는 음성 정보를 제외하도록 직접 선택했습니다.'));
  }
  function sync(){render(store.load(flow));}
  select?.addEventListener('change',()=>{reviewRequired=false;excluded=false;sync();});
  connect?.addEventListener('submit',event=>{
    const latest=store.load(flow);
    if(excluded){clearPayload();return;}
    if(reviewRequired||(signature(latest)!==shown&&(attached||eligible(latest,patient())))){
      event.preventDefault();reviewRequired=true;render(latest);return;
    }
    // 같은 화면의 단일 스냅샷만 전송한다. 변경 없는 수동 흐름은 그대로 진행한다.
    render(latest);
  });
  const initial=store.load(flow);
  if((restored||restoredFlag)&&(!eligible(initial,patient())||JSON.stringify(initial)!==restored||restoredFlag!=='yes'))reviewRequired=true;
  render(initial);return {store,sync};
}
if(typeof document!=='undefined'){
  let storage=null;try{storage=window.sessionStorage;}catch{}
  mountVoiceFlow({document,storage});
}
