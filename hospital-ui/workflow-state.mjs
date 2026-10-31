// Hospital-scoped, display-only planning and explicit important-event receipts.
const enc=encodeURIComponent;
const uuid=()=>globalThis.crypto?.randomUUID?.()??`wf-${Date.now()}-${Math.random().toString(16).slice(2)}`;
const copy=value=>JSON.parse(JSON.stringify(value));
const integer=value=>Number.isSafeInteger(value)&&value>=0;
const validReview=(review,patient,version)=>review?.patient_id===patient&&review.patient_version===version&&review.purpose==='allocation_input_display_only'&&review.blocking===false&&Array.isArray(review.items);

export const PLAN_STATES={READY:'현재 계산 결과',QUEUED:'추천 대기',STALE:'이전 추천 사용 불가',ERROR:'추천 계산 오류',NOT_APPLICABLE:'현재 추천 대상 아님',UNAVAILABLE:'병원용 추천 정보 없음'};
export const RESULT_STATES={RECOMMENDED:'현재 모델의 배정안',UNASSIGNED:'현재 모델에서 추천 없음',DESTINATION_HELD:'현재 목적지 유지',NEEDS_REVIEW:'담당자 검토 필요'};
export const WORKER_STATES={RUNNING:'가동 중',STARTING:'시작 중',DISABLED:'비활성',STOPPED:'중단',STOPPING:'종료 중',ERROR:'오류',STALLED:'처리 지연',UNKNOWN:'가동 여부 미확인'};
const WARNING_LABELS={TREATMENT_CONFIRMATION_REQUIRED:'환자별 치료조건의 수용 가능 여부 확인이 필요합니다.',UNSUPPORTED_RESOURCE:'현재 배정 모델은 진료 자리와 전문팀만 계산합니다. 그 밖의 자원은 별도 검토가 필요합니다.',NO_VALID_ACCEPTED_HOSPITAL:'현재 환자 버전에 유효한 수용 응답이 없습니다.',NO_ELIGIBLE_ACCEPTED_HOSPITAL:'수용 응답은 있으나 현재 운영 조건을 충족한 추천이 없습니다.',OPERATING_RESOURCE_MISSING:'운영 자원 입력이 없습니다.',OPERATING_RESOURCE_UNKNOWN:'현재 운영 자원을 확인하지 못했습니다.',OPERATING_RESOURCE_STALE:'운영 자원 갱신이 오래돼 재확인이 필요합니다.',PRIORITY_UNVERIFIED:'전문가 우선순위가 확인되지 않았습니다.',WAIT_LIMIT_EXCEEDED:'실험상 대기 한도를 넘어 검토가 필요합니다.',DESTINATION_RESTRICTED:'현재 목적지의 수용 제한을 재확인하세요.',HOSPITAL_REQUESTED_ADJUSTMENT:'현재 병원이 조정을 요청했습니다.',CAPABILITY_MISMATCH:'요청한 진료 역량을 확인하세요.'};
export function warningText(code){const [key,...rest]=String(code).split(':');return `${WARNING_LABELS[key]||key}${rest.length?` · ${rest.join(':')}`:''}`;}

export function planningModel({patient,planning,currentReview,currentReviewError='',availability,hospital,now=Math.floor(Date.now()/1000)}){
  const p=patient||{},entry=planning?.patient_id===p.id?planning:null;
  const observed=entry?.availability||availability||{};
  const model={patientId:p.id||'',patientVersion:p.version??null,state:entry?.status||(['ARRIVED','COMPLETED','CANCELLED'].includes(p.state)?'NOT_APPLICABLE':'UNAVAILABLE'),
    worker:observed.worker_state||'UNKNOWN',revision:entry?.revision??null,result:null,inputReview:null,warnings:[],versionIssue:null,
    note:'추천·병원의 수용 응답·담당자의 목적지 확정은 각각 별개입니다. 이 표시는 자동 수용·예약·목적지 변경을 실행하지 않습니다.'};
  // Guidance comes from a separate current-version read, never planning history.
  const review=currentReview;
  if(validReview(review,p.id,p.version)){model.inputReview=copy(review);}
  else if(review)model.versionIssue='현재 환자 버전과 확인 안내가 맞지 않습니다. 최신 정보를 다시 불러오세요.';
  else if(currentReviewError)model.versionIssue=currentReviewError;
  if(model.state==='READY'){
    const result=entry.result,saved=result?.input_review;
    if(!result||saved?.patient_id!==p.id||saved?.patient_version!==p.version){model.state='STALE';model.versionIssue='계산에 사용한 환자 버전이 현재와 맞지 않아 이전 추천을 표시하지 않습니다.';}
    else if(!integer(entry.expires_at)||entry.expires_at<=now){model.state='STALE';model.versionIssue='추천 유효기간이 지났거나 확인되지 않습니다. 최신 결과를 다시 불러오세요.';}
    else if(result.hospital_id&&result.hospital_id!==hospital){model.state='STALE';model.versionIssue='현재 병원에 해당하지 않는 결과는 표시하지 않습니다.';}
    else{model.result=copy(result);model.warnings=(result.warnings||[]).map(warningText);}
  }
  return model;
}

