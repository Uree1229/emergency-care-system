import {VoiceCapture} from './speech.mjs';
import {VoiceClient, requestId} from './voice-client.mjs';
import {QuickSession} from './quick-session.mjs';
import {chooseAnswer, answerVocabulary} from './answer-match.mjs';
import {RECORDING_LIMITS, splitStopCommand} from './recording-control.mjs';

const $ = id => document.getElementById(id);
const VOICE_REVISION = 'voice-v2-20261030', HOLD_MS = 400;
const labels = {age:'나이',age_group:'연령대',sex:'성별',symptoms:'확인된 증상',denied_symptoms:'없다고 확인한 증상',uncertain_symptoms:'미확인·의심 증상',consciousness:'의식',breathing:'호흡',systolic_bp:'수축기 혈압',diastolic_bp:'이완기 혈압',pulse:'맥박',spo2:'산소포화도',temperature:'체온',respiratory_rate:'호흡수',glucose:'혈당',onset_at:'발생 시각',avpu:'AVPU',chief_complaint:'주증상 분류',age_category:'연령 구분',pre_ktas:'Pre-KTAS',stemi_suspected:'STEMI 의심',injury_region:'손상 부위',labor:'분만 진행',pregnancy_weeks:'임신 주수',seizure_ongoing:'경련 진행',bleeding_risk:'출혈 위험',high_risk_mechanism:'고위험 손상기전',pain_score:'통증 점수',gcs:'GCS'};
const observationKeys = new Set(Object.keys(labels).slice(0,16));
const names = {male:'남성',female:'여성',unknown:'미확인',alert:'명료',impaired:'저하',unresponsive:'반응 없음',normal:'정상',abnormal:'비정상',absent:'호흡 없음',adult:'성인',child:'소아',infant:'영아',cardiac:'심혈관',respiratory:'호흡기'};
const units = {age:'세',systolic_bp:'mmHg',diastolic_bp:'mmHg',pulse:'회/분',spo2:'%',temperature:'℃',respiratory_rate:'회/분',glucose:'mg/dL',pregnancy_weeks:'주'};
const kinds = {dictation:'받아쓰기',observation:'상태 변화 관찰',correction:'수정',followup_answer:'질문 답변',undo:'되돌리기'};
const reasons = {missing:'누락',needs_clarification:'재확인 필요',uncertain:'미확인'};
const directions = {increased:'▲ 증가',decreased:'▼ 감소',unchanged:'= 같음'};
const actions = {review:'읽어주기',missing:'빠진 항목 안내',undo:'되돌리기',edit:'수정',add:'추가',confirm:'확인(화면에서 누르기)',submit:'전송 요청(실행 안 함)',observe:'상태 변화 관찰'};
const modeHints = {auto:'상태 설명은 초안에 더하고, “방금 거 취소”·“정리해줘”·“혈압 110에 70으로 수정” 같은 말은 명령으로 처리합니다.',dictation:'설명한 내용을 같은 초안에 추가합니다. 명령으로 해석하지 않습니다.',command:'“혈압 110에 70으로 수정해”, “정리해줘”, “마지막 입력 취소”처럼 말하세요.'};
const idleHint = () => recordingMode==='hold'?'버튼을 누른 채로 말하고, 다 말하면 손을 떼세요.':'한 번 눌러 시작하고 다시 누르거나, 마지막에 “그만” 또는 “녹음 종료”라고 말하세요.';
// Hints that carry a finding value (normal status, negation, numbers) could pull unclear speech
// toward that value, so only vocabulary hints reach the recognizer.
const VALUE_HINT = /[0-9]|[십백]|명료|정상|저하|없음|있음/;
// The 12 items of the server's voice_extended_v1 checklist, shown as 10 summary chips.
const SUMMARY = [['age','sex'],['symptoms','onset_at'],['consciousness'],['blood_pressure'],['pulse'],['spo2'],['temperature'],['breathing'],['respiratory_rate'],['glucose']];
const SHORT = {age:'나이',sex:'성별',symptoms:'증상',onset_at:'발생',consciousness:'의식',blood_pressure:'혈압',pulse:'맥박',spo2:'SpO₂',temperature:'체온',breathing:'호흡',respiratory_rate:'호흡수',glucose:'혈당'};
const ITEM_KEYS = {age:['age','age_group'],symptoms:['symptoms','denied_symptoms','uncertain_symptoms'],blood_pressure:['systolic_bp','diastolic_bp']};
const OPTIONS = {consciousness:['명료','저하','반응 없음'],breathing:['정상','비정상','없음'],sex:['남성','여성']};
const actor = 'QUICK-VOICE-' + requestId();
let client = new VoiceClient({server: location.origin, actorId: actor});
let flow = new QuickSession(client), mode = 'auto', attempt = null, busy = false, stopping = false, ticker, historyCount = 0;
let autoAvailable = true, recordingMode = 'toggle', press = null, lastSpoken = '', hintPlan = {applied: [], excluded: []}, phraseCount = 0;
// Questions whose answer was not understood: set aside (with what was heard) for the crew's review
// instead of asking the same question again. Cleared by "다시 묻기" or a new draft.
let deferred = new Map();
// What the latest request changed (only when it produced a new draft version).
let recent = null;
let manualAppliedText = '', manualCopiedSource = '';

