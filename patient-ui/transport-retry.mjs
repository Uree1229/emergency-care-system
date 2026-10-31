// Scope checks only. Never rewrite an uncertain request's body or request ID.
const decisionPath=/^\/api\/hospitals\/[^/?#]+\/transport-change-reviews\/[^/?#]+\/decision$/;
const actorPattern=/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,79}$/;
function scopeError(message){const error=new Error(message);error.code='LOCAL_TRANSPORT_RETRY_SCOPE';return error;}

export function hospitalDecisionActor(pending){
  if(!decisionPath.test(pending?.path||''))return null;
  const actor=pending?.body?.actor_id;
  if(typeof actor!=='string'||!actorPattern.test(actor))throw scopeError('원래 병원 판단 요청의 작업자 ID를 확인할 수 없습니다. 원래 이송과 저장 상태를 확인하세요.');
  return actor;
}

export function assertTransportRetryScope(pending,current,hospitalActor){
  if(!pending?.scope||['pid','hid','actor'].some(key=>typeof pending.scope[key]!=='string'||pending.scope[key]!==current?.[key])){
    throw scopeError('원래 이송·병원·구급대 작업자 범위로 돌아가야 같은 요청을 재시도할 수 있습니다.');
  }
  // Older v1 pending records have no hospital actor in their scope. The exact
  // original body is the authority for this local retry boundary too.
  const original=hospitalDecisionActor(pending);
  if(original!==null&&original!==String(hospitalActor??'').trim()){
    throw scopeError(`이 병원 판단 요청의 원래 병원 작업자 ID는 ${original}입니다. 병원 시연 작업자 ID를 원래 값으로 되돌린 뒤 같은 ID로 재시도하세요.`);
  }
  return true;
}

export function resetHospitalApprovalChecks(root){
  for(const input of root.querySelectorAll('[data-hospital-decision-confirm]'))input.checked=false;
}