export class HospitalInputReviewClient{
  constructor({request}){this.request=request;this.generation=0;this.signature='';this.context=null;this.review=null;this.error='';this.inflight=null;}
  select(context,patient){
    const ctx=patient?.id===context?.patient&&integer(patient.version)?{...context,patientVersion:patient.version}:null;
    const signature=JSON.stringify(ctx);if(signature===this.signature)return false;
    this.signature=signature;this.generation++;this.context=ctx;this.review=null;this.error='';this.inflight=null;return true;
  }
  async refresh(){
    if(!this.context)return false;if(this.inflight)return this.inflight;
    const generation=this.generation,ctx={...this.context};
    const task=(async()=>{try{
      const value=await Promise.resolve().then(()=>this.request(ctx,`/api/patients/${enc(ctx.patient)}/input-review`));
      if(generation!==this.generation)return false;
      if(value?.patient_id!==ctx.patient||value.patient_version!==ctx.patientVersion||!validReview(value.input_review,ctx.patient,ctx.patientVersion))throw Error('현재 환자 버전과 입력 확인 응답이 일치하지 않습니다.');
      this.review=copy(value.input_review);this.error='';return true;
    }catch(error){if(generation===this.generation){this.review=null;this.error='현재 입력의 확인 안내를 조회하지 못했습니다. 환자와 버전을 확인해 다시 불러오세요.';}return false;}
    finally{if(this.inflight===task)this.inflight=null;}})();
    this.inflight=task;return task;
  }
}

