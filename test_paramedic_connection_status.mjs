import test from 'node:test';
import assert from 'node:assert/strict';
import {buildConnectionStatus} from './paramedic-ui/connection-status.mjs';

const state=overrides=>({connectivity:'unknown',browserOnline:null,pending:false,recovery:'unknown',localUnapplied:false,speechIssue:false,checkedAt:null,compatible:null,httpStatus:null,...overrides});
const text=model=>[model.title,...model.lines].join('\n');

test('default public model is explicit unknown, with a read-only check label and manual-entry label',()=>{
  const result=buildConnectionStatus();assert.deepEqual(result,{tone:'neutral',title:'연결 상태 확인 전',lines:['서버 API 응답은 아직 확인되지 않았습니다.','브라우저 임시 저장 상태는 아직 확인되지 않았습니다.'],checkLabel:'서버 연결 확인',manualLabel:'직접 입력 열기'});
});
for(const [connectivity,tone,title,first]of [
  ['unknown','neutral','연결 상태 확인 전','서버 API 응답은 아직 확인되지 않았습니다.'],
  ['checking','info','서버 연결 확인 중','서버 API 응답을 확인하고 있습니다.'],
  ['reachable','success','서버 응답 확인됨','서버 API 응답을 받았습니다. 입력 저장 완료는 저장 발화와 환자 요약에서 확인하세요.'],
  ['network_error','danger','서버 연결 확인 필요','서버 API 응답을 받지 못했습니다. 네트워크·서버 실행·주소·브라우저 연결 설정을 확인하세요.'],
  ['service_error','danger','서버 처리 오류 확인 필요','서버 오류 응답을 받았습니다. 서버 상태를 확인하고 서버 연결 확인을 눌러 주세요.'],
  ['invalid_response','danger','서버 응답 형식 확인 필요','서버 응답 형식을 읽지 못했습니다. 서버 주소와 실행 중인 앱을 확인하세요.'],
])test(`${connectivity} has its distinct presentation without treating API response as a committed input`,()=>{
  const result=buildConnectionStatus(state({connectivity}));assert.equal(result.tone,tone);assert.equal(result.title,title);assert.equal(result.lines[0],first);assert.doesNotMatch(text(result),/서버 저장 완료|환자 등록 완료|전송 완료|저장됐습니다/);
});
test('checking changes only the check-button label and never announces an automatic retry',()=>{
  const result=buildConnectionStatus(state({connectivity:'checking'}));assert.equal(result.checkLabel,'연결 확인 중…');assert.equal(result.manualLabel,'직접 입력 열기');assert.doesNotMatch(text(result),/자동.*재시도|자동.*연결|자동.*저장/);
});
test('browser offline alone is a hint, not evidence that the server is down',()=>{
  const result=buildConnectionStatus(state({browserOnline:false}));assert.equal(result.title,'연결 상태 확인 필요');assert.equal(result.tone,'warning');assert.ok(result.lines.includes('브라우저는 오프라인으로 표시합니다. 서버 연결은 API 응답으로 따로 확인합니다.'));assert.doesNotMatch(text(result),/서버 API 응답을 받지 못했습니다|서버가 중단|인터넷이 끊/);
});
test('a local API response may be reachable while the browser offline hint is false',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',browserOnline:false}));assert.equal(result.title,'서버 응답 확인됨');assert.equal(result.tone,'success');assert.match(text(result),/API 응답을 받았습니다/);assert.match(text(result),/브라우저는 오프라인/);assert.doesNotMatch(text(result),/응답을 받지 못|인터넷이 끊/);
});
test('browser online does not overwrite an API network failure',()=>{
  const result=buildConnectionStatus(state({connectivity:'network_error',browserOnline:true}));assert.equal(result.tone,'danger');assert.match(text(result),/API 응답을 받지 못/);assert.match(text(result),/브라우저는 온라인/);
});
for(const browserOnline of [true,false,null])test(`browser hint ${String(browserOnline)} never changes a known API service error into internet disconnection`,()=>{
  const result=buildConnectionStatus(state({connectivity:'service_error',browserOnline}));assert.equal(result.title,'서버 처리 오류 확인 필요');assert.doesNotMatch(text(result),/인터넷.*단절|인터넷이 끊|API 응답을 받지 못/);
});
for(const status of [200,201,401,403,404,409,422,429])test(`an HTTP ${status} response reported as reachable does not assert an accepted clinical write`,()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',httpStatus:status}));assert.equal(result.title,status>=400?'서버 응답 있음 · 요청 확인 필요':'서버 응답 확인됨');assert.equal(result.tone,status>=400?'warning':'success');assert.doesNotMatch(text(result),/인터넷이 끊|서버 저장 완료|환자 등록|병원 수용|전송 완료/);
});
for(const httpStatus of [400,401,403,404,409,499])test(`reachable HTTP ${httpStatus} explains request/permission/latest-record checks without claiming network disconnection`,()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',httpStatus}));assert.equal(result.title,'서버 응답 있음 · 요청 확인 필요');assert.equal(result.tone,'warning');assert.ok(result.lines.includes(`HTTP ${httpStatus} 응답을 받았습니다. 권한·요청 내용·최신 기록을 확인하세요. 연결 단절로 단정하지 않습니다.`));assert.doesNotMatch(text(result),/API 응답을 받지 못했습니다|인터넷이 끊/);
});
test('4xx context does not override recovery failure, exact pending requests, network errors or speech issues',()=>{
  for(const [extra,title]of [[{recovery:'failed'},'브라우저 임시 저장 확인 필요'],[{pending:true},'저장 결과 확인 필요'],[{connectivity:'network_error'},'서버 연결 확인 필요'],[{connectivity:'service_error'},'서버 처리 오류 확인 필요'],[{connectivity:'invalid_response'},'서버 응답 형식 확인 필요'],[{speechIssue:true},'음성 입력 확인 필요']])assert.equal(buildConnectionStatus(state({connectivity:'reachable',httpStatus:403,...extra})).title,title);
});
test('request-specific 4xx guidance remains visible beside an older voice compatibility warning',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',httpStatus:403,compatible:false}));assert.equal(result.title,'서버 응답 있음 · 요청 확인 필요');assert.match(text(result),/HTTP 403/);assert.match(text(result),/음성 기능 버전이 이 화면과 일치하지/);
});
test('5xx service error and invalid-response categories retain their separate meaning with status metadata',()=>{
  const service=buildConnectionStatus(state({connectivity:'service_error',httpStatus:503})),format=buildConnectionStatus(state({connectivity:'invalid_response',httpStatus:200}));assert.equal(service.title,'서버 처리 오류 확인 필요');assert.equal(format.title,'서버 응답 형식 확인 필요');assert.doesNotMatch(text(service)+text(format),/권한·요청 내용/);
});
for(const httpStatus of [null,undefined,0,99,600,-1,'403',403.5,NaN,Infinity,'PRIVATE_STATUS'])test(`invalid or unknown HTTP status ${String(httpStatus)} does not invent a 4xx failure`,()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',httpStatus}));assert.equal(result.title,'서버 응답 확인됨');assert.equal(result.tone,'success');assert.doesNotMatch(text(result),/HTTP |PRIVATE_STATUS/);
});
test('checking a new request does not present a prior HTTP status as the current result',()=>{
  const result=buildConnectionStatus(state({connectivity:'checking',httpStatus:403}));assert.equal(result.title,'서버 연결 확인 중');assert.doesNotMatch(text(result),/HTTP 403/);
});
test('invalid JSON response is different from receiving no API response',()=>{
  const result=buildConnectionStatus(state({connectivity:'invalid_response',browserOnline:true,status:200}));assert.match(text(result),/응답 형식을 읽지 못/);assert.doesNotMatch(text(result),/응답을 받지 못했습니다/);
});
for(const connectivity of ['unknown','checking','reachable','network_error','service_error','invalid_response'])test(`pending dominates the ${connectivity} headline and preserves the same-request guidance`,()=>{
  const result=buildConnectionStatus(state({connectivity,pending:true,recovery:'saved'}));assert.equal(result.title,'저장 결과 확인 필요');assert.equal(result.tone,'warning');assert.match(text(result),/같은 요청 재시도로/);assert.match(text(result),/처리 확인이 필요한 요청/);assert.doesNotMatch(text(result),/서버에 이미 보낸|서버 저장 완료|자동.*재시도/);
});
test('reachable plus a pending write still says that processing must be verified',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',pending:true,recovery:'saved'}));assert.equal(result.title,'저장 결과 확인 필요');assert.ok(result.lines.some(line=>line.includes('API 응답을 받았습니다')));assert.ok(result.lines.some(line=>line.includes('처리 결과를 확인')));
});
for(const connectivity of ['unknown','checking','reachable','network_error','service_error','invalid_response'])test(`local recovery failure dominates ${connectivity} without declaring the server write uncommitted`,()=>{
  const result=buildConnectionStatus(state({connectivity,pending:true,recovery:'failed',speechIssue:true,compatible:false}));assert.equal(result.title,'브라우저 임시 저장 확인 필요');assert.equal(result.tone,'danger');assert.match(text(result),/현재 화면을 유지/);assert.match(text(result),/원문을 복사해 보관/);assert.doesNotMatch(text(result),/서버에 저장되지 않았|서버에 보내지 않았|인터넷이 끊/);
});
test('RECOVERY_CHECKPOINT_FAILED is presented as browser storage trouble, not a network error',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',pending:true,recovery:'failed',code:'RECOVERY_CHECKPOINT_FAILED'}));assert.equal(result.title,'브라우저 임시 저장 확인 필요');assert.equal(result.lines[0],'서버 API 응답을 받았습니다. 입력 저장 완료는 저장 발화와 환자 요약에서 확인하세요.');assert.match(text(result),/임시 저장 상태를 확인한 뒤 같은 요청/);
});
test('verified browser save remains distinct from a committed server input',()=>{
  const result=buildConnectionStatus(state({recovery:'saved',connectivity:'network_error',localUnapplied:true}));assert.match(text(result),/최근 브라우저 임시 복구본 저장을 확인/);assert.match(text(result),/서버 저장 결과는 별도로 확인/);assert.match(text(result),/API 응답을 받지 못/);assert.doesNotMatch(text(result),/서버 저장 완료|병원으로 전송/);
});
test('unknown recovery never pretends that the current text has a durable local backup',()=>{
  const result=buildConnectionStatus(state({recovery:'unknown',localUnapplied:true}));assert.match(text(result),/임시 저장 상태는 아직 확인되지/);assert.doesNotMatch(text(result),/복구본 저장을 확인했습니다/);
});
test('unapplied text is kept for explicit later submission, never promised to be automatically sent',()=>{
  const result=buildConnectionStatus(state({localUnapplied:true,connectivity:'network_error'}));assert.match(text(result),/검토칸에서 작성·보관/);assert.match(text(result),/연결 확인 후 반영 버튼/);assert.match(text(result),/직접 입력도 서버 반영에는 연결이 필요/);assert.doesNotMatch(text(result),/연결되면 자동|자동 전송|자동 저장/);
});
test('pending and unapplied flags prioritize exact request result checking before editing more content',()=>{
  const result=buildConnectionStatus(state({pending:true,localUnapplied:true}));assert.match(text(result),/대기 요청의 처리 결과를 먼저 확인/);assert.ok(result.lines.includes('직접 입력도 서버 반영에는 연결이 필요합니다. 대기 요청 결과를 먼저 확인한 뒤 검토칸에서 작성하고 명시적으로 반영하세요.'));assert.doesNotMatch(text(result),/서버에 아직 저장되지 않은|새 요청으로|검토칸에 작성·보관하고/);
});
test('speech failure does not convert successful API response into API failure',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',speechIssue:true,recovery:'saved'}));assert.equal(result.title,'음성 입력 확인 필요');assert.equal(result.tone,'warning');assert.match(text(result),/API 응답을 받았습니다/);assert.match(text(result),/마이크 권한·브라우저 음성 서비스/);assert.match(text(result),/음성 입력 상태는 서버 API 연결과 별도/);assert.doesNotMatch(text(result),/API 응답을 받지 못/);
});
for(const connectivity of ['network_error','service_error','invalid_response'])test(`API ${connectivity} headline is preserved alongside a speech issue`,()=>{
  const result=buildConnectionStatus(state({connectivity,speechIssue:true}));assert.equal(result.tone,'danger');assert.notEqual(result.title,'음성 입력 확인 필요');assert.match(text(result),/마이크 권한/);
});
test('valid API response with the wrong voice revision is a compatibility warning',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',compatible:false}));assert.equal(result.title,'음성 기능 버전 확인 필요');assert.equal(result.tone,'warning');assert.match(text(result),/API 응답을 받았습니다/);assert.match(text(result),/음성 기능 버전이 이 화면과 일치하지 않습니다/);assert.doesNotMatch(text(result),/음성 인식 정상|음성 서비스 연결 완료/);
});
test('speech issues take priority over voice compatibility while both distinct messages remain visible',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',speechIssue:true,compatible:false}));assert.equal(result.title,'음성 입력 확인 필요');assert.match(text(result),/음성 기능 버전이 이 화면과 일치하지/);
});
test('a matching voice revision is not presented as proof of speech-service health',()=>{
  const result=buildConnectionStatus(state({connectivity:'reachable',compatible:true}));assert.match(text(result),/음성 기능 버전이 이 화면과 일치합니다/);assert.doesNotMatch(text(result),/마이크 정상|음성 인식 정상|음성 서비스 정상/);
});
test('unknown compatibility makes no unsupported version claim',()=>{
  for(const compatible of [null,undefined,'PRIVATE_UNKNOWN']){const result=buildConnectionStatus(state({connectivity:'reachable',compatible}));assert.doesNotMatch(text(result),/버전이 이 화면과 일치합니다|버전이 이 화면과 일치하지 않습니다|PRIVATE_UNKNOWN/);}
});
test('the last check is readable fixed-KST attempt time, not a server-success timestamp',()=>{
  const result=buildConnectionStatus(state({connectivity:'network_error',checkedAt:1_700_000_000_125}));assert.ok(result.lines.includes('마지막 통신 확인 시각: 2023-11-15 07:13:20 KST'));assert.doesNotMatch(text(result),/마지막 성공|epoch|\.125|\+09:00/);
});
test('checkedAt zero is an actual check time rather than falsy missing data',()=>assert.ok(buildConnectionStatus(state({checkedAt:0})).lines.includes('마지막 통신 확인 시각: 1970-01-01 09:00:00 KST')));
for(const checkedAt of [null,undefined,-1,NaN,Infinity,'1700000000000',1e16])test(`invalid or missing check timestamp ${String(checkedAt)} does not fabricate a check time`,()=>assert.equal(buildConnectionStatus(state({checkedAt})).lines.some(line=>line.startsWith('마지막 통신 확인 시각:')),false));
test('checking may retain an earlier attempt timestamp without falsely saying it succeeded',()=>{
  const result=buildConnectionStatus(state({connectivity:'checking',checkedAt:1_700_000_000_000}));assert.equal(result.checkLabel,'연결 확인 중…');assert.match(text(result),/마지막 통신 확인 시각/);assert.doesNotMatch(text(result),/성공 시각/);
});
test('private actor/session/clinical/raw-error fields never appear in presentation',()=>{
  const result=buildConnectionStatus(state({connectivity:'service_error',actorId:'PRIVATE_ACTOR',sessionId:'PRIVATE_SID',raw:'PRIVATE_CLINICAL',message:'PRIVATE_SERVER_MESSAGE',server:'PRIVATE_SERVER'}));assert.doesNotMatch(JSON.stringify(result),/PRIVATE_|actorId|sessionId|PRIVATE_SERVER_MESSAGE/);
});
test('unrecognized enum values and truthy non-booleans fail toward unknown rather than claiming saved/connected',()=>{
  const result=buildConnectionStatus({connectivity:'PRIVATE_CONNECTED',browserOnline:'yes',pending:'true',recovery:'PRIVATE_SAVED',localUnapplied:1,speechIssue:'true',compatible:'true'});assert.deepEqual(result,buildConnectionStatus());
});
for(const input of [null,undefined,[],true,42,'PRIVATE_STATE'])test(`malformed state ${String(input)} renders the safe unknown model`,()=>assert.deepEqual(buildConnectionStatus(input),buildConnectionStatus()));
test('the pure model does not mutate source state and returns independent line arrays',()=>{
  const input=Object.freeze(state({connectivity:'network_error',recovery:'saved',localUnapplied:true})),first=buildConnectionStatus(input),second=buildConnectionStatus(input);first.lines.push('result mutation only');assert.notDeepEqual(first.lines,second.lines);assert.deepEqual(second,buildConnectionStatus(input));assert.equal(input.localUnapplied,true);
});
test('independent browser hints never erase the same exact pending/recovery instructions',()=>{
  const input=state({connectivity:'reachable',pending:true,recovery:'failed',localUnapplied:true});for(const browserOnline of [null,true,false]){const model=buildConnectionStatus({...input,browserOnline});assert.equal(model.title,'브라우저 임시 저장 확인 필요');assert.match(text(model),/같은 요청 재시도/);assert.match(text(model),/원문을 복사해 보관/);assert.equal(model.manualLabel,'직접 입력 열기');}
});
