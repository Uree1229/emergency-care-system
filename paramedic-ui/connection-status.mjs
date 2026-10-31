/** Presentation only: browser hints, API response, speech and storage remain separate. */
const CONNECTIVITY=new Set(['unknown','checking','reachable','network_error','service_error','invalid_response']);
const RECOVERY=new Set(['saved','failed','unknown']);
const API_LINES={
  unknown:'서버 API 응답은 아직 확인되지 않았습니다.',
  checking:'서버 API 응답을 확인하고 있습니다.',
  reachable:'서버 API 응답을 받았습니다. 입력 저장 완료는 저장 발화와 환자 요약에서 확인하세요.',
  network_error:'서버 API 응답을 받지 못했습니다. 네트워크·서버 실행·주소·브라우저 연결 설정을 확인하세요.',
  service_error:'서버 오류 응답을 받았습니다. 서버 상태를 확인하고 서버 연결 확인을 눌러 주세요.',
  invalid_response:'서버 응답 형식을 읽지 못했습니다. 서버 주소와 실행 중인 앱을 확인하세요.',
};
function checkedTime(value){
  if(!Number.isSafeInteger(value)||value<0)return null;
  try{return new Date(value+9*60*60*1000).toISOString().replace('T',' ').replace(/\.\d{3}Z$/u,' KST');}catch(_){return null;}
}

export function buildConnectionStatus(state={}){
  if(!state||typeof state!=='object'||Array.isArray(state))state={};
  const connectivity=CONNECTIVITY.has(state.connectivity)?state.connectivity:'unknown';
  const browserOnline=typeof state.browserOnline==='boolean'?state.browserOnline:null;
  const pending=state.pending===true,recovery=RECOVERY.has(state.recovery)?state.recovery:'unknown';
  const localUnapplied=state.localUnapplied===true,speechIssue=state.speechIssue===true;
  const compatible=typeof state.compatible==='boolean'?state.compatible:null,checkedAt=checkedTime(state.checkedAt);
  const httpStatus=Number.isInteger(state.httpStatus)&&state.httpStatus>=100&&state.httpStatus<=599?state.httpStatus:null;
  const requestIssue=connectivity==='reachable'&&httpStatus>=400&&httpStatus<=499;
  let tone='neutral',title='연결 상태 확인 전';
  if(recovery==='failed'){tone='danger';title='브라우저 임시 저장 확인 필요';}
  else if(pending){tone='warning';title='저장 결과 확인 필요';}
  else if(connectivity==='network_error'){tone='danger';title='서버 연결 확인 필요';}
  else if(connectivity==='service_error'){tone='danger';title='서버 처리 오류 확인 필요';}
  else if(connectivity==='invalid_response'){tone='danger';title='서버 응답 형식 확인 필요';}
  else if(speechIssue){tone='warning';title='음성 입력 확인 필요';}
  else if(requestIssue){tone='warning';title='서버 응답 있음 · 요청 확인 필요';}
  else if(compatible===false){tone='warning';title='음성 기능 버전 확인 필요';}
  else if(connectivity==='checking'){tone='info';title='서버 연결 확인 중';}
  else if(connectivity==='reachable'){tone='success';title='서버 응답 확인됨';}
  else if(browserOnline===false){tone='warning';title='연결 상태 확인 필요';}

  const lines=[API_LINES[connectivity]];
  if(requestIssue)lines.push(`HTTP ${httpStatus} 응답을 받았습니다. 권한·요청 내용·최신 기록을 확인하세요. 연결 단절로 단정하지 않습니다.`);
  if(browserOnline!==null)lines.push(browserOnline?'브라우저는 온라인으로 표시합니다. 서버 연결은 API 응답으로 따로 확인합니다.':'브라우저는 오프라인으로 표시합니다. 서버 연결은 API 응답으로 따로 확인합니다.');
  if(pending)lines.push(recovery==='failed'?'처리 확인이 필요한 요청이 있습니다. 임시 저장 상태를 확인한 뒤 같은 요청 재시도로 결과를 확인하세요.':'처리 확인이 필요한 요청이 있습니다. 같은 요청 재시도로 처리 결과를 확인하세요.');
  if(recovery==='saved')lines.push('최근 브라우저 임시 복구본 저장을 확인했습니다. 서버 저장 결과는 별도로 확인하세요.');
  else if(recovery==='failed')lines.push('브라우저 임시 저장에 실패했습니다. 현재 화면을 유지하고 화면의 원문을 복사해 보관하세요.');
  else lines.push('브라우저 임시 저장 상태는 아직 확인되지 않았습니다.');
  if(localUnapplied)lines.push(pending?'미반영으로 표시된 내용이 있습니다. 대기 요청의 처리 결과를 먼저 확인하고 검토칸을 확인하세요.':'미반영 내용이 있습니다. 검토칸에서 작성·보관하고 연결 확인 후 반영 버튼을 눌러 주세요.');
  if(speechIssue)lines.push('마이크 권한·브라우저 음성 서비스를 확인하세요. 음성 입력 상태는 서버 API 연결과 별도로 확인합니다.');
  if(compatible===false)lines.push('서버의 음성 기능 버전이 이 화면과 일치하지 않습니다. 서버와 화면 버전을 확인하세요.');
  else if(compatible===true)lines.push('서버 음성 기능 버전이 이 화면과 일치합니다.');
  if(pending||speechIssue||browserOnline===false||['network_error','service_error','invalid_response'].includes(connectivity))lines.push(pending?'직접 입력도 서버 반영에는 연결이 필요합니다. 대기 요청 결과를 먼저 확인한 뒤 검토칸에서 작성하고 명시적으로 반영하세요.':'직접 입력도 서버 반영에는 연결이 필요합니다. 검토칸에 작성·보관하고 연결 확인 후 명시적으로 반영하세요.');
  if(checkedAt)lines.push('마지막 통신 확인 시각: '+checkedAt);
  return {tone,title,lines,checkLabel:connectivity==='checking'?'연결 확인 중…':'서버 연결 확인',manualLabel:'직접 입력 열기'};
}
