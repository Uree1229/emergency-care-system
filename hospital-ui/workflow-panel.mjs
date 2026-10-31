import {HospitalWorkflowClient,HospitalInputReviewClient,planningModel,PLAN_STATES,RESULT_STATES,WORKER_STATES} from './workflow-state.mjs';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date=value=>value==null?'시각 미상':new Date(value*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'});

export function planningMarkup(model){
  const review=model.inputReview,result=model.result;
  const stateText=model.state==='QUEUED'&&['DISABLED','STOPPED','STOPPING'].includes(model.worker)?'추천 없음 · 배정 워커 확인 필요':PLAN_STATES[model.state]||model.state;
  const guidance=review?`<h4>현재 입력의 확인 안내</h4><p class="workflow-meta">환자 버전 ${esc(review.patient_version)} · 조회 ${esc(date(review.checked_at))}</p>${review.items?.length?['review','info'].map(level=>{
    const rows=review.items.filter(item=>item.level===level);return rows.length?`<h5>${level==='review'?'배정·인계 관련 재확인':'선택 관찰 참고'}</h5><ul>${rows.map(item=>`<li><b>${esc(item.label)}</b> · ${esc(item.reason)}${item.observed_at!=null?`<span class="workflow-meta">관찰 ${esc(date(item.observed_at))}</span>`:''}</li>`).join('')}</ul>`:'';}).join(''):'<p>현재 정책의 재확인 표시 없음</p>'}<p class="workflow-meta">이 안내만으로 수용·배정을 막거나 기존 수용을 취소하지 않습니다. 임상 안전 판정이 아닙니다.</p>`:'<p class="workflow-meta">현재 환자 버전의 확인 안내를 아직 조회하지 못했습니다. 과거 계산 안의 입력 안내로 대신 표시하지 않습니다.</p>';
  const resultHtml=result?`<h4>${esc(RESULT_STATES[result.status]||result.status)}</h4>${result.destination_locked?'<p class="workflow-lock">확정 목적지 잠금 유지 · 이 추천으로 목적지를 자동 변경하지 않습니다.</p>':''}${result.hospital_id?`<p>해당 병원 ${esc(result.hospital_id)}${result.planned_start_at!=null?` · 모델상 시작 ${esc(date(result.planned_start_at))}`:''}</p>`:''}${model.warnings.length?`<ul>${model.warnings.map(w=>`<li>${esc(w)}</li>`).join('')}</ul>`:''}`:'';
  return `<section class="workflow-card" aria-label="선택 환자 추천과 확인 안내" data-workflow-patient="${esc(model.patientId)}"><div class="workflow-heading"><h3>선택 환자의 추천·확인 안내</h3><span id="hospitalRecommendationState" data-state="${esc(model.state)}">${esc(stateText)}</span></div><p class="workflow-meta">환자 ${esc(model.patientId)} · 현재 버전 ${esc(model.patientVersion??'미확인')} · 계산 버전 ${esc(model.revision??'미확인')} · 워커 ${esc(WORKER_STATES[model.worker]||model.worker)}</p><p>${esc(model.note)}</p>${model.versionIssue?`<p class="workflow-warning">${esc(model.versionIssue)}</p>`:''}${resultHtml}<div id="hospitalInputReview">${guidance}</div></section>`;
}

export class HospitalWorkflowPanel{
  constructor({planningRoot,eventRoot,context,snapshot,request,storage,document=globalThis.document,window=globalThis.window,makeId,identityEditing=()=>false,requestPaint=fn=>globalThis.requestAnimationFrame(fn)}){
    this.planningRoot=planningRoot;this.eventRoot=eventRoot;this.getContext=context;this.getSnapshot=snapshot;this.document=document;this.window=window;this.requestPaint=requestPaint;
    this.client=new HospitalWorkflowClient({request,storage,makeId,visible:()=>!identityEditing()&&this.document.visibilityState!=='hidden'&&!this.eventRoot.hidden&&this.eventRoot.isConnected&&this.eventRoot.getClientRects().length>0});
    this.reviewClient=new HospitalInputReviewClient({request});
    this.error='';this.receiptErrors=new Map();this.paint=0;this.loadingGeneration=null;this.pendingRestore=false;this.busy=new Set();this.deliveryFailures=new Set();this.eventSignature='';
    eventRoot.addEventListener('click',event=>{const ack=event.target.closest('[data-workflow-ack]');if(ack)this.acknowledge(Number(ack.dataset.workflowAck));else if(event.target.closest('[data-workflow-refresh]'))this.refresh({restore:true});});
    eventRoot.addEventListener('scroll',()=>this.afterPaint(),true);
    this.onVisible=()=>{if(this.document.visibilityState!=='hidden'){this.sync();this.refresh({restore:true});}};
    this.document.addEventListener('visibilitychange',this.onVisible);this.window.addEventListener('online',this.onVisible);
    this.window.addEventListener('scroll',()=>this.afterPaint(),{passive:true});this.window.addEventListener('resize',()=>this.afterPaint(),{passive:true});
  }
  sync(){
    try{
      const changed=this.client.select(this.getContext());if(changed){this.error='';this.receiptErrors.clear();this.busy.clear();this.deliveryFailures.clear();this.pendingRestore=false;this.paint++;}
      const snapshot=this.getSnapshot(),ctx=this.client.context;
      const p=snapshot.patient?.id===ctx.patient?snapshot.patient:null;
      this.planningRoot.hidden=!p||!snapshot.showPlanning;
      this.reviewClient.select(ctx,this.planningRoot.hidden?null:p);
      if(!this.planningRoot.hidden)this.planningRoot.innerHTML=planningMarkup(planningModel({patient:p,planning:snapshot.planning,currentReview:this.reviewClient.review,currentReviewError:this.reviewClient.error,availability:snapshot.availability,hospital:ctx.hospital}));
      this.renderEvents();return changed;
    }catch(error){this.error=error.message;this.planningRoot.hidden=true;this.renderEvents();return false;}
  }
  async refresh({restore=false}={}){
    const changed=this.sync(),generation=this.client.generation;
    const reviewTask=this.reviewClient.refresh().finally(()=>this.sync());
    if(restore)this.deliveryFailures.clear();
    if(this.loadingGeneration===generation){this.pendingRestore=this.pendingRestore||restore||changed;await reviewTask;return;}
    this.loadingGeneration=generation;
    try{await Promise.all([this.client.refresh({restore:restore||changed}),reviewTask]);if(generation===this.client.generation)this.error='';}
    catch(error){if(generation===this.client.generation)this.error=error.status===403?'현재 병원의 참여 범위에서 이 사건을 조회할 수 없습니다.':error.message;}
    finally{if(this.loadingGeneration===generation)this.loadingGeneration=null;if(generation===this.client.generation){this.renderEvents();if(this.pendingRestore){this.pendingRestore=false;queueMicrotask(()=>this.refresh({restore:true}));}}}
  }
  resolveReceiptErrors(){
    for(const item of this.client.items.values()){
      if(item.delivered_at!=null){this.receiptErrors.delete(`delivered:${item.event_id}`);this.deliveryFailures.delete(item.event_id);}
      if(item.acknowledged_at!=null)this.receiptErrors.delete(`acknowledge:${item.event_id}`);
    }
  }
  renderEvents(){
    const ctx=this.client.context;
    this.eventRoot.hidden=!ctx?.patient;
    if(this.eventRoot.hidden){this.eventRoot.innerHTML='';return;}
    this.resolveReceiptErrors();
    const items=[...this.client.items.values()].sort((a,b)=>Number(a.acknowledged_at!=null)-Number(b.acknowledged_at!=null)||a.event_id-b.event_id);
    const errors=[this.error,...this.receiptErrors.values()].filter(Boolean);
    const signature=JSON.stringify([ctx,items,errors,[...this.busy],this.client.hasMore,this.client.restorationHasMore,items.map(item=>this.client.pending('acknowledge',item.event_id))]);
    if(signature===this.eventSignature){this.afterPaint();return;}this.eventSignature=signature;
    const oldScroll=this.eventRoot.querySelector('#hospitalImportantEventList')?.scrollTop||0;
    this.eventRoot.innerHTML=`<div class="workflow-heading"><h2>중요 사건 · 환자 ${esc(ctx.patient)}</h2><button type="button" class="btn-small" data-workflow-refresh>미확인 다시 불러오기</button></div><p class="workflow-meta">병원 ${esc(ctx.hospital)} · 담당자 ${esc(ctx.handler||'입력 필요')} · 표시 수신과 사용자 확인을 따로 기록합니다. 확인은 수용·목적지 판단이 아닙니다.</p>${errors.map(error=>`<p class="workflow-warning" role="alert">${esc(error)}</p>`).join('')}${!ctx.actor?'<p>상단에 담당자 이름을 입력하면 이 확인자의 중요 사건을 불러옵니다.</p>':`<div id="hospitalImportantEventList" class="workflow-event-list" aria-live="polite">${items.length?items.map(item=>{
      const acked=item.acknowledged_at!=null,pending=ctx.actor&&this.client.pending('acknowledge',item.event_id),busy=this.busy.has(item.event_id);
      return `<article class="workflow-event ${acked?'acknowledged':''}" data-workflow-event="${item.event_id}"><div><b>${esc(item.summary)}</b><p class="workflow-meta">사건 ${esc(date(item.at))} · 표시 수신 ${esc(item.delivered_at==null?'아직 기록 안 됨':date(item.delivered_at))} · 확인 ${esc(item.acknowledged_at==null?'미확인':date(item.acknowledged_at))}</p></div>${acked?'<span class="workflow-acknowledged">확인 기록 있음</span>':`<button type="button" class="btn-small" data-workflow-ack="${item.event_id}" ${item.delivered_at==null||busy?'disabled':''}>${busy?'전송 중…':pending?'확인 기록 다시 전송':'내용 확인'}</button>`}</article>`;}).join(''):'<p>조회된 중요 사건이 없습니다.</p>'}</div>`}${this.client.hasMore?'<p>아직 읽지 않은 사건 페이지가 있습니다. 다시 불러오기로 이어서 조회합니다.</p>':''}${this.client.restorationHasMore?'<p>미확인 사건이 더 있습니다. 현재 사건을 확인한 뒤 다음 미확인 기록을 복원합니다.</p>':''}`;
    const list=this.eventRoot.querySelector('#hospitalImportantEventList');if(list)list.scrollTop=oldScroll;
    this.afterPaint();
  }
  afterPaint(){
    const generation=this.client.generation,paint=++this.paint;
    this.requestPaint(()=>{
      if(generation!==this.client.generation||paint!==this.paint||!this.client.visible())return;
      const container=this.eventRoot.querySelector('#hospitalImportantEventList');if(!container)return;
      const box=container.getBoundingClientRect(),height=this.window.innerHeight,width=this.window.innerWidth;
      const ids=[...container.querySelectorAll('[data-workflow-event]')].filter(el=>{
        const r=el.getBoundingClientRect();return el.isConnected&&r.height>0&&r.bottom>Math.max(0,box.top)&&r.top<Math.min(height,box.bottom)&&r.right>0&&r.left<width;
      }).map(el=>Number(el.dataset.workflowEvent));
      this.client.markRendered(ids);for(const id of ids)this.delivered(id,generation);
    });
  }
  async delivered(id,generation){
    const item=this.client.items.get(id);if(!item||item.delivered_at!=null||this.busy.has(id)||this.deliveryFailures.has(id))return;
    this.busy.add(id);
    try{const result=await this.client.receipt('delivered',id);if(result.stale||result.skipped)return;if(generation===this.client.generation)this.resolveReceiptErrors();}
    catch(error){if(generation===this.client.generation){this.deliveryFailures.add(id);this.receiptErrors.set(`delivered:${id}`,`사건 #${id} 표시 수신 기록을 전송하지 못했습니다. 다시 불러오면 동일한 요청으로 재시도합니다. ${error.message}`);}}
    finally{if(generation===this.client.generation){this.busy.delete(id);this.renderEvents();}}
  }
  async acknowledge(id){
    this.sync();if(this.busy.has(id))return;const generation=this.client.generation;this.busy.add(id);this.renderEvents();
    try{const result=await this.client.receipt('acknowledge',id,{explicit:true});if(result.stale||result.skipped)return;if(generation===this.client.generation){
      this.resolveReceiptErrors();
      try{await this.client.refresh({restore:true});if(generation===this.client.generation)this.error='';}
      catch(error){if(generation===this.client.generation)this.error='사건 확인 뒤 목록을 다시 조회하지 못했습니다. '+error.message;}
    }}
    catch(error){if(generation===this.client.generation)this.receiptErrors.set(`acknowledge:${id}`,`사건 #${id} 확인 기록 전송을 완료하지 못했습니다. 원래 확인 요청으로 다시 전송할 수 있습니다. ${error.message}`);}
    finally{if(generation===this.client.generation){this.busy.delete(id);this.renderEvents();}}
  }
}
