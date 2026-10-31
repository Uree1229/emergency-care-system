import {VoiceCapture} from './speech.mjs';
import {requestId} from './voice-client.mjs';
import {findVoiceTrigger} from './recording-control.mjs';
import {PRACTICE_KEY,PRACTICE_CASES,selectPracticeCases,createPracticeAttempt,emptyPracticeDataset,appendPracticeAttempt,scorePracticeAttempts,readPracticeDataset,writePracticeDataset,importPracticeJSON,exportPracticeJSON,exportPracticeCSV,mergePracticeDatasets,annotatePracticeAttempt} from './command-practice-data.mjs';

const $=id=>document.getElementById(id);
const ACTIONS={stop:'녹음 종료',recommend:'병원 추천',new_patient:'새 환자',previous_patient:'이전 환자',handoff_summary:'인계 요약'};
const STATUSES={ok:'인식문 있음',no_match:'확정 인식문 없음',error:'음성 오류',aborted:'사용자·페이지 중단'};
const FAMILIES={stop:'종료 명령',recommend:'추천 명령',new_patient:'새 환자 명령',previous_patient:'이전 환자 명령',handoff_summary:'인계 요약 명령',negation:'부정·금지',quoted_reported:'인용·보고',clinical_overlap:'처치·상태 설명',multiple:'여러 명령',late_final:'늦게 붙는 부정·보고',long:'긴 복합 문장'};
let dataset=emptyPracticeDataset(),storageRaw=null,storageFailed=false,storageInvalid=false;
let queue=[],current=null,selectedId=null,active=null,stopping=false,ticker=null,shuffleSeed=null;
let mutationRevision=0,exportedRevision=-1;
const node=(tag,text,className)=>{const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(className)item.className=className;return item;};
const clone=value=>JSON.parse(JSON.stringify(value));
const labelAction=action=>action===null?'명령 없음':ACTIONS[action]||'정답 미지정';
const fromSelect=value=>value==='unlabelled'?undefined:value==='none'?null:value;
const toSelect=value=>value===undefined?'unlabelled':value===null?'none':value;
function error(text=''){$('practice-error').textContent=text;$('practice-error').hidden=!text;}
function status(text){$('practice-status').textContent=text;}
function storage(){try{return localStorage;}catch{return null;}}
function locked(){return !!active||stopping;}
function selected(){return dataset.attempts.find(item=>item.id===selectedId)||null;}
function controls(){
  const busy=locked(),blocked=storageFailed||storageInvalid;
  for(const id of ['group','action','search','case','failed-only','shuffle','next','repeat','custom-enabled','reference','expected','speaker','device','noise','style','mask','distance','pair','autostop','manual','fill-manual','import'])$('practice-'+id).disabled=busy;
  $('practice-reference').readOnly=!$('practice-custom-enabled').checked;
  $('practice-record').disabled=stopping||blocked||(!active&&dataset.attempts.length>=500)||(!active&&!VoiceCapture.capabilities().browser)||(!active&&!current&&!$('practice-custom-enabled').checked);
  $('practice-record').textContent=active?'말하기 마치기':'말하기 시작';
  $('practice-abort').disabled=!active;
  $('practice-simulate').disabled=busy||blocked||dataset.attempts.length>=500||(!current&&!$('practice-custom-enabled').checked);
  for(const id of ['json','csv'])$('practice-'+id).disabled=busy||(!dataset.attempts.length&&!storageInvalid)||(id==='csv'&&storageInvalid)||(id==='json'&&storageInvalid&&typeof storageRaw!=='string');
  $('practice-save-retry').hidden=!storageFailed||storageInvalid;$('practice-save-retry').disabled=busy;
  $('practice-new-batch').disabled=busy||exportedRevision!==mutationRevision||!$('practice-export-confirmed').checked;
  $('practice-export-confirmed').disabled=busy||exportedRevision!==mutationRevision;
  $('practice-raw-export').disabled=busy||!$('practice-raw').value;
  $('practice-annotation-save').disabled=busy||blocked||!selected();
  $('practice-verify-next').disabled=busy||blocked||!selected()?.referenceText||selected()?.referenceConfirmed===true;
  $('practice-verify-next').textContent=selected()?.source==='manual_simulation'?'모의 정답 확인 후 다음':'대본대로 말함·정답 확인 후 다음';
  for(const id of ['annotation-action','annotation-verified','annotation-note'])$('practice-'+id).disabled=busy||blocked||!selected();
  if(selected()&&!selected().referenceText)$('practice-annotation-verified').disabled=true;
}
function persistDataset(next){
  mutationRevision++;$('practice-export-confirmed').checked=false;
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
    autoStop:$('practice-autostop').checked,provisionalTriggerText:null,notes:''};
}
function preview(raw,early=null,caseId=active?.input.caseId){
  $('practice-raw').value=raw;const result=findVoiceTrigger(raw);
  $('practice-result').textContent=(caseId===undefined?'':`${caseId||'자유 발화'} · `)+(result?`최종 판정: ${labelAction(result.action)} · “${result.command}”`:'최종 판정: 명령 없음');
  $('practice-prefix').textContent=result?result.prefix||'(본문 없음)':raw||'—';$('practice-suffix').textContent=result?result.suffix||'(뒤 문장 없음)':'—';
  const first=early===null?null:findVoiceTrigger(early);
  $('practice-early-result').textContent=first?`최초 명령 후보: ${labelAction(first.action)}. ${early!==raw?'종료 전 추가 인식까지 보존했으며 최종 판정은 전체 인식문 기준입니다.':'연습 화면에서는 이 명령을 환자·병원에 실행하지 않았습니다.'}`:'';
}
function renderDataset(){
  const summary=scorePracticeAttempts(dataset.attempts);
  for(const name of ['total','human','manual','failed'])$('practice-counts-'+name).textContent=String(summary[name]);
  $('practice-coverage').textContent=`발화 유형 ${summary.coverage.length}개 수집 · 마이크 경로 정답 확인 ${summary.bySource.human_voice.verified}건 중 명령 일치 ${summary.bySource.human_voice.matched}건 · 수동 모의 정답 확인 ${summary.bySource.manual_simulation.verified}건 중 명령 일치 ${summary.bySource.manual_simulation.matched}건 · 가져온 자료 ${summary.imported}건은 원래 출처 집계에 포함됩니다. 오류·무인식·중단도 전체 시도에 포함합니다.`;
  $('practice-records').replaceChildren();
  for(const item of [...dataset.attempts].reverse().slice(0,80)){
    const row=node('article',undefined,'record'),body=node('div');body.append(node('strong',`${item.caseId||'자유 발화'} · ${item.source==='human_voice'?'마이크 경로':'수동 모의'}${item.imported?' · 가져온 자료':''} · ${STATUSES[item.status]} · ${item.trialNo}회차`),
      node('p',`${item.speakerId||'화자 미지정'} · ${item.condition.noiseKind} · 정답: ${labelAction(item.expectedAction)} · 판정: ${labelAction(item.parserResult?.action??null)}${item.referenceConfirmed?' · 정답 확인됨':' · 정답 미확인'}`),node('p',item.rawText.slice(0,180)||'(확정 인식문 없음)'));
    const open=node('button','검토');open.type='button';open.disabled=locked();open.onclick=()=>{if(!locked())selectAttempt(item.id);};row.append(body,open);$('practice-records').append(row);
  }
  if(dataset.attempts.length>80)$('practice-records').append(node('p',`최근 80건을 표시합니다. 전체 ${dataset.attempts.length}건은 내려받기에 포함됩니다.`,'help'));
}
function selectAttempt(id){
  if(locked())return;const item=dataset.attempts.find(record=>record.id===id);if(!item)return;selectedId=id;
  preview(item.rawText,item.provisionalTriggerText,item.caseId);$('practice-selected').textContent=`${item.id} · ${item.source==='human_voice'?'마이크 경로':'수동 모의'} · ${STATUSES[item.status]}`;
  $('practice-annotation-action').value=toSelect(item.expectedAction);$('practice-annotation-verified').checked=item.referenceConfirmed;$('practice-annotation-note').value=item.notes;
  controls();
}
function annotateSelected(advance=false){
  if(locked()||storageFailed||storageInvalid||!selected())return false;
  const item=selected();if(advance&&item.referenceConfirmed)return false;if(advance&&!item.referenceText){error('정답 문장이 없는 자유 발화는 미확인으로 보관하고 정답 검토에서 명령 의도를 따로 지정하세요.');return false;}
  try{const next=annotatePracticeAttempt(dataset,item.id,{expectedAction:fromSelect($('practice-annotation-action').value),referenceConfirmed:advance?true:$('practice-annotation-verified').checked,notes:$('practice-annotation-note').value});
    const saved=persistDataset(next);selectAttempt(item.id);if(saved){status('원문·출처·명령 판정은 유지하고 정답·메모만 보관했습니다.');if(advance)nextCase();}return saved;
  }catch(err){error(err.message);return false;}
}
function finalizePractice(outcome,reason){
  const a=active;if(!a||a.finalized)return; a.finalized=true;active=null;stopping=false;clearInterval(ticker);ticker=null;
  const raw=a.fragments.join(' ');preview(raw,a.provisionalTriggerText);
  try{
    const record=createPracticeAttempt({...a.input,fragments:[...a.fragments],rawText:raw,status:outcome,errorCode:a.errorCode||'',stopReason:reason,
      provisionalTriggerText:a.provisionalTriggerText,timing:{startedAt:a.startedAt,endedAt:Date.now()}});
    const next=appendPracticeAttempt(dataset,record);selectedId=record.id;const saved=persistDataset(next);selectAttempt(record.id);
    status(saved?`${STATUSES[record.status]} · 시도 ${dataset.attempts.length}건을 브라우저에 보관했습니다. 정답을 확인하거나 같은 문장·다음 문장으로 이어가세요.`:'시도는 화면 메모리에 있습니다. JSON을 먼저 내려받고 브라우저 보관을 다시 시도하세요.');
  }catch(err){storageFailed=true;error('시도를 자료로 묶지 못했습니다. 현재 원문 TXT를 먼저 내려받으세요. '+err.message);}
  renderDataset();controls();
}
const capture=new VoiceCapture({
  onInterim(text){if(active)status('듣는 중 · 임시 인식: '+text);},
  onFinal(text){
    const a=active;if(!a||a.finalized)return;a.fragments.push(text);const raw=a.fragments.join(' ');
    if(a.provisionalTriggerText===null&&findVoiceTrigger(raw))a.provisionalTriggerText=raw;
    preview(raw,a.provisionalTriggerText);
    if(!stopping&&a.input.autoStop&&a.provisionalTriggerText!==null){void stopPractice('trigger');return;}
    if(!stopping&&(raw.length>=12000||a.fragments.length>=240))void stopPractice('text_limit');
  },
  onState(state){
    if(state==='listening'&&active)status('말하는 중 · 버튼을 눌러 마치세요.');
    if(state==='idle'){const owner=active;queueMicrotask(()=>{if(owner&&active===owner&&!stopping&&!capture.active)finalizePractice(owner.errorCode?'error':owner.fragments.length?'ok':'no_match',owner.errorCode?'error':'natural');});}
    controls();
  },
  onError(err){if(active){active.errorCode=/^[a-zA-Z0-9_-]{1,80}$/.test(err.code||'')?err.code:'recognition-error';const owner=active;queueMicrotask(()=>{if(active===owner&&!stopping)finalizePractice('error','error');});}error('음성 인식을 완료하지 못했습니다. 전달된 확정 인식문과 실패 상태를 함께 보관합니다. '+err.message);}
});
async function startPractice(){
  if(locked()||storageFailed||storageInvalid||dataset.attempts.length>=500)return false;error();
  try{const input=contextInput('human_voice'),now=Date.now();createPracticeAttempt({...input,rawText:'',fragments:[],status:'no_match',stopReason:'natural',timing:{startedAt:now,endedAt:now}});
    active={input,startedAt:now,fragments:[],provisionalTriggerText:null,errorCode:'',finalized:false};preview('');controls();
    const owner=active;ticker=setInterval(()=>{if(active!==owner)return;const elapsed=Date.now()-owner.startedAt;if(elapsed>=180000){void stopPractice('time_limit');return;}if(capture.active) $('practice-record').textContent=`말하기 마치기 · ${Math.floor(elapsed/1000)}초`;},250);
    await capture.start('browser',{continuous:true});return true;
  }catch(err){if(active){active.errorCode=err.code||'start-failed';finalizePractice('error','error');}error('말하기를 시작하지 못했습니다. 마이크 권한·브라우저 지원을 확인하세요. '+err.message);controls();return false;}
}
async function stopPractice(reason='button'){
  if(!active||stopping)return false;const owner=active;stopping=true;controls();
  try{await capture.stop();}catch(err){if(active===owner)owner.errorCode=err.code||'stop-failed';}
  finally{if(active===owner)finalizePractice(owner.errorCode?'error':owner.fragments.length?'ok':'no_match',owner.errorCode?'error':reason);}
  return true;
}
function abortPractice(){if(!active)return false;const owner=active;owner.finalized=true;capture.abort();owner.finalized=false;if(active===owner)finalizePractice('aborted','aborted');return true;}
function runManualPractice(){
  if(locked()||storageFailed||storageInvalid||dataset.attempts.length>=500)return false;error();
  try{const input=contextInput('manual_simulation'),text=$('practice-manual').value,now=Date.now();
    active={input,startedAt:now,fragments:text?[text]:[],provisionalTriggerText:null,errorCode:'',finalized:false};finalizePractice(text.trim()?'ok':'no_match','manual');return !storageFailed;
  }catch(err){error(err.message);controls();return false;}
}
function download(content,type,filename){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),link=node('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
function exportDataset(format){
  if(locked())return false;try{if(storageInvalid&&typeof storageRaw!=='string')throw Error('저장소 원본을 읽지 못해 원본 JSON을 만들 수 없습니다. 브라우저의 저장 허용 상태를 확인하세요.');const content=format==='csv'?exportPracticeCSV(dataset):storageInvalid?storageRaw:exportPracticeJSON(dataset);download(content,format==='csv'?'text/csv;charset=utf-8':'application/json',`command-practice-${Date.now()}.${format}`);if(format==='json')exportedRevision=mutationRevision;status('내려받기를 요청했습니다. 파일이 저장됐는지 확인하세요. 브라우저의 수집 기록은 그대로 유지합니다.');controls();return true;}catch(err){error(err.message);return false;}
}
function newPracticeBatch(){
  if(locked()||exportedRevision!==mutationRevision||!$('practice-export-confirmed').checked)return false;
  const previous=dataset,invalid=storageInvalid;storageInvalid=false;
  if(!persistDataset(emptyPracticeDataset())){dataset=previous;storageInvalid=invalid;renderDataset();controls();return false;}
  selectedId=null;preview('');$('practice-selected').textContent='새 수집 묶음을 시작했습니다.';rebuildQueue();controls();status('확인한 JSON 파일은 그대로 두고 브라우저에 빈 수집 묶음을 준비했습니다.');return true;
}
function importDataset(text){
  if(locked()||storageFailed||storageInvalid)return false;
  try{const imported=importPracticeJSON(text),before=dataset.attempts.length,next=mergePracticeDatasets(dataset,imported),saved=persistDataset(next);if(saved)status(`${dataset.attempts.length-before}건을 합쳤습니다. 동일 기록은 중복 추가하지 않았고 출처를 유지했습니다.`);if(!selectedId&&dataset.attempts.length)selectAttempt(dataset.attempts.at(-1).id);return saved;}catch(err){error('자료를 합치지 않았습니다. 기존 기록은 유지했습니다. '+err.message);return false;}
}
function restoreContext(item){if(!item)return;for(const [id,value]of Object.entries({speaker:item.speakerId,device:item.device,noise:item.condition.noiseKind,style:item.condition.style,mask:item.condition.mask,distance:item.condition.distanceCm??'',pair:item.pairId}))$('practice-'+id).value=String(value);}
function initialize(){
  const loaded=readPracticeDataset(storage());storageRaw=loaded.raw;
  if(loaded.status==='valid'){dataset=loaded.dataset;restoreContext(dataset.attempts.at(-1));}
  else if(loaded.status==='invalid'){storageFailed=true;storageInvalid=true;error('저장된 수집 자료를 읽지 못했습니다. 덮어쓰지 않았습니다. 전체 JSON으로 원본을 보관하고 기존 파일을 확인하세요. '+loaded.message);}
  const families=[...new Set(PRACTICE_CASES.map(item=>item.family))];$('practice-group').replaceChildren(...['all',...families].map(value=>{const option=node('option',value==='all'?'모든 유형':FAMILIES[value]||value);option.value=value;return option;}));$('practice-group').value='all';
  rebuildQueue();renderDataset();if(dataset.attempts.length)selectAttempt(dataset.attempts.at(-1).id);
  status(loaded.status==='valid'?`${dataset.attempts.length}건을 복원했습니다. 실제 마이크 시도와 수동 모의는 따로 집계합니다.`:'대본을 고르고 말하기 시작을 누르세요. 종료하면 시도를 자동 보관합니다.');controls();
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
$('practice-save-retry').onclick=()=>{if(!locked()&&!storageInvalid){const saved=persistDataset(dataset);if(saved)status('현재 화면의 자료를 브라우저에 다시 보관했습니다.');}};
$('practice-json').onclick=()=>exportDataset('json');$('practice-csv').onclick=()=>exportDataset('csv');
$('practice-export-confirmed').onchange=controls;$('practice-new-batch').onclick=newPracticeBatch;
$('practice-raw-export').onclick=()=>{if(!locked()&&$('practice-raw').value)download($('practice-raw').value,'text/plain;charset=utf-8',`command-practice-raw-${Date.now()}.txt`);};
$('practice-import').onchange=async event=>{const file=event.target.files?.[0];if(!file||locked())return;try{if(file.size>4*1024*1024)throw Error('파일이 4MiB 상한을 넘습니다.');importDataset(await file.text());}catch(err){error(err.message);}finally{$('practice-import').value='';}};
window.addEventListener('storage',event=>{if(event.key!==PRACTICE_KEY||event.newValue===storageRaw)return;if(active)abortPractice();storageFailed=true;error('다른 탭이 자료를 바꿔 이 탭의 보관을 멈췄습니다. 이 화면의 JSON을 내려받고 새로고침 후 합치세요.');controls();});
window.addEventListener('beforeunload',event=>{if(active||storageFailed&&dataset.attempts.length){event.preventDefault();event.returnValue='';}});
window.addEventListener('pagehide',()=>{if(active){const owner=active;owner.finalized=true;capture.abort();owner.finalized=false;if(active===owner)finalizePractice('aborted','page_exit');}capture.stopSpeaking();});
initialize();