const node = (tag, text, className) => {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;};
const has = value => value !== null && value !== undefined;
const itemKeys = id => ITEM_KEYS[id]||[id];
function error(text) {$('quick-error').textContent=text;$('quick-error').hidden=!text;}
function state(text) {$('quick-state').textContent=text;}
function heard(text) {$('quick-transcript').textContent=text;$('quick-live').hidden=!(text||capture.active);if(text)timelineEnd();}
function locked() {return busy||stopping||capture.active||!!flow.job;}
// The server accepts an answer to any offered item, so a deferred question can be skipped over.
function currentQuestion() {
  const plan=client.session?.followup;
  if(!plan)return null;
  const next=(plan.items||[]).find(item=>item.askable!==false&&!deferred.has(item.id))||plan.next_question;
  return next&&!deferred.has(next.id)?next:null;
}
function pttControl(id,target,blocked,idle,holding) {
  const button=$(id), mine=capture.active&&attempt?.target===target;
  button.disabled=blocked||(capture.active&&!mine);
  button.textContent=!mine?idle:!attempt.listening?(attempt.held?'마이크 준비 중… 누른 채로 기다리세요':'시작 취소'):attempt.held?holding:'한 번 눌러 마치기';
  button.setAttribute('aria-pressed',String(mine));
}
function controls() {
  renderStorageReceipt();
  const active=capture.active, isLocked=locked(), q=currentQuestion(), caps=VoiceCapture.capabilities();
  const hasFields=Object.keys(client.session?.fields||{}).length>0, micBlocked=busy||stopping||!!flow.job||!caps.browser;
  // With a question showing, the big button answers it and general input moves to the side button.
  pttControl('quick-record','transcript',micBlocked,q?'다른 내용':recordingMode==='hold'?'누른 채로 말하기':'한 번 눌러 말하기','손을 떼면 전송');
  pttControl('followup-record','answer',micBlocked||!q,recordingMode==='hold'?'누른 채로 답하기':'한 번 눌러 답하기','손을 떼면 답변 전송');
  $('dock').classList.toggle('has-question',!!q);
  $('dock').classList.toggle('is-recording',active);
  $('quick-live').hidden=!(active||$('quick-transcript').textContent);
  $('quick-cancel').hidden=!active;
  $('mode-auto').disabled=isLocked||!autoAvailable;
  $('mode-dictation').disabled=isLocked;
  $('mode-command').disabled=isLocked||!hasFields;
  for(const name of ['auto','dictation','command'])$('mode-'+name).setAttribute('aria-pressed',String(mode===name));
  for(const name of ['toggle','hold']){$('recording-'+name).disabled=isLocked;$('recording-'+name).setAttribute('aria-pressed',String(recordingMode===name));}
  $('followup-unavailable').disabled=isLocked||!q;
  for(const option of $('followup-options').children)option.disabled=isLocked;
  $('followup-speak').disabled=isLocked||!q||!caps.tts;
  $('quick-new').disabled=isLocked;
  $('quick-readback').disabled=isLocked||!hasFields||!caps.tts;
  $('quick-retry').hidden=!client.pending||busy||stopping;
  $('quick-retry').disabled=busy||stopping||active;
  const manualText=$('recording-review').value.trim();
  $('recording-review').disabled=isLocked;
  $('recording-excluded-copy').disabled=isLocked;
  $('recording-review-apply').disabled=isLocked||!manualText||manualText.length>RECORDING_LIMITS.text||manualText===manualAppliedText;
  $('recording-review-count').textContent=`${manualText.length} / ${RECORDING_LIMITS.text}자`;
}
function valueText(key,value) {
  if(key==='onset_at')return new Date(value*1000).toLocaleString('ko-KR');
  if(Array.isArray(value))return value.join(', ')||'없음';
  if(typeof value==='boolean')return value?'예':'아니오';
  return (names[value]||String(value))+(units[key]?' '+units[key]:'');
}
const order = key => {const index=Object.keys(labels).indexOf(key);return index<0?99:index;};
const named = list => [...(list||[])].sort((a,b)=>order(a)-order(b)).map(f=>labels[f]||f);
// Fields of questions answered "미측정/모름": left unresolved on purpose, never filled.
function unavailableKeys() {return new Set((client.session?.followup?.items||[]).filter(item=>item.answer_status==='unavailable').flatMap(item=>item.fields||[]));}
function unavailableQuestions() {return new Set((client.session?.followup?.items||[]).filter(item=>item.answer_status==='unavailable').map(item=>item.id));}
function fieldCard(key,value,meta,unavailable) {
  const unclear=meta.needs_clarification===true, card=node('dl',undefined,unclear?'quick-field needs-clarification':'quick-field');
  // A value-less change report keeps the earlier observation; it is shown as earlier, not current.
  card.append(node('dt',(labels[key]||key)+(unclear?(unavailable?' · 미측정·확인 불가':' · 재확인 필요'):'')),node('dd',!has(value)?'값 없음':valueText(key,value)+(unclear?' (이전 관찰값)':'')));
  const evidence=(unclear?meta.clarification_evidence:meta.evidence)||[];
  const source=meta.source==='followup_answer'?(meta.contextual_answer?'질문 답변 · 질문 문맥으로 해석':'질문 답변'):'';
  const note=[evidence.join(' / '),source].filter(Boolean).join(' · ');
  if(note)card.append(node('p',note));
  return card;
}
function sinceText(epoch) {const minutes=Math.max(0,Math.round((Date.now()/1000-epoch)/60));return minutes<60?`${minutes}분 전`:`${Math.floor(minutes/60)}시간 ${minutes%60}분 전`;}
function itemValue(id,f) {
  if(id==='age')return has(f.age)?`${f.age}세`:has(f.age_group)?String(f.age_group):null;
  if(id==='symptoms')return f.symptoms?.length?f.symptoms.slice(0,2).join(', ')+(f.symptoms.length>2?' 외':''):f.denied_symptoms?.length?`없음 ${f.denied_symptoms.slice(0,2).join('·')}`:f.uncertain_symptoms?.length?`확인 필요 ${f.uncertain_symptoms[0]}`:null;
  if(id==='onset_at')return has(f.onset_at)?sinceText(f.onset_at):null;
  if(id==='blood_pressure')return has(f.systolic_bp)||has(f.diastolic_bp)?`${f.systolic_bp??'—'}/${f.diastolic_bp??'—'}`:null;
  return has(f[id])?names[f[id]]||String(f[id]):null;
}
function itemState(id,ctx) {
  const keys=itemKeys(id), changes=(recent?.changes||[]).filter(c=>keys.includes(c.field));
  if(deferred.has(id))return 'deferred';
  if(ctx.asking===id)return 'asking';
  if(ctx.unavailable.has(id))return 'unavailable';
  if(keys.some(k=>ctx.meta[k]?.needs_clarification===true))return 'unclear';
  if(changes.some(c=>has(c.previous)&&has(c.current)))return 'changed';
  if(changes.some(c=>!has(c.previous)&&has(c.current)))return 'new';
  return itemValue(id,ctx.fields)?'filled':'missing';
}
const STATE_ORDER = ['deferred','asking','unavailable','unclear','changed','new','filled','missing'];
// Only the server's returned utterance list is a storage receipt. Empty extracted
// fields do not mean the transcript was lost, and a created draft is not a saved utterance.
function renderStorageReceipt() {
  const s=client.session, count=Array.isArray(s?.utterances)?s.utterances.length:null;
  const pending=Boolean(client.pending||flow.job||busy);
  let text=!s?'아직 서버에 전송한 발화가 없습니다.':count===null?'초안 응답을 받았습니다. 저장 발화 수는 확인되지 않았습니다.':count===0?'초안 준비됨 · 서버에서 확인한 저장 발화 0건':`서버에서 확인한 저장 발화 ${count}건`;
  if(s&&count>0&&!Object.keys(s.fields||{}).length)text+=' · 원문은 저장됐고, 구조화된 환자 항목은 확인이 필요합니다.';
  if(pending)text+=' · 이번 입력의 저장 결과를 확인하고 있습니다.';
  else if(capture.active)text+=' · 현재 말하는 내용은 종료 후 전송합니다.';
  $('quick-storage-status').textContent=text;
  $('quick-storage-receipt').hidden=!s;
  $('quick-storage-details').textContent=s?`서버: ${client.server}\n기록 ID: ${s.id}\n초안 버전: ${s.version}\n서버 확인 발화: ${count===null?'확인되지 않음':count+'건'}`:'';
}
function summaryChip(ids,ctx) {
  const states=ids.map(id=>itemState(id,ctx)), chipState=STATE_ORDER.find(s=>states.includes(s));
  const single=ids.length===1, parts=ids.map((id,i)=>{
    const value=itemValue(id,ctx.fields), s=states[i], label=SHORT[id];
    if(s==='deferred')return `${label} 보류`;
    if(s==='asking')return `${label} 질문 중`;
    if(s==='unavailable')return `${label} 미측정`;
    if(!value)return single?`${label} —`:`${label} ?`;
    const change=(recent?.changes||[]).find(c=>itemKeys(id).includes(c.field)&&c.direction&&has(c.previous));
    const text=(single?`${label} `:'')+value;
    return text+(s==='unclear'?' 재확인':s==='changed'&&change&&directions[change.direction]?' '+directions[change.direction][0]:'');
  });
  const empty=states.every(s=>s==='missing');
  const text=empty&&!single?`${ids.map(id=>SHORT[id]).join('·')} —`:parts.join(ids[0]==='age'?' ':' · ');
  const complaint=ids.includes('symptoms')&&ctx.fields.symptoms?.length&&['filled','new','changed'].includes(chipState);
  return node('span',text,`qv-sum-chip is-${chipState}${complaint?' is-complaint':''}`);
}
function render() {
  const s=client.session, fields=s?.fields||{}, meta=s?.field_metadata||{}, unavailable=unavailableKeys();
  const entries=Object.entries(fields).sort(([a],[b])=>order(a)-order(b));
  $('quick-fields').replaceChildren();$('derived-fields').replaceChildren();
  for(const [key,value] of entries)$(observationKeys.has(key)?'quick-fields':'derived-fields').append(fieldCard(key,value,meta[key]||{},unavailable.has(key)));
  for(const key of Object.keys(meta).sort((a,b)=>order(a)-order(b)))if(!(key in fields)&&meta[key]?.needs_clarification===true&&observationKeys.has(key))$('quick-fields').append(fieldCard(key,null,meta[key],unavailable.has(key)));
  if(!$('quick-fields').children.length)$('quick-fields').append(node('p','아직 추출된 관찰 정보가 없습니다. 아래 마이크 버튼으로 말해 보세요.','quick-empty'));
  const derived=entries.filter(([k])=>!observationKeys.has(k)).length;
  $('quick-derived').hidden=!derived;$('derived-count').textContent=`${derived}개`;
  const warnings=s?.warnings||[];
  $('quick-warnings').hidden=!warnings.length;$('warning-count').textContent=`${warnings.length}개`;
  $('warning-items').replaceChildren(...warnings.map(text=>node('li',text)));
  const ctx={fields,meta,unavailable:unavailableQuestions(),asking:currentQuestion()?.id};
  $('summary-chips').replaceChildren(...SUMMARY.map(ids=>summaryChip(ids,ctx)));
  const filled=SUMMARY.flat().filter(id=>itemValue(id,fields)).length;
  $('quick-count').textContent=filled?`12개 중 ${filled}개`:s?.utterances?.length?'원문 저장됨 · 항목 확인 필요':'기록 대기';
  $('quick-session').textContent=s?`가상 환자 · 초안 ${String(s.id).slice(-6)} · 버전 ${s.version}`:'가상 환자 · 새 기록';
  renderFollowup();controls();
}
function answerOptions(q) {return q.id==='symptoms'?(q.uncertain_items?.length===1?['있음','없음']:[]):OPTIONS[q.id]||[];}
function renderFollowup() {
  const plan=client.session?.followup, items=plan?.items||[];
  for(const id of [...deferred.keys()])if(!items.some(item=>item.id===id&&item.askable!==false))deferred.delete(id);
  const q=currentQuestion();
  $('followup-card').hidden=!q;
  if(q){
    $('followup-question').textContent=q.question;$('followup-hint').textContent=q.answer_hint||'';
    $('followup-reason').textContent=reasons[q.reason]||'';
    $('followup-progress').textContent=Number.isInteger(plan.unanswered_count)?`남은 ${Math.max(0,plan.unanswered_count-deferred.size)}${deferred.size?` · 보류 ${deferred.size}`:''}`:'';
  }
  const options=q?answerOptions(q):[];
  $('followup-options').replaceChildren(...options.map(text=>{const button=node('button',text,'qv-chip-btn');button.type='button';button.onclick=()=>tapAnswer(text);return button;}));
  $('followup-options').hidden=!options.length;
  $('followup-deferred').hidden=!deferred.size;
  $('followup-deferred-text').textContent=deferred.size?`검토 때 확인 ${deferred.size}개 · ${[...deferred].map(([id,heardText])=>`${questionLabel(items.find(item=>item.id===id))} “${heardText}”`).join(', ')}`:'';
  const done=Boolean(client.session&&plan&&!q);
  $('followup-done').hidden=!done||deferred.size>0;
  $('followup-done').textContent=!done||deferred.size?'':plan.complete?'확인 질문 목록의 항목이 모두 기록됐습니다. 값은 원문과 비교해 검토하세요.':`더 묻지 않습니다. 미측정·확인 불가로 남긴 항목 ${plan.unresolved_count??''}개는 0이나 정상으로 채우지 않았습니다.`;
}
function snapshot() {const meta=client.session?.field_metadata||{};return {version:client.session?.version||0,flagged:Object.keys(meta).filter(k=>meta[k]?.needs_clarification===true)};}
// latest_changes describes the newest revision; without a new version this request changed nothing.
function computeRecent(before) {
  const s=client.session;
  if(!s||!before||s.version<=before.version)return null;
  const meta=s.field_metadata||{};
  return {changes:(s.latest_changes||[]).filter(c=>typeof c?.field==='string'),
    flagged:Object.keys(meta).filter(k=>meta[k]?.needs_clarification===true&&!before.flagged.includes(k)).sort((a,b)=>order(a)-order(b))};
}
function renderChanges() {
  const list=$('change-items');
  list.replaceChildren();
  if(!client.session||!recent){$('quick-changes').hidden=true;$('changes-kind').textContent='';return;}
  const s=client.session, changes=recent.changes, unavailable=unavailableKeys();
  const observed=changes.filter(c=>observationKeys.has(c.field)).sort((a,b)=>order(a.field)-order(b.field)), derived=changes.length-observed.length;
  for(const c of observed.filter(c=>has(c.previous)&&has(c.current))){
    const item=node('li',undefined,'change-update');
    item.append(node('span',labels[c.field]||c.field,'change-label'),node('s',valueText(c.field,c.previous)),node('span','→','change-arrow'),node('strong',valueText(c.field,c.current)));
    if(directions[c.direction])item.append(node('em',directions[c.direction],'change-'+c.direction));
    list.append(item);
  }
  for(const key of recent.flagged){
    const item=node('li',undefined,'change-clarify'), value=s.fields?.[key];
    item.append(node('span',labels[key]||key,'change-label'),node('span',has(value)?`이전 관찰값 ${valueText(key,value)}`:'이전 값 없음'),node('strong',unavailable.has(key)?'미측정 · 확인 불가로 남김':'현재 값 미확인 · 재확인 필요'));
    list.append(item);
  }
  const added=observed.filter(c=>!has(c.previous)&&has(c.current)), removed=observed.filter(c=>has(c.previous)&&!has(c.current));
  if(added.length)list.append(node('li',`새로 기록 ${added.length}개: ${added.map(c=>labels[c.field]).join(', ')}`,'change-added'));
  if(removed.length)list.append(node('li',`비움 ${removed.length}개: ${removed.map(c=>labels[c.field]).join(', ')}`,'change-removed'));
  if(derived)list.append(node('li',`자동 해석 제안 ${derived}개 갱신`,'change-derived'));
  if(!list.children.length)list.append(node('li','바뀐 칸이 없습니다. 인식 원문과 확인할 내용을 살펴보세요.','change-none'));
  $('changes-kind').textContent=kinds[changes[0]?.change_kind]?'· '+kinds[changes[0].change_kind]:'';
  $('quick-changes').hidden=false;
}
function questionLabel(q) {return (q?.fields||[]).map(f=>labels[f]||f).join('·')||q?.id||'';}
function answerSummary(result) {
  const answer=result.answer||{}, applied=named(answer.applied_fields), ignored=named(answer.ignored_fields), parts=[];
  if(answer.unavailable)parts.push('미측정·확인 불가로 남겼습니다. 0이나 정상으로 채우지 않습니다.');
  else if(answer.accepted&&applied.length)parts.push(`답변을 반영했습니다: ${applied.join(', ')}${answer.contextual_answer?' (질문 문맥으로 해석)':''}.`);
  else if(!answer.accepted)parts.push('값을 알아듣지 못해 이 질문은 보류하고 다음 질문으로 넘어갑니다. 들린 말은 검토 목록에 남겼습니다.');
  if(ignored.length)parts.push(`질문 대상이 아닌 ${ignored.join(', ')} 값은 반영하지 않았습니다. 큰 버튼으로 다시 말해 주세요.`);
  return parts.join(' ')||'답변을 처리했습니다.';
}
function changeText(changes) {
  const fields=client.session?.fields||{}, by=Object.fromEntries(changes.map(c=>[c.field,c])), parts=[], arrow=c=>c?.direction&&directions[c.direction]?' '+directions[c.direction][0]:'';
  for(const c of changes){
    if(c.field==='diastolic_bp'&&by.systolic_bp)continue;
    if(c.field==='systolic_bp'||c.field==='diastolic_bp'){
      const s=by.systolic_bp, d=by.diastolic_bp;
      parts.push(`혈압 ${s?s.previous:fields.systolic_bp??'—'}/${d?d.previous:fields.diastolic_bp??'—'} → ${s?s.current:fields.systolic_bp??'—'}/${d?d.current:fields.diastolic_bp??'—'}${arrow(s||d)}`);
    } else parts.push(`${labels[c.field]} ${valueText(c.field,c.previous)} → ${valueText(c.field,c.current)}${arrow(c)}`);
  }
  return parts.join(', ');
}
// One short tag under the crew's bubble: what this utterance did to the draft.
function resultTag(a,result) {
  const answer=result.answer;
  if(a.target==='answer'){
    if(answer?.unavailable)return {text:'미측정 · 값을 채우지 않음',tone:'is-muted'};
    if(!answer?.accepted)return {text:'알아듣지 못해 보류 · 검토 때 확인',tone:'is-warn'};
    if(answer.ignored_fields?.length)return {text:`${named(answer.ignored_fields).join(', ')} 값은 반영 안 함`,tone:'is-warn'};
  }
  if(!recent)return null;
  const observed=recent.changes.filter(c=>observationKeys.has(c.field)), updated=observed.filter(c=>has(c.previous)&&has(c.current));
  if(updated.length)return {text:`${kinds[updated[0].change_kind]||'변경'} · ${changeText(updated)}`,tone:'is-change'};
  if(recent.flagged.length)return {text:`${recent.flagged.map(k=>labels[k]).join(', ')} 재확인 필요`,tone:'is-warn'};
  const added=observed.filter(c=>!has(c.previous)&&has(c.current));
  // An accepted answer already names what it filled ("반영: 의식").
  return added.length&&a.target!=='answer'?{text:`${added.length}칸 기록`,tone:'is-ok'}:null;
}
function timelineEnd() {const timeline=document.querySelector('.qv-timeline');if(timeline)timeline.scrollTop=timeline.scrollHeight;}
function appendApp(text,className='') {
  if(!text)return;
  const item=node('div',undefined,'qv-msg is-app'+(className?' '+className:''));
  item.append(node('p',text,'qv-bubble'));$('quick-history').append(item);timelineEnd();
}
function addHistory(a,result) {
  historyCount++;
  if(a.target==='answer'&&a.question)appendApp(a.question.question,'is-question');
  // A natural state report ("혈압이 100에 70으로 떨어졌어") comes back as command "observe": show it as a state change.
  const observe=result.command?.action==='observe';
  const title=a.manual?'직접 입력 · 검토한 내용을 초안에 반영':a.target==='answer'?`확인 질문 답변 · ${questionLabel(a.question)}`:a.mode==='command'?'수정 · 확인 명령':a.mode==='auto'?`자동 판단${result.effective_mode?` → ${observe?'상태 변화':result.effective_mode==='command'?'명령':'상태 설명'}`:''}`:'환자 상태 설명';
  const pick=a.pick, text=pick?pick.text:(a.sourceFinals||a.fragments).join(' ');
  const note=!pick?'':[pick.rank>0?`인식 후보 ${pick.rank+1}순위 사용(1순위 “${pick.alternatives[0]}”)`:'',pick.unavailable?'미측정 뜻으로 보고 보냄':''].filter(Boolean).join(' · ');
  const answer=result.answer||{}, tag=resultTag(a,result);
  const base=a.target==='answer'?(answer.unavailable?'미측정으로 남김':answer.accepted?`반영: ${named(answer.applied_fields).join(', ')}${answer.contextual_answer?' (질문 문맥으로 해석)':''}`:'반영 안 됨'):observe?'':result.command?`처리: ${actions[result.command.action]||result.command.action}`:tag?'':'음성 초안에 자동 반영';
  const detail=[base,note?`(${note})`:''].filter(Boolean).join(' ');
  const item=node('article',undefined,'qv-msg is-user quick-entry'), meta=node('div',undefined,'qv-meta');
  meta.append(node('strong',title));
  if(detail)meta.append(node('span',detail));
  if(tag)meta.append(node('span',tag.text,'qv-tag '+tag.tone));
  item.append(node('p',(a.button?'[버튼] ':'')+text,'qv-bubble'),meta);
  if(!a.button)item.append(sourceDetail(a));
  $('quick-history').append(item);$('quick-history-count').textContent=`${historyCount}회`;timelineEnd();
}
function endLabel(a) {
  const reason={button:'버튼으로 종료',release:'손을 떼어 종료',voice:`음성 명령 “${a.stopCommand||''}”`,limit:`${a.maxSeconds}초 시간 한도`,text_limit:'4000자 한도',recognizer:'브라우저 인식 종료',cancel:'취소',error:'음성 연결 오류',manual:'직접 입력 반영'}[a.endReason]||'말하기 종료';
  return `${reason}${a.failed&&a.endReason!=='error'?' · 종료 중 연결 오류':''} · ${new Date(a.endedAt||Date.now()).toLocaleTimeString('ko-KR')}`;
}
function sourceDetail(a) {
  const detail=node('details',undefined,'qv-source');detail.append(node('summary','인식 원문 · API 전송 본문'));
  detail.append(node('p',(a.manual?'직접 입력 원문: ':'확정 인식 원문: ')+(a.sourceFinals||a.fragments).join(' ')),node('p','API 전송 본문: '+((a.apiText??a.fragments.join(' '))||'없음')),node('p',endLabel(a)));
  if(a.copiedFrom)detail.append(node('p','가져온 미반영 인식 원문: '+a.copiedFrom));
  if(a.excludedFinals?.length)detail.append(node('p','종료 명령 뒤 확정되어 본문에 넣지 않은 내용: '+a.excludedFinals.join(' ')));
  return detail;
}
function markEnd(a,reason) {
  if(!a)return;
  a.endReason=a.endReason||reason;a.endedAt=a.endedAt||Date.now();
  $('recording-ended').textContent=endLabel(a);$('recording-ended').hidden=false;
}
function addUnsentHistory(a,message) {
  const item=node('article',undefined,'qv-msg is-user quick-recording-log');
  item.append(node('p',(a.sourceFinals||a.fragments).join(' ')||'인식문 없음','qv-bubble'),node('div',message,'qv-meta'),sourceDetail(a));
  $('quick-history').append(item);timelineEnd();
}
function say(text,prefix='') {
  if(!text||!VoiceCapture.capabilities().tts||capture.active)return;
  try{capture.speak(text);}catch(err){error(prefix+err.message);}
}
function feedback(result,a) {
  const action=result.command?.action, tapOnly=result.operational_effect==='confirmation_requires_tap';
  const text=a.target==='answer'?answerSummary(result):action==='submit'?'시험 화면에서는 환자정보를 전송하지 않습니다.':tapOnly?(result.speak_text||'확인은 화면에서 눌러 주세요.'):result.speak_text||(action==='edit'?'수정한 값을 반영했습니다.':action==='confirm'?'현재 초안을 확인했습니다.':action==='undo'?'직전 변경을 되돌렸습니다.':'');
  $('quick-feedback').textContent=text;$('quick-feedback').hidden=!text;
  // Commands and notices answer in the conversation; answers are summarised by the bubble's tag.
  if(a.target!=='answer')appendApp(text);
  // Read aloud only with the microphone closed: requested readbacks, else the server's next question once.
  let spoken='';
  if(['review','missing'].includes(action)||tapOnly)spoken=result.speak_text||'';
  else if($('followup-autoread').checked){
    const q=currentQuestion(), key=q?q.id+'|'+q.question:'';
    if(q&&key!==lastSpoken){spoken=q.question;lastSpoken=key;}
    // The server's speak_text would repeat a deferred question; only say that answers were set aside.
    else if(!q&&a.target==='answer')spoken=deferred.size?'알아듣지 못한 답은 보류했습니다. 검토할 때 확인해 주세요.':result.speak_text||'';
  }
  say(spoken,'정보 처리는 완료했지만 읽어주기를 시작하지 못했습니다. ');
}
function settle(a,result) {
  if(a.manual){manualAppliedText=a.apiText;$('recording-review-status').textContent='검토한 내용을 초안에 반영했습니다. 같은 본문은 다시 보내지 않습니다.';}
  if(a.target==='answer'&&a.question){
    if(result.answer?.accepted)deferred.delete(a.question.id);
    else deferred.set(a.question.id,a.pick?.text||a.fragments.join(' '));
  }
  recent=computeRecent(a.before);heard('');
  addHistory(a,result);render();renderChanges();feedback(result,a);state('처리 완료');
  $('quick-status').textContent=a.target!=='answer'?'자동으로 정리했습니다. 빠지거나 잘못된 값은 원문과 비교해 주세요.':result.answer?.accepted?'답변을 처리했습니다. 다음 질문이나 결과를 확인하세요.':'알아듣지 못한 답은 보류했습니다. 검토할 때 고치면 됩니다.';
}
async function failure(err) {
  state('처리 확인 필요');error(err.message);
  if(err.status===409&&client.session){
    // A changed draft or question is re-read, never answered against the stale question.
    try{await client.sync();recent=null;error(`${err.message} 최신 초안과 질문을 다시 불러왔습니다. 확인한 뒤 다시 말해 주세요.`);}
    catch(syncError){error(`${err.message} 최신 초안을 불러오지 못했습니다. ${syncError.message}`);}
  }
  $('quick-status').textContent='원문은 유지했습니다. 결과가 불확실한 요청은 같은 요청으로 재시도하세요.';render();
}
async function send(a,request) {
  busy=true;a.before=snapshot();state('정보 정리 중');
  $('quick-status').textContent=a.target==='answer'?'질문에 대한 답을 서버에서 확인하고 있습니다.':'긴 문장을 서버에서 한 번에 처리하고 있습니다.';controls();
  try{settle(a,await request());}catch(err){await failure(err);}
  finally{busy=false;controls();}
}
async function finalize(a) {
  if(attempt!==a||a.cancelled||a.finished||a.failed||capture.active)return;
  a.finished=true;clearInterval(ticker);markEnd(a,'recognizer');
  const text=a.fragments.join(' ');
  a.apiText=text;
  $('quick-interim').textContent=a.limited?`${a.maxSeconds}초가 지나 자동으로 마쳤습니다. 이어서 말하려면 다시 누르세요.`:a.stopCommand?'종료 명령을 제외하고 앞의 내용만 정리합니다.':'말하기를 마쳤습니다. 이어서 말하려면 다시 누르세요.';
  if(text.length>RECORDING_LIMITS.text){a.apiText='';addUnsentHistory(a,'4000자를 넘어 전송하지 않았습니다. 원문을 복사해 나누어 입력하세요.');heard('');state('4000자 초과 · 전송 안 함');error('최종 본문은 4000자까지입니다. 인식 원문을 기록에 남겼습니다. 내용을 나누어 다시 말하세요.');controls();return;}
  if(!text.trim()&&a.stopCommand){a.apiText='';addUnsentHistory(a,'종료 명령만 있어 API에 보내지 않았습니다.');heard('');state('말하기 종료');$('quick-interim').textContent='종료 명령만 인식했습니다. 환자 본문과 새 초안을 만들지 않았습니다.';controls();return;}
  if(!text.trim()){heard('');state('인식문 없음');error('최종 인식문이 없습니다. 마이크와 브라우저 음성 인식 연결을 확인한 뒤 다시 말해 주세요.');controls();return;}
  if(a.target!=='answer'){await send(a,()=>flow.apply(text,a.mode));return;}
  // A one-breath answer may use a lower-ranked recognizer hypothesis that fits the question.
  a.pick=chooseAnswer(a.question,!a.stopCommand&&a.fragments.length===1?a.alternatives[0]:[text]);
  a.apiText=a.pick.text;
  if(a.pick.rank>0)heard(a.pick.text);
  await send(a,()=>flow.answer(a.question,a.pick.text,{unavailable:a.pick.unavailable}));
}
const capture=new VoiceCapture({
  onInterim(text){if(capture.active&&!attempt?.stopCommand&&!attempt?.failed&&!attempt?.cancelled)$('quick-interim').textContent=text||'듣고 있습니다…';},
  onFinal(text,alternatives){
    const a=attempt;if(!a||a.cancelled||a.finished||a.failed||!capture.active)return;
    a.sourceFinals.push(text);
    if(a.stopCommand){a.excludedFinals.push(text);$('recording-excluded-text').textContent=a.excludedFinals.join(' ');$('recording-excluded').hidden=false;heard(a.sourceFinals.join(' '));return;}
    const combined=[...a.fragments,text].join(' ');
    const stop=recordingMode==='toggle'?splitStopCommand(combined):null;
    if(stop){a.fragments=stop.prefix.trim()?[stop.prefix]:[];a.alternatives=stop.prefix.trim()?[[stop.prefix]]:[];}
    else if(text.trim()){a.fragments.push(text);a.alternatives.push(alternatives?.length?alternatives:[text]);}
    heard(a.sourceFinals.join(' '));
    if(stop){a.stopCommand=stop.command;press=null;void finish('voice');}
    else if(a.fragments.join(' ').length>=RECORDING_LIMITS.text){press=null;void finish('text_limit');}
  },
  onState(next){
    if(next==='listening'&&attempt){attempt.listening=true;state(attempt.target==='answer'?'답변을 듣고 있습니다':'듣고 있습니다');buzz();timelineEnd();}
    if(next==='idle'){clearInterval(ticker);const ending=attempt;if(ending?.endReason){ending.endedAt=Date.now();markEnd(ending,ending.endReason);}queueMicrotask(()=>{if(ending)finalize(ending);});}
    controls();
  },
  onError(err){
    if(err.code==='tts-failed'){error('읽어주기를 완료하지 못했습니다. 저장된 초안은 유지됩니다. '+err.message);controls();return;}
    if(attempt){attempt.failed=true;markEnd(attempt,'error');}clearInterval(ticker);state('음성 연결 확인 필요');error(err.message);$('quick-interim').textContent='최종 인식문은 그대로 남겨두었습니다.';controls();
  }
});

