import {VoiceCapture} from './speech.mjs';
import {requestId} from './voice-client.mjs';
const $=id=>document.getElementById(id);
const familyLabels={demographics:'나이·성별',measurements_arabic:'숫자 활력징후',measurements_korean:'한글 수 읽기',abbreviations_units:'약어·단위',symptom_positive:'증상 있음',negation:'증상 없음',uncertainty:'불확실한 진술',omission_nonclinical:'미언급·일반 문구',history_family:'과거·가족 정보',questions:'질문',corrections_conflicts:'정정·상충',commands_edit_add:'수정·추가 명령',commands_review_control:'조회·제어 명령',invalid_ambiguous:'잘못된 값·모호함',onset_and_multi:'발생 시각·복합 문장'};
let corpus,current,attempt=null,completed=[],stopping=false,transcript=[];
function error(text){$('eval-error').textContent=text;$('eval-error').hidden=!text;}
function status(text){$('eval-status').textContent=text;}
function controls(){const active=capture.active||stopping,unsaved=attempt&&!attempt.saved;
  for(const id of ['speaker','condition','eval-family','eval-case','next-case','simulate-result'])$(id).disabled=active||!!unsaved;
  $('eval-record').disabled=stopping||!current||!VoiceCapture.capabilities().browser||(!capture.active&&!!unsaved);
  $('eval-record').textContent=capture.active?'읽기 마치기':'읽기 시작';$('eval-abort').disabled=!active&&!unsaved;
  $('save-recording').disabled=active||!attempt||!!attempt.saved||!$('reference-verified').checked;
  $('export-recordings').disabled=!completed.length||active;}
function choose(){
  current=corpus.cases.find(c=>c.id===$('eval-case').value);attempt=null;transcript=[];
  $('hypothesis').value='';$('reference-verified').checked=false;$('eval-interim').textContent='';
  $('prompt').textContent=current.reference_text;$('case-mode').textContent=`${current.id} · ${current.mode==='command'?'명령 모드':'환자 상태 받아쓰기'}`;controls();
}
function family(){const selected=corpus.cases.filter(c=>$('eval-family').value==='all'||c.family===$('eval-family').value);
  $('eval-case').replaceChildren(...selected.map(c=>new Option(c.id+' · '+c.reference_text.slice(0,24),c.id)));choose();}
function begin(source){if(!$('speaker').value.trim())throw Error('익명 화자 ID를 입력하세요.');
  attempt={recording_id:'R-'+requestId(),case_id:current.id,source,engine:source==='human_voice'?'browser-speech-recognition':'synthetic-example',speaker_id:$('speaker').value.trim(),condition:$('condition').value,status:'no_match',hypothesis:'',reference_verified:false,timing:{},collection:{mode:current.mode,actual_audio_file_saved:false}};
  transcript=[];
}
const capture=new VoiceCapture({
  onInterim(text){$('eval-interim').textContent=text;},
  onFinal(text){if(!attempt)return;transcript.push(text);attempt.hypothesis=transcript.join(' ');attempt.collection.received_final_fragments=[...transcript];attempt.status='ok';attempt.timing.stt_final_ms=performance.now();$('hypothesis').value=attempt.hypothesis;},
  onState(state){if(state==='listening'&&attempt)attempt.collection.capture_started_ms=performance.now();if(state==='idle'){if(attempt)attempt.collection.capture_closed_ms=performance.now();status('인식 결과와 대본을 확인한 뒤 결과를 기록하세요.');}controls();},
  onError(err){if(attempt){attempt.status='error';attempt.raw_hypothesis=transcript.join(' ');attempt.hypothesis='';attempt.collection.recognition_error_code=err.code||'unknown';$('hypothesis').value=attempt.raw_hypothesis;}error(err.message);controls();}
});
async function stop(){if(!capture.active)return;if(attempt)attempt.collection.capture_stop_requested_ms=performance.now();stopping=true;controls();try{await capture.stop();}finally{stopping=false;controls();}}
$('eval-record').onclick=async()=>{error('');try{if(capture.active){await stop();return;}if(attempt&&!attempt.saved)throw Error('현재 결과를 기록하거나 시험 취소 후 다시 시작하세요.');begin('human_voice');$('hypothesis').value='';$('reference-verified').checked=false;const starting=capture.start('browser');controls();await starting;status('대본을 그대로 읽으세요.');controls();}catch(err){if(attempt){attempt.status='error';attempt.raw_hypothesis=transcript.join(' ');attempt.hypothesis='';attempt.collection.recognition_error_code=err.code||'start-failed';}error(err.message);controls();}};
$('eval-abort').onclick=()=>{capture.abort();attempt=null;transcript=[];stopping=false;$('hypothesis').value='';status('취소했습니다. 이 시도는 기록하지 않습니다.');controls();};
$('save-recording').onclick=()=>{if(!attempt||attempt.saved||capture.active||!$('reference-verified').checked)return;
  attempt.reference_verified=true;completed.push(JSON.parse(JSON.stringify(attempt)));attempt.saved=true;
  $('record-count').textContent=String(completed.length);const row=document.createElement('p');row.textContent=`${attempt.case_id} · ${attempt.engine} · ${attempt.status} · ${attempt.hypothesis||'인식 결과 없음'}`;$('eval-log').prepend(row);status('결과를 메모리에 기록했습니다. 내려받기 전에는 파일로 저장되지 않습니다.');controls();};
$('simulate-result').onclick=()=>{try{begin('synthetic_corruption');attempt.hypothesis=current.reference_text;attempt.status='ok';$('hypothesis').value=attempt.hypothesis;$('reference-verified').checked=false;status('합성 예시입니다. 실제 녹음 성능으로 집계하지 않습니다.');controls();}catch(err){error(err.message);}};
$('reference-verified').onchange=controls;$('eval-case').onchange=choose;$('eval-family').onchange=family;
$('next-case').onclick=()=>{const select=$('eval-case');select.selectedIndex=(select.selectedIndex+1)%select.options.length;choose();};
$('export-recordings').onclick=()=>{const packageData={schema_version:1,dataset_id:corpus.dataset_id,corpus_sha256:corpus.corpus_sha256,collection_note:'Synthetic prompts; speaker confirms verbatim reading. No clinician review or workload time measurement.',recordings:completed};
  const blob=new Blob([JSON.stringify(packageData,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download='speech-recordings-'+Date.now()+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
window.addEventListener('pagehide',()=>{capture.abort();capture.stopSpeaking();});
fetch('./evaluation-cases.json',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error('대본 파일을 읽지 못했습니다.');return r.json();}).then(data=>{corpus=data;
  $('eval-family').replaceChildren(new Option('모든 유형','all'),...[...new Set(data.cases.map(c=>c.family))].map(name=>new Option(familyLabels[name]||name,name)));family();
  status(VoiceCapture.capabilities().browser?'대본을 고르고 읽기 시작을 누르세요.':'이 브라우저의 실시간 음성 인식을 사용할 수 없습니다. 합성 결과로 수집 화면을 시험할 수 있습니다.');
}).catch(err=>error(err.message));
