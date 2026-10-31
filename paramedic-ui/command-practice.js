import {VoiceCapture} from './speech.mjs';
import {requestId} from './voice-client.mjs';
import {findVoiceTrigger} from './recording-control.mjs';
import {PRACTICE_KEY,PRACTICE_CASES,selectPracticeCases,createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,scorePracticeAttempts,readPracticeDataset,writePracticeDataset,importPracticeJSON,exportPracticeJSON,exportPracticeCSV,mergePracticeDatasets,annotatePracticeAttempt,attachPracticeAudio} from './command-practice-data.mjs';
import {PracticeAudioCapture,makePracticeAudioStore,describePracticeAudio} from './practice-audio.mjs';
import {buildPracticeAudioBundle} from './practice-audio-bundle.mjs';
import {CommandCommitGate} from './command-commit.mjs';

const $=id=>document.getElementById(id);
const ACTIONS={stop:'녹음 종료',recommend:'병원 추천',new_patient:'새 환자',previous_patient:'이전 환자',handoff_summary:'인계 요약'};
const STATUSES={ok:'인식문 있음',no_match:'확정 인식문 없음',error:'음성 오류',aborted:'사용자·페이지 중단'};
const FAMILIES={stop:'종료 명령',recommend:'추천 명령',new_patient:'새 환자 명령',previous_patient:'이전 환자 명령',handoff_summary:'인계 요약 명령',negation:'부정·금지',quoted_reported:'인용·보고',clinical_overlap:'처치·상태 설명',multiple:'여러 명령',late_final:'늦게 붙는 부정·보고',long:'긴 복합 문장'};
let dataset=emptyPracticeDataset(),storageRaw=null,storageFailed=false,storageInvalid=false;
let queue=[],current=null,selectedId=null,active=null,stopping=false,ticker=null,shuffleSeed=null;
let mutationRevision=0,exportedRevision=-1;
const audioAssets=new Map(),audioStore=makePracticeAudioStore();
let audioBusy=false,audioStorageFailed=false,archiveReceipt=null,audioPlayerURL=null;
const AUDIO_BATCH_LIMIT=128*1024*1024;
const node=(tag,text,className)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;};
const clone=value=>JSON.parse(JSON.stringify(value));
function audioIdentity(value){
  const ignore=new Set(['expectedAction','referenceConfirmed','notes','imported','audio','actualAudioSaved']);
  const stable=item=>item&&typeof item==='object'?Array.isArray(item)?item.map(stable):Object.fromEntries(Object.keys(item).sort().map(key=>[key,stable(item[key])])):item;
  return JSON.stringify(stable(Object.fromEntries(Object.entries(value).filter(([key])=>!ignore.has(key)))));
}
const labelAction=action=>action===null?'명령 없음':ACTIONS[action]||'정답 미지정';
const fromSelect=value=>value==='unlabelled'?undefined:value==='none'?null:value;
const toSelect=value=>value===undefined?'unlabelled':value===null?'none':value;
function error(text=''){$('practice-error').textContent=text;$('practice-error').hidden=!text;}
function status(text){$('practice-status').textContent=text;}
function storage(){try{return localStorage;}catch{return null;}}
function locked(){return !!active||stopping||audioBusy;}
function hasAudio(){return dataset.attempts.some(item=>item.audio&&item.audio.status!=='not_recorded');}
function backedUp(){return hasAudio()?archiveReceipt?.revision===mutationRevision:exportedRevision===mutationRevision;}
function selected(){return dataset.attempts.find(item=>item.id===selectedId)||null;}
function controls(){
  const busy=locked(),blocked=storageFailed||storageInvalid;
  for(const id of ['group','action','search','case','failed-only','shuffle','next','repeat','custom-enabled','reference','expected','speaker','device','noise','style','mask','distance','pair','autostop','manual','fill-manual','import','audio-enabled','command-policy','command-wait'])$('practice-'+id).disabled=busy;
  $('practice-reference').readOnly=!$('practice-custom-enabled').checked;
  $('practice-record').disabled=stopping||audioBusy||blocked||(!active&&audioStorageFailed&&$('practice-audio-enabled').checked)||(!active&&dataset.attempts.length>=500)||(!active&&!VoiceCapture.capabilities().browser)||(!active&&!current&&!$('practice-custom-enabled').checked);
  $('practice-record').textContent=active?'말하기 마치기':'말하기 시작';
  $('practice-abort').disabled=!active;
  $('practice-simulate').disabled=busy||blocked||dataset.attempts.length>=500||(!current&&!$('practice-custom-enabled').checked);
  for(const id of ['json','csv'])$('practice-'+id).disabled=busy||(!dataset.attempts.length&&!storageInvalid)||(id==='csv'&&storageInvalid)||(id==='json'&&storageInvalid&&typeof storageRaw!=='string');
  $('practice-save-retry').hidden=!(storageFailed||audioStorageFailed)||storageInvalid;$('practice-save-retry').disabled=busy;
  $('practice-new-batch').disabled=busy||!backedUp()||!$('practice-export-confirmed').checked;
  $('practice-export-confirmed').disabled=busy||!backedUp();
  $('practice-bundle').disabled=busy||storageInvalid||!dataset.attempts.length;
  $('practice-audio-recover').disabled=busy||blocked;
  $('practice-audio-cleanup').disabled=busy||!hasAudio();
  $('practice-audio-download').disabled=busy||!audioAssets.get(selectedId)?.blob;
  $('practice-raw-export').disabled=busy||!$('practice-raw').value;
  $('practice-trace-export').disabled=busy||!selected()?.commandTrace;
  $('practice-annotation-save').disabled=busy||blocked||!selected();
  $('practice-verify-next').disabled=busy||blocked||!selected()?.referenceText||selected()?.referenceConfirmed===true;
  $('practice-verify-next').textContent=selected()?.source==='manual_simulation'?'모의 정답 확인 후 다음':'대본대로 말함·정답 확인 후 다음';
  for(const id of ['annotation-action','annotation-verified','annotation-note'])$('practice-'+id).disabled=busy||blocked||!selected();
  if(selected()&&!selected().referenceText)$('practice-annotation-verified').disabled=true;
}
function persistDataset(next){
  mutationRevision++;archiveReceipt=null;$('practice-export-confirmed').checked=false;
  dataset=next;
  try{const saved=writePracticeDataset(storage(),dataset,{expectedRaw:storageRaw});storageRaw=saved.raw;dataset=saved.dataset;storageFailed=false;error();return true;}
  catch(err){storageFailed=true;error('브라우저 보관을 확인하지 못했습니다. 새 결과는 이 화면 메모리에 유지했습니다. JSON을 먼저 내려받고 보관을 다시 시도하세요. '+err.message);return false;}
  finally{renderDataset();controls();}
}
function failedCaseIds(){return [...new Set(dataset.attempts.filter(item=>item.status!=='ok'||(item.referenceConfirmed&&Object.hasOwn(item,'expectedAction')&&(item.parserResult?.action??null)!==item.expectedAction)).map(item=>item.caseId).filter(Boolean))];}
function rebuildQueue(){
  if(locked())return;
  queue=selectPracticeCases({group:$('practice-group').value,action:$('practice-action').value,search:$('practice-search').value,
    failedCaseIds:$('practice-failed-only').checked?failedCaseIds():null,shuffleSeed});
  const previous=$('practice-case').value;$('practice-case').replaceChildren(...queue.map(item=>{const option=node('option',item.title+' · '+item.referenceText.slice(0,30));option.value=item.id;return option;}));
  $('practice-case').value=queue.some(item=>item.id===previous)?previous:queue[0]?.id||'';selectCase();
}
function selectCase(){
  if(locked())return;
  current=queue.find(item=>item.id===$('practice-case').value)||null;
  if(!$('practice-custom-enabled').checked){$('practice-reference').value=current?.referenceText||'';$('practice-expected').value=toSelect(current?.expectedAction);}
  $('practice-prompt').textContent=$('practice-custom-enabled').checked?($('practice-reference').value||'자유 발화: 정답 문장을 비우고 수집할 수 있습니다. 나중에 원문과 의도를 검토하세요.'):current?.referenceText||'선택한 조건에 맞는 문장이 없습니다. 필터를 바꾸거나 직접 만든 문장으로 수집하세요.';
  controls();
}
function nextCase(){if(locked())return;const index=queue.findIndex(item=>item.id===$('practice-case').value);$('practice-case').value=queue[(index+1)%queue.length]?.id||'';selectCase();}
function contextInput(source){
  const custom=$('practice-custom-enabled').checked,expected=fromSelect($('practice-expected').value),reference=$('practice-reference').value;
  const snapshot=!custom&&current?Object.fromEntries(['id','family','title','referenceText','expectedAction'].map(key=>[key,current[key]])):null;
  const caseId=snapshot?.id||'',speakerId=$('practice-speaker').value,pairId=$('practice-pair').value;
  return {id:'CP-'+requestId(),source,trialNo:dataset.attempts.filter(item=>item.caseId===caseId&&item.speakerId===speakerId&&item.pairId===pairId&&item.source===source).length+1,
    pairId,caseId,caseSnapshot:snapshot,referenceText:reference,referenceConfirmed:false,...(expected===undefined?{}:{expectedAction:expected}),
    speakerId,device:$('practice-device').value,browser:(navigator.userAgent||'').slice(0,512),condition:{noiseKind:$('practice-noise').value,mask:$('practice-mask').value,distanceCm:$('practice-distance').value===''?null:Number($('practice-distance').value),style:$('practice-style').value},
    autoStop:$('practice-autostop').checked&&$('practice-command-policy').value!=='observe',provisionalTriggerText:null,notes:''};
}
function renderCommandTrace(trace,pending=null){
  const last=trace?.events.findLast(event=>event.type==='commit'||event.type==='would_commit');
  $('practice-command-status').textContent=pending?`${labelAction(pending.trigger.action)} 후보 · ${pending.blocked?'후속 발화가 확정되기를 기다립니다.':'후속 문맥을 기다립니다. 녹음은 계속됩니다.'}`:last?`${last.type==='would_commit'?'가상 처리만 기록':'녹음 종료 요청 확정'} · ${labelAction(last.action)} · 시작 요청 후 ${last.atMs}ms`:'보류 중인 명령 후보 없음';
  $('practice-command-log').textContent=trace?`방식 ${trace.policy} · 대기 설정 ${trace.waitMs}ms · 수신 시점 기록 ${trace.events.length}개${trace.dropped?' · 생략 '+trace.dropped+'개':''}${trace.limited?' · 길이 제한':''}\n`+trace.events.slice(-16).map(event=>`${event.atMs}ms ${event.type}${event.action?' '+labelAction(event.action):''}${event.reason?' ('+event.reason+')':''}`).join('\n'):'새 녹음부터 명령 후보·취소·확정과 인식 조각의 수신 시점을 기록합니다.';
}
function practiceCommandSettings(){
  const requested=$('practice-command-policy').value,raw=$('practice-command-wait').value.trim(),waitMs=requested==='immediate'?0:Number(raw);
  if(!['deferred','immediate','observe'].includes(requested)||requested!=='immediate'&&(!raw||!Number.isInteger(waitMs)||waitMs<100||waitMs>5000))throw new Error('보류 시간은 100~5000ms의 정수로 입력하세요. 0ms는 즉시 처리 비교 모드에서만 사용합니다.');
  return {requested,waitMs};
}
function makePracticeGate(owner,{requested,waitMs}){
  const mode=owner.input.autoStop?requested:'observe';
  return new CommandCommitGate({mode,waitMs,now:()=>Date.now(),setTimer:(callback,ms)=>setTimeout(callback,ms),clearTimer:timer=>clearTimeout(timer),
    onEvent(){if(active===owner&&!owner.finalized&&owner.gate)renderCommandTrace(owner.gate.snapshot(),owner.gate.pending);},
    onCommit(payload){if(active!==owner||owner.finalized||owner.disposed||stopping)return;owner.committedTrigger=payload.trigger;
      if(capture.active)void stopPractice('trigger');}
  });
}
function preview(raw,early=null,caseId=active?.input.caseId,recordedResult=undefined){
  $('practice-raw').value=raw;const currentResult=findVoiceTrigger(raw),stored=recordedResult!==undefined,result=stored?recordedResult:currentResult,title=stored?'보관 당시 판정':'최종 판정';
  const changed=stored&&['action','prefix','command','suffix','index','end'].some(key=>result?.[key]!==currentResult?.[key]);
  $('practice-result').textContent=(caseId===undefined?'':`${caseId||'자유 발화'} · `)+(result?`${title}: ${labelAction(result.action)} · “${result.command}”`:`${title}: 명령 없음`)+(changed?` · 현재 재판정: ${labelAction(currentResult?.action??null)}`:'');
  $('practice-prefix').textContent=result?result.prefix||'(본문 없음)':raw||'—';$('practice-suffix').textContent=result?result.suffix||'(뒤 문장 없음)':'—';
  const first=early===null?null:findVoiceTrigger(early);
  $('practice-early-result').textContent=first?`최초 명령 후보: ${labelAction(first.action)}. ${early!==raw?'종료 전 추가 인식까지 보존했으며 최종 판정은 전체 인식문 기준입니다.':'연습 화면에서는 이 명령을 환자·병원에 실행하지 않았습니다.'}`:'';
}
function renderDataset(){
  const summary=scorePracticeAttempts(dataset.attempts);
  for(const name of ['total','human','manual','failed'])$('practice-counts-'+name).textContent=String(summary[name]);
  $('practice-coverage').textContent=`발화 유형 ${summary.coverage.length}개 수집 · 마이크 경로 정답 확인 ${summary.bySource.human_voice.verified}건 중 최종 텍스트 판정 일치 ${summary.bySource.human_voice.matched}건 · 수동 모의 정답 확인 ${summary.bySource.manual_simulation.verified}건 중 최종 텍스트 판정 일치 ${summary.bySource.manual_simulation.matched}건 · 가져온 자료 ${summary.imported}건은 원래 출처 집계에 포함됩니다. 오류·무인식·중단도 전체 시도에 포함합니다. 이 집계는 명령 결정·실행 시점의 정확도와 다르며 이벤트 기록과 따로 검토해야 합니다.`;
  $('practice-records').replaceChildren();
  for(const item of [...dataset.attempts].reverse().slice(0,80)){
    const row=node('article',undefined,'record'),body=node('div');body.append(node('strong',`${item.caseId||'자유 발화'} · ${item.source==='human_voice'?'마이크 경로':'수동 모의'}${item.imported?' · 가져온 자료':''} · ${STATUSES[item.status]} · ${item.trialNo}회차`),
      node('p',`${item.speakerId||'화자 미지정'} · ${item.condition.noiseKind} · 정답: ${labelAction(item.expectedAction)} · 판정: ${labelAction(item.parserResult?.action??null)}${item.referenceConfirmed?' · 정답 확인됨':' · 정답 미확인'} · 음성: ${item.audio?.status||'not_recorded'}${item.audio?.errorCode?' ('+item.audio.errorCode+')':''}`),node('p',item.rawText.slice(0,180)||'(확정 인식문 없음)'));
    const open=node('button','검토');open.type='button';open.disabled=locked();open.onclick=()=>{if(!locked())selectAttempt(item.id);};row.append(body,open);$('practice-records').append(row);
  }
  if(dataset.attempts.length>80)$('practice-records').append(node('p',`최근 80건을 표시합니다. 전체 ${dataset.attempts.length}건은 내려받기에 포함됩니다.`,'help'));
}
function selectAttempt(id){
  if(locked())return;const item=dataset.attempts.find(record=>record.id===id);if(!item)return;selectedId=id;
  preview(item.rawText,item.provisionalTriggerText,item.caseId,item.parserResult);$('practice-selected').textContent=`${item.id} · ${item.source==='human_voice'?'마이크 경로':'수동 모의'} · ${STATUSES[item.status]}`;
  $('practice-annotation-action').value=toSelect(item.expectedAction);$('practice-annotation-verified').checked=item.referenceConfirmed;$('practice-annotation-note').value=item.notes;
  renderCommandTrace(item.commandTrace);
  refreshAudioPlayer();controls();
}
function annotateSelected(advance=false){
  if(locked()||storageFailed||storageInvalid||!selected())return false;
  const item=selected();if(advance&&item.referenceConfirmed)return false;if(advance&&!item.referenceText){error('정답 문장이 없는 자유 발화는 미확인으로 보관하고 정답 검토에서 명령 의도를 따로 지정하세요.');return false;}
  try{const next=annotatePracticeAttempt(dataset,item.id,{expectedAction:fromSelect($('practice-annotation-action').value),referenceConfirmed:advance?true:$('practice-annotation-verified').checked,notes:$('practice-annotation-note').value});
    const saved=persistDataset(next);selectAttempt(item.id);if(saved){status('원문·출처·명령 판정은 유지하고 정답·메모만 보관했습니다.');if(advance)nextCase();}return saved;
  }catch(err){error(err.message);return false;}
}
function audioMetadata(descriptor,status='stored',errorCode=descriptor?.captureErrorCode||''){
  return {status,contentType:descriptor?.contentType||'',extension:descriptor?.extension||'',byteLength:descriptor?.byteLength||0,sha256:descriptor?.sha256||null,startedAt:descriptor?.startedAt??null,endedAt:descriptor?.endedAt??null,errorCode};
}
function refreshAudioPlayer(){
  if(audioPlayerURL){URL.revokeObjectURL(audioPlayerURL);audioPlayerURL=null;}
  const player=$('practice-audio-player'),asset=audioAssets.get(selectedId),item=selected();
  player.pause?.();player.removeAttribute?.('src');player.hidden=!asset?.blob;
  if(asset?.blob){audioPlayerURL=URL.createObjectURL(asset.blob);player.src=audioPlayerURL;}
  $('practice-audio-status').textContent=asset?.blob?`음성 ${asset.byteLength}바이트 · ${asset.contentType} · ${asset.durable?'IndexedDB 보관 검증됨':'화면 메모리만 보관 · ZIP을 먼저 내려받으세요'}${item?.audio?.errorCode?' · 불완전 녹음 경고: '+item.audio.errorCode:''}`:item?.audio?`음성 상태: ${item.audio.status}${item.audio.errorCode?' · '+item.audio.errorCode:''} · 재생 가능한 파일 없음`:'선택한 시도에 녹음 파일이 없습니다. 수동 모의에는 음성을 만들지 않습니다.';
}
const audioCapture=new PracticeAudioCapture({onState(state){if(active?.audioRequested)$('practice-audio-status').textContent='음성 파일: '+state;},onLimit(reason){if(active&&!stopping)void stopPractice(reason==='time_limit'?'time_limit':'error');}});
function baseAttempt(a,outcome,reason){const now=Date.now();return createPracticeAttempt({...a.input,fragments:[...a.fragments],rawText:a.fragments.join(' '),status:outcome,errorCode:a.errorCode||'',stopReason:reason,
  provisionalTriggerText:a.provisionalTriggerText,timing:{startedAt:a.startedAt,endedAt:now},...(a.gate?{commandTrace:a.gate.snapshot()}:{})},{now});}
