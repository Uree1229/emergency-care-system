// UI state only; all clinical proposals and the bound token come from the server.
export class MainTreatmentReview {
  constructor(client){this.client=client;this.review=null;this.notice='';}
  invalidate(){this.review=null;this.notice='';}
  get path(){return `/api/voice/sessions/${encodeURIComponent(this.client.session?.id||'')}/treatment-review`;}
  requireWaiting(patient){
    if(!patient||patient.state!=='WAITING'||this.client.session?.patient_id!==patient.id||this.client.session?.state!=='DRAFT')throw Error('목적지 확정 전의 같은 환자 초안에서만 치료조건을 검토할 수 있습니다.');
  }
  async load(patient,mode){
    this.requireWaiting(patient);if(mode!==undefined&&!['suggested','keep'].includes(mode))throw Error('기존 유지 또는 서버 제안을 선택하세요.');
    this.invalidate();const {review}=await this.client.api(`${this.path}?actor_id=${encodeURIComponent(this.client.actorId)}`);
    // GET chooses the server default. It must never replace an explicit Keep
    // selection with an available suggestion while the dropdown still says Keep.
    if(mode==='keep'&&review.selection?.mode!=='keep')return this.preview(patient,'keep');
    this.review=review;
    if(mode==='suggested'&&review.selection?.mode==='keep')this.notice='현재 적용할 서버 제안이 없어 기존 치료조건 유지안을 표시합니다. 변경 전후를 다시 확인하세요.';
    return review;
  }
  async preview(patient,mode){
    this.requireWaiting(patient);if(!['suggested','keep'].includes(mode))throw Error('기존 유지 또는 서버 제안을 선택하세요.');
    this.invalidate();const {review}=await this.client.write('treatment-review/preview',{expected_patient_version:patient.version,selection:{mode}});this.review=review;return review;
  }
  async apply(patient,{confirmed=false,allowReduction=false,reason=''}={}){
    this.requireWaiting(patient);const review=this.review;
    if(!review||review.session_version!==this.client.session.version||review.patient_version!==patient.version)throw Error('변경안을 최신 초안·환자 버전으로 다시 조회하세요.');
    if(!this.client.session.confirmed||!confirmed)throw Error('음성 초안과 치료조건을 각각 명시적으로 확인하세요.');
    if(!review.can_apply||review.blockers?.length)throw Error('적용 제한 항목을 확인하고 변경안을 다시 조회하세요.');
    if(review.reductions?.length&&!allowReduction)throw Error('빠지거나 감소하는 치료조건을 별도로 확인하세요.');
    const result=await this.client.write('treatment-review/apply',{expected_patient_version:review.patient_version,review_token:review.token,selection:review.selection,confirmed:true,allow_reduction:!!allowReduction,reason});
    this.invalidate();return result;
  }
}
