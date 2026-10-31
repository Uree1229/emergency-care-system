// Hospital-scoped operational records. Calls never confirm clinical acceptance.
export const OUTCOMES = {can_accept:'수용 가능하다고 전달받음',cannot_accept:'수용 어렵다고 전달받음',checking:'확인 중이라고 전달받음',additional_info:'추가 정보 요청',no_answer:'통화 연결 안 됨',callback_requested:'재통화 요청',other:'기타'};
export const KINDS = {request_review:'환자 요청 검토',request_view:'문의 확인',respond:'응답 작성',resource_update:'해당 건 자원 확인·수정',additional_question:'추가 질문·답변',recheck:'수용 조건 재확인',chat:'이송 대화',call:'해당 건 통화',patient_input:'환자정보 보완'};
const enc = encodeURIComponent;
const uuid = () => globalThis.crypto?.randomUUID?.() ?? `op-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => value == null ? '미기록' : new Date(value*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});

export class HospitalOperations {
  constructor({request,storage,makeId=uuid,monotonic=()=>performance.now()}) {
    this.request=request;this.storage=storage;this.makeId=makeId;this.monotonic=monotonic;
    this.context=null;this.generation=0;this.patient='';this.inflight=new Map();this.loadSerial={calls:0,work:0,external:0,provenance:0};this.resetData();
  }
  resetData(){this.calls={records:[]};this.work={activities:[]};this.summary=null;this.external={external_arrivals:[]};this.provenance=null;this.workFetchedMono=this.monotonic();}
  key(name,ctx=this.context){return `capstone.hospital.ops.v1:${enc(JSON.stringify([ctx.server,ctx.hospital,name.startsWith('pending:external-')?'external_shared':ctx.handler]))}:${name}`;}
  read(key){const raw=this.storage.getItem(key);return raw==null?null:JSON.parse(raw);}
  save(key,value){try{this.storage.setItem(key,JSON.stringify(value));}catch{throw Error('재시도 요청을 저장할 수 없습니다. 브라우저 저장소를 확인한 뒤 다시 시도하세요.');}}
  select(context){
    const signature=JSON.stringify([context.server,context.hospital,context.handler,context.version]);
    if(signature===this.signature)return false;
    this.signature=signature;this.context={...context};this.generation++;this.resetData();
    let actor=this.read(this.key('actor'));
    if(!actor){actor=`hospital-${this.makeId()}`;this.save(this.key('actor'),actor);}
    this.context.actor=actor;this.patient=this.read(this.key('patient')) || '';return true;
  }
  setPatient(pid){if(pid===this.patient)return;this.patient=String(pid).trim();this.save(this.key('patient'),this.patient);this.work={activities:[]};this.summary=null;}
  stamp(){return {generation:this.generation,patient:this.patient,context:{...this.context}};}
  current(stamp,patient=false){return stamp.generation===this.generation&&(!patient||stamp.patient===this.patient);}
  hospitalBase(ctx=this.context){return `/api/hospitals/${enc(ctx.hospital)}`;}
  patientBase(pid=this.patient,ctx=this.context){if(!pid)throw Error('먼저 이송 건 ID를 선택하세요.');return `${this.hospitalBase(ctx)}/patients/${enc(pid)}`;}
  pending(channel){return this.read(this.key('pending:'+channel));}
  async perform(channel,path,makePayload){
    const stamp=this.stamp(),key=this.key('pending:'+channel,stamp.context);
    if(this.inflight.has(key))return this.inflight.get(key);
    let saved=this.read(key);
    if(!saved){saved={path,payload:makePayload(),context:stamp.context};this.save(key,saved);}
    // A stored retry uses the exact original path/body, including its version.
    const promise=(async()=>{
      try{const value=await this.request(saved.context,saved.path,{method:'POST',body:JSON.stringify(saved.payload)});
        const item=value.record||value.activity||value.external_arrival;
        if(item?.hospital_id&&item.hospital_id!==saved.context.hospital)throw Error('다른 병원 응답이므로 처리 결과를 표시하지 않았습니다.');
        this.storage.removeItem(key);return {value,stale:!this.current(stamp)};
      }catch(error){error.pending=!(error.status>=400&&error.status<500);error.requestChannel=channel;
        if(!error.pending)this.storage.removeItem(key);throw error;}
      finally{this.inflight.delete(key);}
    })();this.inflight.set(key,promise);return promise;
  }
  clearRejected(channel){const key=this.key('pending:'+channel);if(this.inflight.has(key))throw Error('전송이 끝난 뒤 다시 확인하세요.');this.storage.removeItem(key);}
  async loadCalls({before=null,status='all'}={}){
    const stamp=this.stamp(),serial=++this.loadSerial.calls;const query=new URLSearchParams({limit:'30',confirmation_status:status,current_only:'false'});if(before)query.set('before_id',before);
    const value=await this.request(stamp.context,`${this.hospitalBase(stamp.context)}/call-records?${query}`);
    if(!this.current(stamp)||serial!==this.loadSerial.calls)return false;
    if(value.scope?.hospital_id!==stamp.context.hospital||value.records.some(r=>r.hospital_id!==stamp.context.hospital))throw Error('다른 병원의 통화 응답은 표시하지 않았습니다.');
    for(const record of value.records){
      const channel=`confirm:${record.patient_id}:${record.id}`,pending=this.pending(channel);
      if(pending&&record.confirmation?.actor_id===stamp.context.actor&&record.confirmation.decision===pending.payload.decision)this.storage.removeItem(this.key('pending:'+channel));
      const call=this.pending(`call:${record.patient_id}`);
      if(call&&record.actor_id===stamp.context.actor&&record.client_record_id===call.payload.client_record_id)this.storage.removeItem(this.key('pending:call:'+record.patient_id));
    }
    this.calls={...value,records:before?[...this.calls.records,...value.records]:value.records};return true;
  }
  async loadWork({before=null}={}){
    if(!this.patient)return false;const stamp=this.stamp(),serial=++this.loadSerial.work,base=this.patientBase(stamp.patient,stamp.context);
    const query=new URLSearchParams({actor_id:stamp.context.actor,limit:'30'});if(before)query.set('before_id',before);
    const [value,summary]=await Promise.all([this.request(stamp.context,`${base}/work-activities?${query}`),this.request(stamp.context,`${base}/work-activities/summary?actor_id=${enc(stamp.context.actor)}`)]);
    if(!this.current(stamp,true)||serial!==this.loadSerial.work)return false;
    for(const result of [value,summary])if(result.scope?.hospital_id!==stamp.context.hospital||result.scope?.patient_id!==stamp.patient)throw Error('계측 응답의 병원·이송 건이 달라 표시하지 않았습니다.');
    if(value.activities.some(a=>a.hospital_id!==stamp.context.hospital||a.patient_id!==stamp.patient||a.actor_id!==stamp.context.actor))throw Error('다른 작업자의 계측 응답은 표시하지 않았습니다.');
    const start=this.pending(`work-start:${stamp.patient}`);
    if(start&&value.activities.some(a=>a.client_activity_id===start.payload.client_activity_id))this.storage.removeItem(this.key(`pending:work-start:${stamp.patient}`));
    for(const activity of value.activities)if(activity.state==='CLOSED')this.storage.removeItem(this.key(`pending:work-stop:${stamp.patient}:${activity.id}`));
    this.work={...value,activities:before?[...this.work.activities,...value.activities]:value.activities};this.summary=summary;this.workFetchedMono=this.monotonic();return true;
  }
  async loadExternal({before=null,state='ACTIVE'}={}){
    const stamp=this.stamp(),serial=++this.loadSerial.external,query=new URLSearchParams({state,limit:'30'});if(before)query.set('before_id',before);
    const value=await this.request(stamp.context,`${this.hospitalBase(stamp.context)}/external-arrivals?${query}`);
    if(!this.current(stamp)||serial!==this.loadSerial.external)return false;
    if(value.external_arrivals.some(row=>row.hospital_id!==stamp.context.hospital))throw Error('다른 병원의 외부 유입 응답은 표시하지 않았습니다.');
    this.external={...value,external_arrivals:before?[...this.external.external_arrivals,...value.external_arrivals]:value.external_arrivals};return true;
  }
  async loadProvenance(){const stamp=this.stamp(),serial=++this.loadSerial.provenance;const result=await this.request(stamp.context,`${this.hospitalBase(stamp.context)}/data-provenance`);if(!this.current(stamp)||serial!==this.loadSerial.provenance)return false;
    if(result.hospitals?.some(h=>h.hospital_id!==stamp.context.hospital))throw Error('다른 병원의 출처 응답은 표시하지 않았습니다.');this.provenance=result;return true;}
  createCall(fields){const pid=this.patient,ctx={...this.context};return this.perform(`call:${pid}`,`${this.patientBase(pid,ctx)}/call-records`,()=>({...fields,actor_id:ctx.actor,client_record_id:this.makeId()}));}
  confirmCall(record,decision,reason=''){const ctx={...this.context};if(record.hospital_id!==ctx.hospital)throw Error('현재 병원 기록만 내용 확인할 수 있습니다.');return this.perform(`confirm:${record.patient_id}:${record.id}`,`${this.patientBase(record.patient_id,ctx)}/call-records/${record.id}/confirmation`,()=>({actor_id:ctx.actor,client_confirmation_id:this.makeId(),decision,...(reason?{reason}:{})}));}
  startWork(kind){const pid=this.patient,ctx={...this.context};if(!KINDS[kind])throw Error('업무 종류를 선택하세요.');return this.perform(`work-start:${pid}`,`${this.patientBase(pid,ctx)}/work-activities`,()=>({actor_id:ctx.actor,client_activity_id:this.makeId(),kind}));}
  stopWork(activity){const ctx={...this.context};if(activity.actor_id!==ctx.actor||activity.hospital_id!==ctx.hospital||activity.patient_id!==this.patient)throw Error('현재 담당자의 선택한 이송 건만 종료할 수 있습니다.');return this.perform(`work-stop:${activity.patient_id}:${activity.id}`,`${this.patientBase(activity.patient_id,ctx)}/work-activities/${activity.id}/stop`,()=>({actor_id:ctx.actor}));}
  registerExternal(fields){const ctx={...this.context};return this.perform('external-register',`${this.hospitalBase(ctx)}/external-arrivals`,()=>({...fields,handled_by:ctx.handler,request_id:this.makeId()}));}
  dischargeExternal(record){const ctx={...this.context};if(record.hospital_id!==ctx.hospital||record.state!=='ACTIVE')throw Error('현재 병원의 활성 유입 기록만 개별 퇴실할 수 있습니다.');return this.perform(`external-discharge:${record.id}`,`${this.hospitalBase(ctx)}/external-arrivals/${record.id}/discharge`,()=>({expected_version:record.version,handled_by:ctx.handler}));}
  elapsed(activity){return activity.state==='OPEN'?Math.max(0,Math.floor(activity.open_elapsed_seconds+Math.max(0,this.monotonic()-this.workFetchedMono)/1000)):activity.completed_seconds;}
}

export class HospitalOperationsPanel {
  constructor({root,externalRoot,client,context,notice,onBusy=()=>{},onChanged=()=>{}}){
    this.root=root;this.externalRoot=externalRoot;this.client=client;this.getContext=context;this.notice=notice;this.onBusy=onBusy;this.onChanged=onChanged;this.busy=false;this.loading=false;this.visible=false;
    this.callStatus='all';this.externalState='ACTIVE';this.draft={started:'',ended:'',outcome:'checking',counterparty:'',note:'',supersedes:''};this.kind='request_review';this.error='';this.backgroundSignature=null;
    root.addEventListener('submit',e=>this.submit(e));root.addEventListener('click',e=>this.click(e));root.addEventListener('input',e=>this.input(e));root.addEventListener('change',e=>this.input(e));
    externalRoot.addEventListener('click',e=>this.externalClick(e));externalRoot.addEventListener('change',e=>{if(e.target.id==='opsExternalState'){this.externalState=e.target.value;this.refreshExternal();}});
  }
  sync(){const changed=this.client.select(this.getContext());if(changed){this.error='';this.backgroundSignature=null;this.draft={started:'',ended:'',outcome:'checking',counterparty:'',note:'',supersedes:''};this.client.resetData();this.render();this.renderExternal();}return changed;}
  async show(pid=''){this.sync();this.visible=true;if(pid&&!this.client.patient)this.client.setPatient(pid);this.render();await this.refresh();}
  hide(){this.visible=false;}
  editing(){const active=this.root.ownerDocument?.activeElement;return !!active&&this.root.contains(active)&&['INPUT','SELECT','TEXTAREA'].includes(active.tagName);}
  readSignature(){const c=this.client;return JSON.stringify({calls:c.calls,work:c.work,totals:c.summary?.totals,provenance:c.provenance?.hospitals,error:this.error},
    (key,value)=>['measured_at','age_seconds','open_elapsed_seconds','open_raw_elapsed_seconds','open_elapsed_union_seconds'].includes(key)?undefined:value);}
  async refresh({background=false}={}){if(this.loading)return;this.sync();this.loading=true;const generation=this.client.generation;
    try{const outcomes=await Promise.allSettled([this.client.loadCalls({status:this.callStatus}),this.client.patient?this.client.loadWork():Promise.resolve(),this.client.loadProvenance()]);if(generation!==this.client.generation)return;this.error=outcomes.filter(r=>r.status==='rejected').map(r=>r.reason.message).join(' · ');
      const signature=this.readSignature();
      // Background polls must retain the actual form nodes: restoring text and
      // focus after innerHTML replacement still destroys date pickers/selection.
      // Leave the signature unapplied so the next idle poll renders new data.
      if(background&&(this.editing()||signature===this.backgroundSignature)){this.tick();return;}
      this.render();this.backgroundSignature=signature;}
    finally{this.loading=false;}}
  async refreshExternal(){this.sync();const generation=this.client.generation;try{await this.client.loadExternal({state:this.externalState});if(generation===this.client.generation)this.renderExternal();}catch(e){if(generation===this.client.generation)this.externalRoot.textContent='외부 유입 목록 조회 실패: '+e.message;}}
  input(e){const name=e.target.dataset.draft;if(name)this.draft[name]=e.target.value;if(e.target.id==='opsKind')this.kind=e.target.value;if(e.target.id==='opsCallStatus'){this.callStatus=e.target.value;this.refresh();}}
  async action(callback,message){if(this.busy)return;this.sync();if(!this.client.context.handler){this.notice('담당자 이름을 먼저 입력하세요.',true);return;}this.busy=true;this.onBusy(true);this.render();this.renderExternal();const generation=this.client.generation;
    try{const result=await callback();if(result?.stale||generation!==this.client.generation)return;this.error='';this.notice(result?.value?.duplicate?'이미 처리된 같은 요청을 확인했습니다.':message,false);await this.onChanged();}
    catch(e){if(generation!==this.client.generation)return;this.error=e.message+(e.pending?' · 결과 미확인 요청은 같은 ID·본문으로 재시도합니다.':e.status?' · 요청이 거부되었습니다. 최신 기록과 입력을 확인한 뒤 직접 다시 시도하세요.':'');this.notice(this.error,true);if(e.status===409){if(e.requestChannel?.startsWith('external-'))await this.refreshExternal();else await this.refresh();}}
    finally{this.busy=false;this.onBusy(false);if(generation===this.client.generation){this.render();this.renderExternal();}}
  }
  confirmationControls(record,disabled){
    if(record.author_role!=='paramedic'||record.confirmation_status!=='unconfirmed'||record.superseded_by_id)return '';
    const pending=this.client.pending(`confirm:${record.patient_id}:${record.id}`);
    if(pending){const dispute=pending.payload.decision==='dispute';return `<button class="btn-small" data-ops-${dispute?'dispute':'confirm'}="${record.id}" ${disabled}>같은 ${dispute?'내용 이의':'내용 확인'} 요청 재시도</button>`;}
    return `<button class="btn-small" data-ops-confirm="${record.id}" ${disabled}>통화 기록 내용 확인</button><button class="btn-small" data-ops-dispute="${record.id}" ${disabled}>내용 이의</button>`;
  }
  render(){
    const c=this.client,ctx=c.context;if(!ctx)return;
    const active=this.root.ownerDocument?.activeElement,focus=active&&this.root.contains(active)?{id:active.id,draft:active.dataset.draft,start:active.selectionStart,end:active.selectionEnd}:null;
    const disabled=this.busy?'disabled':'',pending=c.patient?c.pending(`call:${c.patient}`):null;
    const fields=pending?.payload;const fieldDisabled=this.busy||pending?'disabled':'';
    const pendingStart=c.patient?c.pending('work-start:'+c.patient):null;if(pendingStart)this.kind=pendingStart.payload.kind;
    const kindDisabled=this.busy||pendingStart?'disabled':'';
    const options=Object.entries(OUTCOMES).map(([value,label])=>`<option value="${value}" ${(fields?.reported_outcome??this.draft.outcome)===value?'selected':''}>${label}</option>`).join('');
    const records=c.calls.records.map(r=>`<article class="resource-card" data-call-record="${r.id}"><div class="row"><b>통화 기록 #${r.id} · ${esc(r.patient_id)}</b><span class="tag t-gray">기록 내용 ${{unconfirmed:'미확인',confirmed:'확인됨',disputed:'이의 있음'}[r.confirmation_status]||esc(r.confirmation_status)}</span></div><p>${esc(OUTCOMES[r.reported_outcome]||r.reported_outcome)}</p><p class="note">작성: ${r.author_role==='hospital'?'병원':'구급대원'} · 보고된 통화 ${esc(date(r.call_started_at))} ~ ${esc(date(r.call_ended_at))} · 서버 저장 ${esc(date(r.recorded_at))}</p><div style="white-space:pre-wrap">${esc(r.note)}</div>${r.confirmation?.reason?`<p>내용 이의 사유: ${esc(r.confirmation.reason)}</p>`:''}${r.supersedes_id?`<p class="note">기록 #${r.supersedes_id}의 정정본</p>`:''}${r.superseded_by_id?`<p class="note">정정본 #${r.superseded_by_id} 있음 · 이 원문은 보존</p>`:''}<div class="row"><button class="btn-small" data-ops-patient="${esc(r.patient_id)}" ${disabled}>이 이송 건 선택</button>${this.confirmationControls(r,disabled)}${r.author_role==='hospital'&&r.actor_id===ctx.actor&&!r.superseded_by_id?`<button class="btn-small" data-ops-correct="${r.id}" ${disabled}>원문 보존·정정본 작성</button>`:''}</div></article>`).join('');
    const activities=c.work.activities.map(a=>`<li><b>${esc(KINDS[a.kind]||a.kind)}</b> · ${a.state==='OPEN'?'진행 중':'완료'} · <span data-ops-elapsed="${a.id}">${c.elapsed(a)}</span>초 · ${esc(date(a.started_at))}${a.ended_at?' ~ '+esc(date(a.ended_at)):''}${a.state==='OPEN'?` <button class="btn-small" data-ops-stop="${a.id}" ${disabled}>내 업무 종료</button>`:''}</li>`).join('');
    this.root.innerHTML=`<section class="resource-card"><h3>통화 기록·직접 업무 계측</h3><p class="note">병원 ${esc(ctx.hospital)} · 담당자 ${esc(ctx.handler||'미입력')} · 작업자 ID ${esc(ctx.actor)}</p><p><b>통화 내용 확인은 수용 응답이 아닙니다.</b> 수용·불가·목적지 변경은 기존 업무 버튼으로 별도 처리합니다.</p><form id="opsPatientForm" class="row"><label>이송 건 ID <input name="patient" value="${esc(c.patient)}" maxlength="128" required ${disabled}></label><button class="btn-small" ${disabled}>이송 건 조회</button><button type="button" class="btn-small" id="opsRefresh" ${disabled}>기록 새로고침</button></form><p role="status" class="note">${esc(this.error||'현재 병원에 공유된 통화 기록과 이송 건별 내 업무를 조회합니다.')}</p></section>
      <section class="resource-card"><h3>명시적 업무 시작·종료 ${c.patient?'· '+esc(c.patient):''}</h3><p class="note">실제 검토·응답·통화를 시작할 때 누르고 끝나면 종료하세요. 서버 시각으로 기록하며, 자동 문의 대기나 화면이 열린 시간을 업무시간으로 추정하지 않습니다. 새로고침 후 열린 업무를 다시 조회합니다.</p><div class="row"><select id="opsKind" aria-label="계측할 업무 종류" ${kindDisabled}>${Object.entries(KINDS).map(([k,v])=>`<option value="${k}" ${this.kind===k?'selected':''}>${v}</option>`).join('')}</select><button class="btn-small" id="opsStartWork" ${this.busy||!c.patient||c.summary?.totals?.open_activity_count>0||c.work.activities.some(a=>a.state==='OPEN')?'disabled':''}>${c.patient&&c.pending('work-start:'+c.patient)?'같은 시작 요청 재시도':'내 업무 시작'}</button></div><ul class="review-issues">${activities||'<li>조회된 내 업무가 없습니다.</li>'}</ul>${c.work.has_more?`<button class="btn-small" id="opsMoreWork" ${disabled}>이전 업무 더 보기</button>`:''}<p class="note">완료 활동시간(중첩 제거): ${c.summary?.totals?.completed_active_seconds??'미조회'}초 · 미종료 ${c.summary?.totals?.open_activity_count??'미조회'}건. 이송 건별 수치이며 여러 환자 사이의 중복시간·병원 전체 업무량은 집계하지 않습니다.</p></section>
      <form id="opsCallForm" class="resource-card"><h3>${fields?.supersedes_id||this.draft.supersedes?'통화 정정본':'통화 결과 기록'} ${c.patient?'· '+esc(c.patient):''}</h3><p class="note">실제 전화·녹음 기능은 아닙니다. 아래 시각은 담당자가 보고한 통화 시각입니다. 계측한 활동시간에 자동 합산하지 않습니다.${pending?' 전송 결과 미확인: 입력을 고정하고 같은 요청으로 재시도합니다.':''}</p><div class="row"><label>통화 시작(KST)<input type="datetime-local" step="1" data-draft="started" name="started" value="${esc(fields?localInput(fields.call_started_at):this.draft.started)}" required ${fieldDisabled}></label><label>통화 종료(KST)<input type="datetime-local" step="1" data-draft="ended" name="ended" value="${esc(fields?localInput(fields.call_ended_at):this.draft.ended)}" required ${fieldDisabled}></label></div><div class="row"><button type="button" class="btn-small" data-ops-time="started" ${fieldDisabled}>통화 시작 시각 기록</button><button type="button" class="btn-small" data-ops-time="ended" ${fieldDisabled}>통화 종료 시각 기록</button></div><label>전달받은 결과<select name="outcome" data-draft="outcome" ${fieldDisabled}>${options}</select></label><label>통화 상대 표시명(선택)<input name="counterparty" data-draft="counterparty" maxlength="80" value="${esc(fields?.counterparty_label??this.draft.counterparty)}" ${fieldDisabled}></label><label>통화 메모<textarea name="note" data-draft="note" maxlength="1000" rows="3" ${fieldDisabled}>${esc(fields?.note??this.draft.note)}</textarea></label>${this.draft.supersedes?`<p class="note">원본 #${esc(this.draft.supersedes)} 보존·새 정정본 추가</p>`:''}<button class="btn-small" ${this.busy||!c.patient?'disabled':''}>${pending?'같은 통화 기록 재시도':'통화 기록 저장'}</button></form>
      <section class="resource-card"><h3>현재 병원의 통화함</h3><div class="row"><label>내용 확인 상태<select id="opsCallStatus">${[['all','전체'],['unconfirmed','미확인'],['confirmed','확인됨'],['disputed','이의']].map(([k,v])=>`<option value="${k}" ${this.callStatus===k?'selected':''}>${v}</option>`).join('')}</select></label></div><div class="stack">${records||'<p class="note">조회된 통화 기록이 없습니다.</p>'}</div>${c.calls.has_more?`<button class="btn-small" id="opsMoreCalls" ${disabled}>이전 통화 더 보기</button>`:''}</section>`;
    const evidence=c.provenance?.hospitals?.[0];
    if(evidence)this.root.insertAdjacentHTML('beforeend',`<details class="resource-card"><summary>병원·공공 관측·운영 자원 출처</summary><p>기관 정보: ${esc(evidence.identity?.origin?.label||'미상')}</p>${evidence.public_evidence.map(row=>`<p class="note">${esc(row.endpoint)} · ${esc(row.origin?.label)} · 원천 기준시각 ${esc(row.as_of_utc||'미상')} · 수집시각 ${esc(row.fetched_at_utc||'미상')}${row.collection_is_not_observation_time?' · 수집시각은 관측시각을 대신하지 않음':''}</p>`).join('')}${evidence.operating_resources.map(row=>`<p class="note">운영 ${esc(row.resource_type)}: ${esc(row.origin?.label||'미상')} · 갱신 ${esc(row.updated_at_utc||'미상')} · 신선도 ${esc(row.update_freshness?.time_status||'unknown')} · 공공 관측치를 운영 총량으로 사용: ${row.capacity_is_public_observation===false?'아니오':'미확인'}</p>`).join('')}<p class="note">저장된 출처 표기만으로 실제 병원 담당자의 실측이나 현재 수용을 검증하지 않습니다.</p></details>`);
    try{const link=new URL('/patient/transport-change.html',ctx.server);link.searchParams.set('hid',ctx.hospital);if(/^https?:$/.test(link.protocol))this.root.insertAdjacentHTML('beforeend',`<p><a href="${esc(link.href)}" target="_blank" rel="noopener">이송 중 조건 변경 검토 화면 열기</a></p>`);}catch{}
    if(focus){const el=focus.id?this.root.querySelector('#'+focus.id):focus.draft?this.root.querySelector(`[data-draft="${focus.draft}"]`):null;if(el&&!el.disabled){el.focus();if(focus.start!=null&&el.setSelectionRange&&['text','textarea'].includes(el.type))el.setSelectionRange(focus.start,focus.end);}}
  }
  renderExternal(){const c=this.client;if(!c.context)return;const disabled=this.busy?'disabled':'';this.externalRoot.innerHTML=`<section class="resource-card"><h3>외부 유입 목록·개별 퇴실</h3><p class="note">등록된 자원 사용 기록을 개별 종료합니다. 합계 점유를 수동 차감하거나 기록을 삭제하지 않습니다. 집계 충돌은 최신 자원을 확인한 뒤 처리하세요.</p><div class="row"><select id="opsExternalState" aria-label="외부 유입 상태">${[['ACTIVE','활성'],['DISCHARGED','퇴실 이력'],['RECONCILED','무차감 종결'],['LEGACY','과거 원장 없음'],['all','전체']].map(([k,v])=>`<option value="${k}" ${this.externalState===k?'selected':''}>${v}</option>`).join('')}</select><button class="btn-small" id="opsExternalRefresh" ${disabled}>유입 목록 새로고침</button></div>${c.external.external_arrivals.map(r=>`<article class="resource-card" data-external-record="${r.id}"><b>#${r.id} · ${esc(r.state)} · 버전 ${r.version}</b><p>${esc(Object.entries(r.applied_resource_demands||{}).map(([k,v])=>`${k} ${v}`).join(' · ')||'실제 반영 자원 미확인')}</p><div style="white-space:pre-wrap">${esc(r.note)}</div><p class="note">등록 ${esc(date(r.observed_at))} · 요청 ID ${esc(r.request_id||'과거 기록·없음')}${r.discharged_at?' · 퇴실 '+esc(date(r.discharged_at)):''}</p>${r.data_errors?.length?`<p class="note">원장 확인 필요: ${esc(r.data_errors.join(' · '))}</p>`:''}${r.state==='ACTIVE'?`<button class="btn-small" data-ops-discharge="${r.id}" ${disabled}>이 기록만 퇴실·반영 점유 해제</button>`:'<p class="note">종료·과거 기록은 여기서 자동 차감하지 않습니다.</p>'}</article>`).join('')||'<p class="note">조회된 외부 유입 기록이 없습니다.</p>'}${c.external.next_before_id?`<button class="btn-small" id="opsMoreExternal" ${disabled}>이전 유입 더 보기</button>`:''}</section>`;}
  tick(){for(const el of this.root.querySelectorAll('[data-ops-elapsed]')){const a=this.client.work.activities.find(a=>String(a.id)===el.dataset.opsElapsed);if(a)el.textContent=this.client.elapsed(a);}}
  async submit(e){e.preventDefault();if(e.target.id==='opsPatientForm'){this.client.setPatient(e.target.elements.patient.value);this.render();await this.refresh();return;}if(e.target.id!=='opsCallForm')return;
    const pending=this.client.pending('call:'+this.client.patient);let fields=pending?.payload;
    if(!fields){const started=epochKst(this.draft.started),ended=epochKst(this.draft.ended);if(!Number.isInteger(started)||!Number.isInteger(ended)||ended<started){this.notice('통화 시작·종료 시각을 확인하세요.',true);return;}
      fields={call_started_at:started,call_ended_at:ended,reported_outcome:this.draft.outcome,counterparty_role:'paramedic',counterparty_label:this.draft.counterparty.trim(),note:this.draft.note.trim(),...(this.draft.supersedes?{supersedes_id:Number(this.draft.supersedes)}:{})};}
    await this.action(async()=>{const r=await this.client.createCall(fields);if(!r.stale){this.draft={started:'',ended:'',outcome:'checking',counterparty:'',note:'',supersedes:''};await this.refresh();}return r;},'통화 결과를 기록했습니다. 수용·목적지 상태는 바뀌지 않습니다.');}
  async click(e){const target=e.target.closest('button');if(!target||this.busy)return;
    if(['started','ended'].includes(target.dataset.opsTime)){
      if(this.client.pending('call:'+this.client.patient))return;
      this.draft[target.dataset.opsTime]=localInput(Math.floor(Date.now()/1000));this.render();return;
    }
    if(target.id==='opsRefresh'){await this.refresh();return;}
    if(target.id==='opsMoreCalls'){await this.action(async()=>{await this.client.loadCalls({before:this.client.calls.next_before_id,status:this.callStatus});},'이전 통화 기록을 조회했습니다.');return;}
    if(target.id==='opsMoreWork'){await this.action(async()=>{await this.client.loadWork({before:this.client.work.next_before_id});},'이전 업무 기록을 조회했습니다.');return;}
    if(target.dataset.opsPatient){this.client.setPatient(target.dataset.opsPatient);this.render();await this.refresh();return;}
    if(target.id==='opsStartWork'){await this.action(async()=>{const r=await this.client.startWork(this.kind);if(!r.stale)await this.client.loadWork();return r;},'내 업무 계측을 시작했습니다. 실제 업무가 끝나면 종료하세요.');return;}
    if(target.dataset.opsStop){const a=this.client.work.activities.find(a=>String(a.id)===target.dataset.opsStop);if(a)await this.action(async()=>{const r=await this.client.stopWork(a);if(!r.stale)await this.client.loadWork();return r;},'내 업무를 종료했습니다.');return;}
    const record=this.client.calls.records.find(r=>String(r.id)===(target.dataset.opsConfirm||target.dataset.opsDispute||target.dataset.opsCorrect));if(!record)return;
    if(target.dataset.opsCorrect){this.client.setPatient(record.patient_id);this.draft={started:localInput(record.call_started_at),ended:localInput(record.call_ended_at),outcome:record.reported_outcome,counterparty:record.counterparty_label||'',note:record.note||'',supersedes:String(record.id)};this.render();return;}
    const pending=this.client.pending(`confirm:${record.patient_id}:${record.id}`);
    const reason=target.dataset.opsDispute?(pending?.payload.reason??globalThis.prompt('통화 기록 내용에 대한 이의 사유(수용 불가 응답과 구분)')):'';
    if(target.dataset.opsDispute&&!reason?.trim())return;
    await this.action(async()=>{const r=await this.client.confirmCall(record,target.dataset.opsDispute?'dispute':'confirm',reason||'');if(!r.stale)await this.client.loadCalls({status:this.callStatus});return r;},'통화 기록 내용에 대한 확인을 저장했습니다. 수용 응답은 별도입니다.');
  }
  async externalClick(e){const target=e.target.closest('button');if(!target||this.busy)return;if(target.id==='opsExternalRefresh'){await this.refreshExternal();return;}if(target.id==='opsMoreExternal'){await this.action(async()=>{await this.client.loadExternal({before:this.client.external.next_before_id,state:this.externalState});},'이전 유입 기록을 조회했습니다.');return;}const record=this.client.external.external_arrivals.find(r=>String(r.id)===target.dataset.opsDischarge);if(!record)return;
    await this.action(async()=>{const r=await this.client.dischargeExternal(record);if(!r.stale)await this.client.loadExternal({state:this.externalState});return r;},'선택한 외부 유입을 개별 퇴실 처리했습니다.');}
}

export function epochKst(value){if(!/^\d{4}-\d\d-\d\dT\d\d:\d\d(?::\d\d)?$/.test(value))return NaN;return Date.parse(value+(value.length===16?':00':'')+'+09:00')/1000;}
export function localInput(epoch){return new Date((epoch+9*3600)*1000).toISOString().slice(0,19);}