function finishRecord(record,audio=null){
  let next=appendPracticeAttempt(dataset,record);if(audio)next=attachPracticeAudio(next,record.id,audio);
  selectedId=record.id;const saved=persistDataset(next);selectAttempt(record.id);
  status(saved?`${STATUSES[record.status]} · 시도 ${dataset.attempts.length}건을 보관했습니다. 녹음 파일 상태를 별도로 확인하세요.`:'시도는 화면 메모리에 있습니다. ZIP 또는 JSON을 먼저 내려받고 브라우저 보관을 다시 시도하세요.');
  if(audioStorageFailed)error('음성의 브라우저 보관에 실패했습니다. 파일은 이 화면 메모리에 유지합니다. ZIP으로 내보내거나 보관을 다시 시도하세요. 새 음성 녹음은 잠시 멈췄습니다.');
}
async function finishAudioPractice(a,outcome,reason){
  audioBusy=true;stopping=true;controls();
  try{
    const result=await audioCapture.stop({reason});if(a.disposed)return;
    const record=baseAttempt(a,outcome,reason);a.record=record;let metadata;
    if(result.attemptId===record.id&&result.blob?.size){
      const descriptor=await describePracticeAudio(record.id,result.blob,{startedAt:result.startedAt,endedAt:result.endedAt,captureErrorCode:result.errorCode||''});
      const asset={attemptId:record.id,availability:'available',blob:result.blob,...descriptor,snapshot:clone(record),durable:false};audioAssets.set(record.id,asset);
      try{const saved=await audioStore.put(record.id,result.blob,record,{startedAt:result.startedAt,endedAt:result.endedAt,captureErrorCode:result.errorCode||''});Object.assign(asset,saved,{durable:true});}
      catch{audioStorageFailed=true;}
      metadata=audioMetadata(descriptor,asset.durable?'stored':'memory_only',result.errorCode||'');
    }else metadata=audioMetadata(result,result.status==='empty'?'empty':result.status==='unavailable'?'unavailable':'failed',result.errorCode||'AUDIO_NOT_AVAILABLE');
    if(a.disposed)return;active=null;stopping=false;audioBusy=false;finishRecord(record,metadata);
  }catch(err){
    if(a.disposed||active!==a)return;
    active=null;stopping=false;audioBusy=false;
    try{finishRecord(baseAttempt(a,outcome,reason),audioMetadata(null,'failed',err.code||'AUDIO_SAVE_FAILED'));}catch{storageFailed=true;}
    error('음성 파일을 확정하지 못했습니다. 확정 인식문을 보관하고 원문 TXT도 내보낼 수 있습니다. '+err.message);
  }finally{renderDataset();refreshAudioPlayer();controls();}
}
function finalizePractice(outcome,reason){
  const a=active;if(!a||a.finalized)return; a.finalized=true;clearInterval(ticker);ticker=null;
  a.gate?.cancel(reason);
  const raw=a.fragments.join(' ');preview(raw,a.provisionalTriggerText);
  if(a.audioRequested)return finishAudioPractice(a,outcome,reason);
  active=null;stopping=false;
  try{
    finishRecord(baseAttempt(a,outcome,reason));
  }catch(err){storageFailed=true;error('시도를 자료로 묶지 못했습니다. 현재 원문 TXT를 먼저 내려받으세요. '+err.message);}
  renderDataset();controls();
}
const capture=new VoiceCapture({
  onInterim(text){if(active&&!active.finalized){active.gate?.interim(text);status('듣는 중 · 임시 인식: '+text);}},
  onFinal(text){
    const a=active;if(!a||a.finalized)return;a.fragments.push(text);const raw=a.fragments.join(' ');
    if(a.provisionalTriggerText===null&&findVoiceTrigger(raw))a.provisionalTriggerText=raw;
    preview(raw,a.provisionalTriggerText);
    a.gate?.final(text);if(a.gate)renderCommandTrace(a.gate.snapshot(),a.gate.pending);
    if(!stopping&&(raw.length>=12000||a.fragments.length>=240))void stopPractice('text_limit');
  },
  onState(state){
    if(state==='listening'&&active)status('말하는 중 · 버튼을 눌러 마치세요.');
    if(state==='idle'){const owner=active;owner?.gate?.mark('recognizer_end');queueMicrotask(async()=>{if(!owner||active!==owner||owner.finalized||stopping||capture.active)return;
      if(owner.errorCode)owner.gate?.cancel('error');else await owner.gate?.end('natural');
      if(active===owner&&!owner.finalized&&!stopping&&!capture.active)finalizePractice(owner.errorCode?'error':owner.fragments.length?'ok':'no_match',owner.errorCode?'error':'natural');});}
    controls();
  },
  onError(err){if(active){active.errorCode=/^[a-zA-Z0-9_-]{1,80}$/.test(err.code||'')?err.code:'recognition-error';active.gate?.cancel('error');const owner=active;queueMicrotask(()=>{if(active===owner&&!stopping)finalizePractice('error','error');});}error('음성 인식을 완료하지 못했습니다. 전달된 확정 인식문과 실패 상태를 함께 보관합니다. '+err.message);}
});
async function startPractice(){
  if(locked()||storageFailed||storageInvalid||dataset.attempts.length>=500||audioStorageFailed&&$('practice-audio-enabled').checked)return false;error();
  let commandSettings;try{commandSettings=practiceCommandSettings();}catch(err){error(err.message);return false;}
  if($('practice-audio-enabled').checked&&dataset.attempts.reduce((sum,item)=>sum+(item.audio?.byteLength||0),0)+20*1024*1024>AUDIO_BATCH_LIMIT){error('현재 묶음의 음성 용량이 가득 찼습니다. ZIP 파일을 확인한 뒤 새 수집 묶음을 시작하세요.');return false;}
  let owner=null;
  try{const input=contextInput('human_voice'),now=Date.now();createPracticeAttempt({...input,rawText:'',fragments:[],status:'no_match',stopReason:'natural',timing:{startedAt:now,endedAt:now}},{now});
    active={input,startedAt:now,fragments:[],provisionalTriggerText:null,errorCode:'',finalized:false,audioRequested:$('practice-audio-enabled').checked};preview('');controls();
    owner=active;owner.gate=makePracticeGate(owner,commandSettings);renderCommandTrace(owner.gate.snapshot());ticker=setInterval(()=>{if(active!==owner)return;const elapsed=Date.now()-owner.startedAt;if(elapsed>=180000){void stopPractice('time_limit');return;}if(capture.active) $('practice-record').textContent=`말하기 마치기 · ${Math.floor(elapsed/1000)}초`;},250);
    if(owner.audioRequested){status('녹음용 마이크 권한을 확인합니다. 준비되면 음성 인식을 시작합니다.');await audioCapture.start(input.id);if(active!==owner||owner.finalized)return false;}
    await capture.start('browser',{continuous:true});return true;
  }catch(err){if(owner&&(active!==owner||owner.disposed))return false;if(active===owner&&owner){owner.errorCode=err.code||'start-failed';await finalizePractice('error','error');}error('말하기를 시작하지 못했습니다. 마이크 권한·브라우저 지원을 확인하세요. '+err.message);controls();return false;}
}
async function stopPractice(reason='button'){
  if(!active||stopping)return false;const owner=active;stopping=true;controls();
  try{owner.gate?.mark('stop_requested');await capture.stop();owner.gate?.mark('stop_drained');}catch(err){if(active===owner)owner.errorCode=err.code||'stop-failed';}
  finally{if(active===owner){if(owner.errorCode)owner.gate?.cancel('error');else if(reason==='button')await owner.gate?.flush('button');else if(reason!=='trigger')owner.gate?.cancel(reason);
    if(active===owner)await finalizePractice(owner.errorCode?'error':owner.fragments.length?'ok':'no_match',owner.errorCode?'error':reason);}}
  return true;
}
function abortPractice(){if(!active)return false;const owner=active;owner.gate?.cancel('aborted');owner.finalized=true;capture.abort();owner.finalized=false;if(active===owner)finalizePractice('aborted','aborted');return true;}
function runManualPractice(){
  if(locked()||storageFailed||storageInvalid||dataset.attempts.length>=500)return false;error();
  try{const input=contextInput('manual_simulation'),text=$('practice-manual').value,now=Date.now();
    active={input,startedAt:now,fragments:text?[text]:[],provisionalTriggerText:null,errorCode:'',finalized:false};finalizePractice(text.trim()?'ok':'no_match','manual');return !storageFailed;
  }catch(err){error(err.message);controls();return false;}
}
function download(content,type,filename){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function exportDataset(format){
  if(locked())return false;try{if(storageInvalid&&typeof storageRaw!=='string')throw Error('저장소 원본을 읽지 못해 원본 JSON을 만들 수 없습니다. 브라우저의 저장 허용 상태를 확인하세요.');const content=format==='csv'?exportPracticeCSV(dataset):storageInvalid?storageRaw:exportPracticeJSON(dataset);download(content,format==='csv'?'text/csv;charset=utf-8':'application/json',`command-practice-${Date.now()}.${format}`);if(format==='json')exportedRevision=mutationRevision;status('메타데이터 파일 내려받기를 요청했습니다. JSON·CSV에는 음성 파일이 없습니다. 음성까지 보관하려면 ZIP을 선택하세요.');controls();return true;}catch(err){error(err.message);return false;}
}
function newPracticeBatch(){
  if(locked()||!backedUp()||!$('practice-export-confirmed').checked)return false;
  if(hasAudio()&&$('practice-audio-cleanup').checked)return clearBackedUpAudioAndStart();
  return resetPracticeBatch();
}
function resetPracticeBatch(){
  const previous=dataset,invalid=storageInvalid;storageInvalid=false;
  if(!persistDataset(emptyPracticeDataset())){dataset=previous;storageInvalid=invalid;renderDataset();controls();return false;}
  selectedId=null;audioAssets.clear();audioStorageFailed=false;refreshAudioPlayer();preview('');$('practice-selected').textContent='새 수집 묶음을 시작했습니다.';rebuildQueue();controls();status('파일 확인 후 새 수집 묶음을 준비했습니다. 선택하지 않은 기존 녹음은 IndexedDB에 유지됩니다.');return true;
}
async function clearBackedUpAudioAndStart(){
  const receipt=archiveReceipt;if(!resetPracticeBatch())return false;audioBusy=true;controls();
  try{await audioStore.deleteMany(receipt.audio);status('새 묶음을 준비하고, 확인한 ZIP에 포함된 녹음만 브라우저에서 정리했습니다.');return true;}
  catch(err){error('새 묶음은 준비했지만 기존 녹음 정리는 완료하지 못했습니다. 녹음 보관함 복구로 다시 확인할 수 있습니다. '+err.message);return false;}
  finally{audioBusy=false;controls();}
}
async function collectAudioAssets(){
  const assets=[];
  for(const item of dataset.attempts){
    let asset=audioAssets.get(item.id);
    if(!asset&&item.audio&&item.source==='human_voice'){try{const row=await audioStore.get(item.id);if(row){asset={attemptId:item.id,availability:'available',blob:row.blob,...row.descriptor,snapshot:row.snapshot,durable:true};}}catch{assets.push({attemptId:item.id,availability:'error',errorCode:'AUDIO_READ_FAILED'});continue;}}
    if(asset){if(audioIdentity(asset.snapshot)!==audioIdentity(item)||item.audio?.sha256&&item.audio.sha256!==asset.sha256||item.audio?.byteLength&&item.audio.byteLength!==asset.byteLength){audioAssets.delete(item.id);assets.push({attemptId:item.id,availability:'error',errorCode:'AUDIO_ASSOCIATION_MISMATCH'});continue;}audioAssets.set(item.id,asset);}
    assets.push(asset||{attemptId:item.id,availability:item.audio?.byteLength?'missing':['failed','unavailable','empty','interrupted'].includes(item.audio?.status)?'error':'not_recorded',...(item.audio?.errorCode?{errorCode:item.audio.errorCode}:{})});
  }
  return assets;
}
async function exportAudioBundle(){
  if(locked()||storageInvalid||!dataset.attempts.length)return false;
  audioBusy=true;controls();error();
  try{const revision=mutationRevision,assets=await collectAudioAssets(),bundle=await buildPracticeAudioBundle(dataset,assets);
    download(bundle.blob,'application/zip',bundle.filename);
    archiveReceipt={revision,audio:bundle.manifest.audio.filter(row=>row.included).map(row=>({attemptId:row.attemptId,sha256:row.sha256}))};
    $('practice-export-confirmed').checked=false;
    const count=bundle.manifest.audio.filter(row=>row.included).length,missing=bundle.manifest.audio.filter(row=>row.availability==='missing'||row.availability==='error').length;
    status(`ZIP 내려받기를 요청했습니다. 음성 ${count}개 포함 · 누락·오류 ${missing}건. 파일을 실제로 확인하세요. 인식문·정답·시험 조건·manifest를 함께 담았습니다.`);return true;
  }catch(err){archiveReceipt=null;error('ZIP을 만들지 못했습니다. 현재 자료는 유지합니다. '+err.message);return false;}
  finally{audioBusy=false;refreshAudioPlayer();controls();}
}
async function retryAudioStorage(){
  if(locked()||storageInvalid)return false;audioBusy=true;controls();let next=dataset,failed=false;
  try{for(const asset of audioAssets.values()){if(asset.durable)continue;try{const saved=await audioStore.put(asset.attemptId,asset.blob,asset.snapshot,{startedAt:asset.startedAt,endedAt:asset.endedAt,captureErrorCode:asset.captureErrorCode||''});Object.assign(asset,saved,{durable:true});if(next.attempts.some(item=>item.id===asset.attemptId))next=attachPracticeAudio(next,asset.attemptId,audioMetadata(saved));}catch{failed=true;}}
    audioStorageFailed=failed;const saved=persistDataset(next);if(saved&&!failed)status('텍스트와 음성의 브라우저 보관을 다시 확인했습니다.');if(failed)error('아직 보관하지 못한 음성은 메모리에 있습니다. 먼저 ZIP을 내려받으세요.');return saved&&!failed;
  }finally{audioBusy=false;refreshAudioPlayer();controls();}
}
async function verifyRestoredAudio(){
  if(!hasAudio()||locked())return;audioBusy=true;controls();let next=dataset,changed=false;
  try{const assets=await collectAudioAssets();for(const asset of assets){const item=next.attempts.find(row=>row.id===asset.attemptId);if(!item?.audio)continue;
      const metadata=asset.blob?audioMetadata(asset,asset.durable?'stored':'memory_only'):item.audio.byteLength?{...item.audio,status:'missing',errorCode:'AUDIO_BYTES_MISSING'}:item.audio;
      if(JSON.stringify(metadata)!==JSON.stringify(item.audio)){next=attachPracticeAudio(next,item.id,metadata);changed=true;}}
    if(changed)persistDataset(next);
  }catch(err){error('복원된 음성을 확인하지 못했습니다. ZIP의 누락 목록을 확인하세요. '+err.message);}
  finally{audioBusy=false;refreshAudioPlayer();controls();}
}
async function recoverAudioTrials(){
  if(locked()||storageFailed||storageInvalid)return false;audioBusy=true;controls();
  try{const rows=await audioStore.list();let next=dataset,count=0;
    for(const row of rows){if(next.attempts.some(item=>item.id===row.attemptId))continue;next=appendPracticeAttempt(next,row.snapshot);next=attachPracticeAudio(next,row.attemptId,audioMetadata(row.descriptor));count++;}
    let restored=0;
    for(const row of rows){const item=next.attempts.find(item=>item.id===row.attemptId);if(item&&audioIdentity(item)===audioIdentity(row.snapshot)&&(!item.audio?.sha256||item.audio.sha256===row.descriptor.sha256)){
      audioAssets.set(row.attemptId,{attemptId:row.attemptId,availability:'available',blob:row.blob,...row.descriptor,snapshot:row.snapshot,durable:true});
      const metadata=audioMetadata(row.descriptor);if(JSON.stringify(item.audio)!==JSON.stringify(metadata)){next=attachPracticeAudio(next,item.id,metadata);restored++;}}}
    if(count||restored)persistDataset(next);status(`녹음 보관함에서 누락 시도 ${count}건·음성 연결 ${restored}건을 복구했습니다. 기존 정답 검토는 유지했습니다.`);return true;
  }catch(err){error('녹음 보관함을 복구하지 못했습니다. 기존 목록은 유지합니다. '+err.message);return false;}
  finally{audioBusy=false;renderDataset();refreshAudioPlayer();controls();}
}
function importDataset(text){
  if(locked()||storageFailed||storageInvalid)return false;
  try{const imported=importPracticeJSON(text),before=dataset.attempts.length,next=mergePracticeDatasets(dataset,imported),saved=persistDataset(next);if(saved)status(`${dataset.attempts.length-before}건을 합쳤습니다. 동일 기록은 중복 추가하지 않았고 출처를 유지했습니다. JSON에는 음성이 없어 이 브라우저의 보관함을 따로 확인합니다.`);if(!selectedId&&dataset.attempts.length)selectAttempt(dataset.attempts.at(-1).id);void verifyRestoredAudio();return saved;}catch(err){error('자료를 합치지 않았습니다. 기존 기록은 유지했습니다. '+err.message);return false;}
}
function restoreContext(item){if(!item)return;for(const [id,value]of Object.entries({speaker:item.speakerId,device:item.device,noise:item.condition.noiseKind,style:item.condition.style,mask:item.condition.mask,distance:item.condition.distanceCm??'',pair:item.pairId}))$('practice-'+id).value=String(value);}
function initialize(){
  const loaded=readPracticeDataset(storage());storageRaw=loaded.raw;
  if(loaded.status==='valid'){dataset=loaded.dataset;restoreContext(dataset.attempts.at(-1));}
  else if(loaded.status==='invalid'){storageFailed=true;storageInvalid=true;error('저장된 수집 자료를 읽지 못했습니다. 덮어쓰지 않았습니다. 전체 JSON으로 원본을 보관하고 기존 파일을 확인하세요. '+loaded.message);}
  const families=[...new Set(PRACTICE_CASES.map(item=>item.family))];$('practice-group').replaceChildren(...['all',...families].map(value=>{const option=node('option',value==='all'?'모든 유형':FAMILIES[value]||value);option.value=value;return option;}));$('practice-group').value='all';
  rebuildQueue();renderDataset();if(dataset.attempts.length)selectAttempt(dataset.attempts.at(-1).id);
  status(loaded.status==='valid'?`${dataset.attempts.length}건을 복원했습니다. 실제 마이크 시도와 수동 모의는 따로 집계합니다.`:'대본을 고르고 말하기 시작을 누르세요. 종료하면 시도를 자동 보관합니다.');controls();
  void verifyRestoredAudio();
}
for(const name of ['group','action','failed-only'])$('practice-'+name).onchange=rebuildQueue;
$('practice-search').oninput=rebuildQueue;$('practice-case').onchange=selectCase;
$('practice-shuffle').onclick=()=>{if(locked())return;shuffleSeed=(Date.now()>>>0);rebuildQueue();status('문장 순서를 섞었습니다. 필터와 정답은 유지했습니다.');};
$('practice-next').onclick=nextCase;$('practice-repeat').onclick=()=>{if(!locked()){selectCase();status('같은 문장을 준비했습니다. 조건을 하나씩 바꾸거나 다시 말해 보세요.');}};
$('practice-custom-enabled').onchange=()=>{if(locked())return;if($('practice-custom-enabled').checked){$('practice-reference').value='';$('practice-expected').value='unlabelled';}selectCase();};
$('practice-reference').oninput=()=>{if(!locked()&&$('practice-custom-enabled').checked)selectCase();};
$('practice-record').onclick=()=>active?stopPractice():startPractice();$('practice-abort').onclick=abortPractice;
$('practice-fill-manual').onclick=()=>{if(!locked())$('practice-manual').value=$('practice-reference').value;};$('practice-simulate').onclick=runManualPractice;
$('practice-annotation-save').onclick=()=>annotateSelected();$('practice-verify-next').onclick=()=>annotateSelected(true);
$('practice-save-retry').onclick=retryAudioStorage;
$('practice-audio-enabled').onchange=controls;
$('practice-trace-export').onclick=()=>{const item=selected();if(!locked()&&item?.commandTrace)download(exportPracticeJSON(appendPracticeAttempt(emptyPracticeDataset(),item)),'application/json',`command-trial-${encodeURIComponent(item.id)}.json`);};
$('practice-audio-recover').onclick=recoverAudioTrials;
$('practice-bundle').onclick=exportAudioBundle;
$('practice-audio-download').onclick=()=>{const asset=audioAssets.get(selectedId);if(!locked()&&asset?.blob)download(asset.blob,asset.contentType,encodeURIComponent(asset.attemptId)+'.'+asset.extension);};
$('practice-json').onclick=()=>exportDataset('json');$('practice-csv').onclick=()=>exportDataset('csv');
$('practice-export-confirmed').onchange=controls;$('practice-new-batch').onclick=newPracticeBatch;
$('practice-raw-export').onclick=()=>{if(!locked()&&$('practice-raw').value)download($('practice-raw').value,'text/plain;charset=utf-8',`command-practice-raw-${Date.now()}.txt`);};
$('practice-import').onchange=async event=>{const file=event.target.files?.[0];if(!file||locked())return;try{if(file.size>4*1024*1024)throw Error('파일이 4MiB 상한을 넘습니다.');importDataset(await file.text());}catch(err){error(err.message);}finally{$('practice-import').value='';}};
window.addEventListener('storage',event=>{if(event.key!==PRACTICE_KEY||event.newValue===storageRaw)return;if(active)abortPractice();storageFailed=true;error('다른 탭이 자료를 바꿔 이 탭의 보관을 멈췄습니다. 이 화면의 JSON을 내려받고 새로고침 후 합치세요.');controls();});
window.addEventListener('beforeunload',event=>{if(active||audioBusy||(storageFailed||audioStorageFailed)&&dataset.attempts.length){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{if(active){const owner=active;owner.gate?.cancel('page_exit');owner.finalized=true;owner.disposed=true;capture.abort();audioCapture.cancel();active=null;stopping=false;audioBusy=false;clearInterval(ticker);ticker=null;
    try{if(!dataset.attempts.some(item=>item.id===owner.input.id))finishRecord(owner.record||baseAttempt(owner,'aborted','page_exit'),owner.audioRequested?audioMetadata(null,'interrupted','AUDIO_PAGE_EXIT'):null);}catch{storageFailed=true;}}
  audioCapture.cancel();capture.stopSpeaking();if(audioPlayerURL){URL.revokeObjectURL(audioPlayerURL);audioPlayerURL=null;}});
initialize();