export class HospitalWorkflowClient{
  constructor({request,storage,makeId=uuid,visible=()=>true,maxPages=10}){
    this.request=request;this.storage=storage;this.makeId=makeId;this.visible=visible;this.maxPages=maxPages;
    this.generation=0;this.serial=0;this.context=null;this.signature='';this.inflight=new Map();this.reset();
  }
  reset(){this.items=new Map();this.rendered=new Set();this.cursor=0;this.hasMore=false;this.restorationHasMore=false;this.error='';}
  read(key){const raw=this.storage.getItem(key);return raw==null?null:JSON.parse(raw);}
  save(key,value){try{this.storage.setItem(key,JSON.stringify(value));}catch{throw Error('중요 사건의 동일 요청을 저장할 수 없습니다. 브라우저 저장소를 확인하세요.');}}
  namespace(ctx=this.context){return `capstone.hospital.workflow.v1:${enc(JSON.stringify([ctx.server,ctx.hospital,ctx.handler,ctx.actor,ctx.patient]))}`;}
  select(context){
    const ctx={server:String(context.server||'').replace(/\/$/,''),hospital:String(context.hospital||''),handler:String(context.handler||'').trim(),patient:String(context.patient||''),version:context.version??0};
    const signature=JSON.stringify(ctx);if(signature===this.signature)return false;
    this.signature=signature;this.generation++;this.serial++;this.reset();this.context=null;
    try{if(ctx.handler){const key=`capstone.hospital.workflow.actor.v1:${enc(JSON.stringify([ctx.server,ctx.hospital,ctx.handler]))}`;let actor=this.read(key);
      if(!actor){actor=`hospital-wf-${this.makeId()}`;this.save(key,actor);}
      if(typeof actor!=='string'||actor.length>80||!actor.trim())throw Error('저장된 사건 확인자 식별자를 확인하세요.');ctx.actor=actor;}
    else ctx.actor=null;
    this.context=ctx;return true;
    }catch(error){this.signature='';throw error;}
  }
  stamp(){return {generation:this.generation,context:{...this.context}};}
  current(stamp){return stamp.generation===this.generation;}
  base(ctx=this.context){return `/api/hospitals/${enc(ctx.hospital)}/patients/${enc(ctx.patient)}/important-events`;}
  validEvent(event,ctx=this.context){
    if(!event||!Number.isSafeInteger(event.event_id)||event.event_id<1||event.patient_id!==ctx.patient||(event.hospital_id!=null&&event.hospital_id!==ctx.hospital))throw Error('다른 환자 또는 병원의 중요 사건은 표시하지 않았습니다.');
    return event;
  }
  merge(event,ctx=this.context){this.validEvent(event,ctx);const old=this.items.get(event.event_id);
    const merged={...event,delivered_at:old?.delivered_at??event.delivered_at??null,acknowledged_at:old?.acknowledged_at??event.acknowledged_at??null};
    this.items.set(event.event_id,merged);
    if(merged.delivered_at!=null)this.storage.removeItem(this.pendingKey('delivered',event.event_id,ctx));
    if(merged.acknowledged_at!=null)this.storage.removeItem(this.pendingKey('acknowledge',event.event_id,ctx));}
  validatePage(value,ctx){const s=value?.scope;
    if(s?.role!=='hospital'||s.hospital_id!==ctx.hospital||s.patient_id!==ctx.patient||s.actor_id!==ctx.actor||!Array.isArray(value.items))throw Error('중요 사건 응답의 병원·환자·확인자 범위가 맞지 않습니다.');
    for(const event of value.items)this.validEvent(event,ctx);
  }
  async refresh({restore=false}={}){
    if(!this.context?.patient||!this.context.actor)return false;
    const stamp=this.stamp(),serial=++this.serial,ctx=stamp.context;
    if(restore){const query=new URLSearchParams({actor_id:ctx.actor,unacknowledged_only:'true',limit:'100'});
      const value=await this.request(ctx,`${this.base(ctx)}?${query}`);if(!this.current(stamp)||serial!==this.serial)return false;
      this.validatePage(value,ctx);for(const item of value.items)this.merge(item,ctx);this.restorationHasMore=!!value.has_more;}
    let cursor=this.cursor;
    for(let page=0;page<this.maxPages;page++){
      const query=new URLSearchParams({actor_id:ctx.actor,after_id:String(cursor),limit:'100'});
      const value=await this.request(ctx,`${this.base(ctx)}?${query}`);if(!this.current(stamp)||serial!==this.serial)return false;
      this.validatePage(value,ctx);const next=value.next_after_id;
      if(!integer(next)||next<cursor||(value.has_more&&next<=cursor))throw Error('중요 사건 페이지 커서가 올바르지 않습니다.');
      for(const item of value.items)this.merge(item,ctx);this.cursor=next;this.hasMore=!!value.has_more;cursor=next;
      if(!value.has_more)break;
    }
    this.error='';return true;
  }
  markRendered(ids){if(!this.visible())return false;for(const id of ids)if(this.items.has(id))this.rendered.add(id);return true;}
  pendingKey(kind,id,ctx=this.context){return `${this.namespace(ctx)}:receipt:${kind}:${id}`;}
  pending(kind,id){try{return this.read(this.pendingKey(kind,id));}catch{return {invalid:true};}}
  async receipt(kind,id,{explicit=false}={}){
    if(!['delivered','acknowledge'].includes(kind))throw Error('지원하지 않는 사건 확인 동작입니다.');
    if(!this.visible()||!this.rendered.has(id))return {skipped:true};
    const item=this.items.get(id);if(!item)throw Error('화면에 표시한 사건이 없습니다.');
    if(kind==='acknowledge'&&(!explicit||item.delivered_at==null))throw Error('사건 표시의 수신 기록 뒤 확인 버튼을 직접 눌러주세요.');
    if(kind==='delivered'&&item.delivered_at!=null)return {duplicate:true};
    if(kind==='acknowledge'&&item.acknowledged_at!=null)return {duplicate:true};
    const stamp=this.stamp(),ctx=stamp.context,key=this.pendingKey(kind,id,ctx),path=`${this.base(ctx)}/${id}/${kind}`;
    if(this.inflight.has(key))return this.inflight.get(key);
    let saved=this.read(key);if(!saved){saved={path,payload:{actor_id:ctx.actor},context:ctx};this.save(key,saved);}
    if(saved.path!==path||saved.payload?.actor_id!==ctx.actor)throw Error('저장된 재시도 요청의 범위가 다릅니다.');
    const promise=(async()=>{try{
      const value=await this.request(saved.context,saved.path,{method:'POST',body:JSON.stringify(saved.payload)});
      this.validEvent(value.event,ctx);if(value.event.event_id!==id)throw Error('다른 사건의 처리 결과는 반영하지 않았습니다.');
      this.storage.removeItem(key);if(!this.current(stamp))return {stale:true};this.merge(value.event,ctx);return {value,stale:false};
    }catch(error){error.pending=!(error.status>=400&&error.status<500);if(!error.pending)this.storage.removeItem(key);throw error;}
    finally{this.inflight.delete(key);}})();this.inflight.set(key,promise);return promise;
  }
}
