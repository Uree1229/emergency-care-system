import {readDraftHandoff,writeDraftHandoff} from '/paramedic/draft-handoff.mjs';

const id=(value,max,actor=false)=>typeof value==='string'&&value.length>0&&value.length<=max&&(actor?/^[A-Za-z0-9][A-Za-z0-9._:@-]*$/:/^[A-Za-z0-9][A-Za-z0-9_-]*$/).test(value);
export function parseDraftEntry(href){
  const url=new URL(href),allowed=new Set(['sid','actor_id','version']);
  if(!['http:','https:'].includes(url.protocol)||url.username||url.password||[...url.searchParams.keys()].some(key=>!allowed.has(key))||new Set(url.searchParams.keys()).size!==[...url.searchParams.keys()].length)throw Error('기록 연결 주소를 확인하세요.');
  const sid=url.searchParams.get('sid'),actorId=url.searchParams.get('actor_id'),raw=url.searchParams.get('version');
  if(!id(sid,128)||!id(actorId,80,true)||!raw||!/^[1-9][0-9]*$/.test(raw)||!Number.isSafeInteger(Number(raw)))throw Error('기록 연결 정보를 확인하세요.');
  return {origin:url.origin,sid,actorId,version:Number(raw)};
}

/** Restore one existing draft reference only. No patient creation or clinical POST. */
export async function openConsoleDraft({href,storage,fetchImpl,navigate,isCurrent=()=>true,signal}){
  const target=parseDraftEntry(href);
  const previous=readDraftHandoff(storage,target.origin);
  if(previous.status==='invalid'||previous.status==='valid'&&(previous.payload.unappliedText||previous.payload.sessionId!==target.sid||previous.payload.actorId!==target.actorId))throw Error('먼저 열어둔 초안의 미반영 입력을 확인하세요.');
  const path=`${target.origin}/api/voice/sessions/${encodeURIComponent(target.sid)}?actor_id=${encodeURIComponent(target.actorId)}`;
  const response=await fetchImpl(path,{method:'GET',cache:'no-store',signal});
  if(!response.ok)throw Error('초안을 불러오지 못했습니다. 연결 상태를 확인하세요.');
  const {session}=await response.json();
  if(!isCurrent())return false;
  if(!session||session.id!==target.sid||session.actor_id!==target.actorId||session.state!=='DRAFT'||session.patient_id||!Number.isSafeInteger(session.version)||session.version<target.version)throw Error('초안 상태가 바뀌었습니다. 관제 화면에서 다시 선택하세요.');
  // A later local handoff must not be replaced by an old, delayed GET.
  const latest=readDraftHandoff(storage,target.origin);
  if(JSON.stringify(latest)!==JSON.stringify(previous))throw Error('다른 화면에서 기록 연결이 바뀌었습니다. 다시 선택하세요.');
  writeDraftHandoff(storage,{serverOrigin:target.origin,actorId:target.actorId,sessionId:target.sid,version:session.version,unappliedText:'',intent:'recommend'});
  if(!isCurrent())return false;
  navigate('/paramedic/?voice_handoff=1&flow=field');return true;
}

if(typeof document!=='undefined'){
  let generation=0;
  const run=async()=>{
    const token=++generation,button=document.getElementById('entry-retry'),status=document.getElementById('entry-status');
    button.hidden=true;status.textContent='기록 불러오는 중';
    try{await openConsoleDraft({href:location.href,storage:sessionStorage,fetchImpl:fetch.bind(globalThis),navigate:url=>location.replace(url),isCurrent:()=>token===generation});}
    catch(error){if(token===generation){status.textContent=error.message;button.hidden=false;}}
  };
  document.getElementById('entry-retry').onclick=run;
  addEventListener('pagehide',()=>generation++);
  void run();
}