// Start cue: vibration only. A beep would play while the microphone opens and could be transcribed.
function buzz() {const nav=globalThis.navigator;if(nav?.userActivation?.hasBeenActive!==false)nav?.vibrate?.(30);}
function tick(a) {
  if(attempt!==a||!capture.active)return;
  const seconds=Math.floor((performance.now()-a.started)/1000);
  $('quick-time').textContent=String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0');
  if(seconds>=a.maxSeconds&&!stopping){a.limited=true;press=null;void finish('limit');}
}
async function record(target='transcript',held=false) {
  if(busy||stopping||flow.job||capture.active)return;
  const question=target==='answer'?currentQuestion():null;
  if(target==='answer'&&!question)return;
  error('');capture.stopSpeaking();
  const a={target,mode,question,held,maxSeconds:RECORDING_LIMITS[recordingMode],fragments:[],sourceFinals:[],excludedFinals:[],alternatives:[],started:performance.now(),listening:false};attempt=a;
  $('recording-ended').hidden=true;
  $('recording-excluded').hidden=true;$('recording-excluded-text').textContent='';
  heard('');$('quick-interim').textContent='마이크 연결을 기다리고 있습니다…';$('quick-time').textContent='00:00';state('마이크 준비 중');
  // While answering, bias toward every option of that question (never a specific number).
  try{
    phraseCount=capture.setPhrases(question?[...hintPlan.applied,...answerVocabulary(question)]:hintPlan.applied).length;
    const starting=capture.start('browser',question?{alternatives:5}:undefined);controls();
    await starting;if(attempt!==a||a.cancelled||!capture.active)return;
    a.started=performance.now();ticker=setInterval(()=>tick(a),500);
    $('quick-interim').textContent=!held?'이어서 말하세요. 마치면 버튼을 한 번 더 누르세요.':target==='answer'?'질문에 대한 답을 말하고 손을 떼세요.':'누른 채로 이어서 말하고, 다 말하면 손을 떼세요.';
    hintStatus();
  }catch(err){if(a.cancelled)return;a.failed=true;markEnd(a,'error');releaseCapture();clearInterval(ticker);error(err.message);state('음성 연결 확인 필요');}
  finally{controls();}
}
function releaseCapture() {try{capture.abort();}catch(err){error('마이크 연결 해제 오류: '+err.message);}}
async function finish(reason='button') {
  if(!capture.active||stopping)return;
  if(!attempt?.listening){cancel(attempt?.held?'마이크가 준비되기 전에 손을 뗐습니다. 버튼을 누른 채로 잠시 기다린 뒤 말하세요.':undefined);return;}
  const a=attempt;markEnd(a,reason);
  state('말하기 종료 중');stopping=true;controls();
  try{await capture.stop();if(capture.active)throw new Error('음성 인식이 종료되지 않아 연결을 해제했습니다.');}
  catch(err){a.failed=true;releaseCapture();markEnd(a,'error');clearInterval(ticker);state('음성 연결 확인 필요');error(err.message);$('quick-interim').textContent='최종 인식문은 그대로 남겨두었습니다.';}
  finally{stopping=false;controls();}
  await finalize(a);
}
function cancel(message='이번 말하기는 초안에 반영하지 않았습니다.'){press=null;if(attempt){attempt.cancelled=true;markEnd(attempt,'cancel');}releaseCapture();clearInterval(ticker);heard('');state('취소됨');$('quick-interim').textContent=message;controls();}
// Hold remains selectable. Pointer/keyboard gestures each consume the trailing click.
function pressStart(target) {
  if(press||busy||stopping||flow.job||capture.active||$(target==='answer'?'followup-record':'quick-record').disabled)return;
  press={target,at:performance.now()};
  return record(target,true).catch(err=>{error(err.message);controls();});
}
function pressEnd(target) {
  if(!press||press.target!==target)return;
  const held=performance.now()-press.at;press=null;
  if(held<HOLD_MS){if(capture.active)cancel('짧게 눌렀습니다. 버튼을 누른 채로 말하고, 다 말한 뒤 손을 떼세요.');return;}
  return finish('release');
}
function toggle(target) {
  if(busy||stopping||flow.job)return;
  if(capture.active)return attempt?.target===target?finish().catch(err=>{error(err.message);controls();}):undefined;
  return record(target).catch(err=>{error(err.message);controls();});
}
const pttKeys=new Set([' ','Spacebar','Enter']);
const resetGestures=[];
function bindPtt(id,target) {
  const button=$(id);let gesture=null, suppressUntil=-1;
  const suppress=()=>{suppressUntil=performance.now()+1000;};
  button.onpointerdown=event=>{
    if(event.button>0||event.isPrimary===false||gesture||button.disabled)return;
    gesture={type:'pointer',id:event.pointerId};suppress();
    try{button.setPointerCapture?.(event.pointerId);}catch{/* Capture is optional. */}
    return recordingMode==='hold'?pressStart(target):toggle(target);
  };
  button.onpointerup=event=>{
    if(gesture?.type!=='pointer'||(event?.pointerId!==undefined&&gesture.id!==event.pointerId))return;
    gesture=null;suppress();return recordingMode==='hold'?pressEnd(target):undefined;
  };
  button.onpointercancel=()=>{if(gesture?.type!=='pointer')return;gesture=null;suppress();if(recordingMode==='hold')cancel();};
  button.onkeydown=event=>{
    if(!pttKeys.has(event.key))return;event.preventDefault();
    if(event.repeat||gesture||button.disabled)return;
    gesture={type:'key',key:event.key};suppress();return recordingMode==='hold'?pressStart(target):toggle(target);
  };
  button.onkeyup=event=>{
    if(!pttKeys.has(event.key))return;event.preventDefault();
    if(gesture?.type!=='key'||gesture.key!==event.key)return;
    gesture=null;suppress();return recordingMode==='hold'?pressEnd(target):undefined;
  };
  button.oncontextmenu=event=>event.preventDefault();
  const resetGesture=()=>{const hadGesture=!!gesture;gesture=null;suppress();if(hadGesture&&recordingMode==='hold'&&capture.active&&attempt?.target===target&&!stopping)cancel('버튼에서 포커스가 벗어나 이번 말하기를 취소했습니다.');};
  button.onblur=resetGesture;
  button.onlostpointercapture=()=>{if(gesture?.type==='pointer')resetGesture();};
  resetGestures.push(resetGesture);
  button.onclick=()=>{if(performance.now()<=suppressUntil){suppressUntil=-1;return;}if(button.disabled)return;return toggle(target);};
}
// Tapped answers (option chips, "미측정 · 모름") go through the same answer API as spoken ones.
function tapAnswer(text,options={}) {
  const q=currentQuestion();if(!q||locked())return;
  error('');capture.stopSpeaking();
  const a={target:'answer',question:q,button:true,fragments:[text],finished:true};attempt=a;
  return send(a,()=>flow.answer(q,text,options));
}
function togglePanel(buttonId,panelId,openText,closedText) {
  const panel=$(panelId), open=panel.hidden;panel.hidden=!open;
  $(buttonId).setAttribute('aria-expanded',String(open));
  if(openText)$(buttonId).textContent=open?openText:closedText;
}
bindPtt('quick-record','transcript');
bindPtt('followup-record','answer');
window.addEventListener('blur',()=>{for(const reset of resetGestures)reset();});
for(const next of ['toggle','hold'])$('recording-'+next).onclick=()=>{if(locked())return;recordingMode=next;press=null;$('quick-interim').textContent=idleHint();controls();};
$('recording-excluded-copy').onclick=()=>{if(locked())return;manualCopiedSource=$('recording-excluded-text').textContent;$('recording-review').value=manualCopiedSource;$('settings').hidden=false;$('settings-toggle').setAttribute('aria-expanded','true');$('recording-manual').open=true;$('recording-review').focus?.();$('recording-review-status').textContent='직접 입력 검토칸으로 가져왔습니다. 검토한 뒤 반영 버튼을 누르세요. 자동 전송하지 않았습니다.';controls();};
$('recording-review').oninput=()=>{if(!$('recording-review').value.trim())manualCopiedSource='';controls();};
$('recording-review-apply').onclick=()=>{
  if(locked())return;
  const text=$('recording-review').value.trim();
  if(!text||text.length>RECORDING_LIMITS.text){error('직접 입력 본문은 1~4000자여야 합니다.');controls();return;}
  if(text===manualAppliedText)return;
  const a={manual:true,target:'transcript',mode:'dictation',fragments:[text],sourceFinals:[text],apiText:text,copiedFrom:manualCopiedSource,finished:true,endReason:'manual',endedAt:Date.now()};
  attempt=a;error('');capture.stopSpeaking();
  return send(a,()=>flow.apply(text,'dictation'));
};
$('quick-cancel').onclick=()=>cancel();
$('settings-toggle').onclick=()=>togglePanel('settings-toggle','settings');
$('summary-toggle').onclick=()=>togglePanel('summary-toggle','summary-detail','접기','전체 보기 ›');
for(const next of ['auto','dictation','command'])$('mode-'+next).onclick=()=>{if(locked()||(next==='auto'&&!autoAvailable))return;mode=next;$('mode-hint').textContent=modeHints[next];controls();};
$('quick-new').onclick=()=>{
  if(locked())return;
  capture.stopSpeaking();client=new VoiceClient({server:location.origin,actorId:actor});flow=new QuickSession(client);
  mode=autoAvailable?'auto':'dictation';attempt=null;historyCount=0;lastSpoken='';deferred=new Map();recent=null;manualAppliedText='';manualCopiedSource='';
  error('');heard('');$('quick-interim').textContent=idleHint();$('quick-status').textContent='새 기록을 준비했습니다.';$('quick-feedback').hidden=true;
  $('quick-history').replaceChildren();$('quick-history-count').textContent='0회';$('quick-time').textContent='00:00';
  $('recording-ended').hidden=true;$('recording-excluded').hidden=true;$('recording-review').value='';$('recording-review-status').textContent='자동 전송하지 않습니다. 검토한 뒤 반영 버튼을 누르세요.';
  $('mode-hint').textContent=modeHints[mode];state('준비됨');render();renderChanges();
};
$('quick-readback').onclick=()=>say(client.session?.speak_text);
$('followup-speak').onclick=()=>{const q=currentQuestion();if(!q||locked())return;lastSpoken=q.id+'|'+q.question;say(q.question);};
$('followup-deferred-retry').onclick=()=>{if(locked()||!deferred.size)return;deferred.clear();render();const q=currentQuestion();if(q&&$('followup-autoread').checked){lastSpoken=q.id+'|'+q.question;say(q.question);}};
$('followup-unavailable').onclick=()=>tapAnswer('미측정',{unavailable:true});
$('quick-retry').onclick=async()=>{if(busy||stopping||capture.active)return;const a=attempt;busy=true;error('');controls();try{const result=await flow.retry();if(a)settle(a,result);else render();$('quick-status').textContent='같은 요청으로 저장 결과를 확인했습니다.';}catch(err){error(err.message);}finally{busy=false;controls();}};
window.addEventListener('pagehide',()=>{press=null;if(attempt)attempt.cancelled=true;releaseCapture();capture.stopSpeaking();clearInterval(ticker);});
function hintStatus() {
  const count=phraseCount, excluded=hintPlan.excluded.length;
  const text={unsupported:'문맥 힌트: 이 브라우저는 지원하지 않아 일반 인식을 사용합니다.',pending:`문맥 힌트 ${count}개를 다음 말하기부터 브라우저 인식에 요청합니다.`,applied:`문맥 힌트 ${count}개를 브라우저 인식에 전달했습니다. 정확도 개선 여부는 측정하지 않았습니다.`,rejected:'문맥 힌트: 브라우저가 지원하지 않아 일반 인식으로 다시 시작했습니다.'}[capture.phraseStatus]||'';
  $('hint-status').textContent=text&&excluded?`${text} 값이 들어간 표현 ${excluded}개(정상·없음·숫자 등)는 정상 소견 쪽으로 끌릴 수 있어 제외했습니다.`:text;
}
function serverChip(text,tone) {$('server-chip').textContent=text;$('server-chip').className='qv-server'+(tone?' is-'+tone:'');}
async function checkServer() {
  let caps;
  try{caps=await client.api('/api/voice/capabilities');}
  catch{$('server-status').textContent='서버 음성 기능 정보를 읽지 못했습니다. 서버 실행 상태를 확인하세요.';serverChip('서버 연결 안 됨','bad');return;}
  autoAvailable=(Array.isArray(caps.transcript_modes)?caps.transcript_modes:[]).includes('auto');
  if(!autoAvailable&&mode==='auto'&&!locked()){mode='dictation';$('mode-hint').textContent=modeHints.dictation;}
  const current=caps.voice_revision===VOICE_REVISION;
  $('server-status').textContent=current?`서버 음성 기능 ${VOICE_REVISION} 확인. 자동 판단, 확인 질문, 변경 전후 표시를 사용합니다.`:`서버 음성 기능 버전이 다릅니다(${caps.voice_revision||'표시 없음'}). 이전 코드가 실행 중이면 자동 판단·확인 질문·변경 표시가 동작하지 않을 수 있습니다.`;
  $('server-status').className=current?'':'server-mismatch';
  serverChip(current?'음성 V2':'버전 다름',current?'ok':'warn');
  let terms={};
  try{terms=await client.api('/api/voice/terms');}catch{/* Hints are optional; plain recognition still works. */}
  const all=[...new Set([...(caps.speech_context_hints||[]),...(terms.speech_recognition_hints||[])].filter(h=>typeof h==='string').map(h=>h.trim()).filter(Boolean))];
  hintPlan={applied:all.filter(h=>!VALUE_HINT.test(h)),excluded:all.filter(h=>VALUE_HINT.test(h))};
  phraseCount=capture.setPhrases(hintPlan.applied).length;hintStatus();controls();
}
if(!VoiceCapture.capabilities().browser){state('지원 브라우저 필요');error('이 브라우저는 실시간 음성 인식을 지원하지 않습니다. 지원 브라우저에서 열어 주세요.');}
render();
checkServer();
