// Presentation layer only. The existing capture, command decision, labels,
// audio archive, storage recovery and dataset exports keep their original owners.
// A new module URL also refreshes the legacy preview's cached entry module.
import './command-practice.js?studio=20261031';
import {VoiceCapture} from './speech.mjs';

const $ = id => document.getElementById(id);
const settings = $('studio-settings');
const records = $('studio-records');
const results = $('studio-results');
const record = $('practice-record');
const abort = $('practice-abort');
const prompt = $('practice-prompt');
const familyNames = {
  all:'모든 문장', long:'긴 문장', stop:'종료 명령', recommend:'추천 명령',
  new_patient:'새 환자', previous_patient:'이전 환자', handoff_summary:'인계 요약',
  negation:'부정·금지', quoted_reported:'인용·보고', clinical_overlap:'처치·상태',
  multiple:'여러 명령', late_final:'늦게 붙는 문맥',
};
let lastPrompt = null;
let activeBefore = false;
let pendingReveal = false;
let skipReveal = false;
let observer;

function showDialog(dialog, focusId) {
  if (!dialog.open) dialog.showModal();
  sync();
  if (focusId) $(focusId).focus();
}
function showResults({scroll = true} = {}) {
  if (settings.open) settings.close();
  if (records.open) records.close();
  results.open = true;
  if (scroll) results.scrollIntoView({behavior:'smooth', block:'start'});
}
function beginAgain() {
  if (record.disabled || !abort.disabled) return;
  $('practice-repeat').click();
  results.open = false;
  $('studio-reading-title').scrollIntoView({behavior:'smooth', block:'start'});
  record.focus({preventScroll:true});
}
function stylePassage() {
  const text = prompt.textContent;
  if (text === lastPrompt) return;
  lastPrompt = text;
  // Use text nodes throughout; an imported/custom passage is never HTML.
  const fragments = text.split(/(\d+(?:\.\d+)?)/g).map((part, index) => {
    if (index % 2 === 0) return document.createTextNode(part);
    const strong = document.createElement('strong');
    strong.textContent = part;
    return strong;
  });
  prompt.replaceChildren(...fragments);
  document.body.classList.toggle('body-long', text.length > 140);
}
function sync() {
  stylePassage();
  const isActive = !abort.disabled;
  const processing = isActive && record.disabled;
  const hasError = !$('practice-error').hidden;
  const supported = VoiceCapture.capabilities().browser;
  const option = $('practice-case').selectedOptions[0];
  const custom = $('practice-custom-enabled').checked;
  const count = $('practice-case').options.length;
  const index = $('practice-case').selectedIndex;
  $('studio-family').textContent = custom ? '자유 발화' : familyNames[$('practice-group').value] || '연습 문장';
  $('studio-progress').replaceChildren();
  const position = document.createElement('strong');
  position.textContent = custom ? '자유' : String(Math.max(0,index+1));
  $('studio-progress').append(position, document.createTextNode(custom ? ' 발화' : ` / ${count} 문장`));
  $('studio-case-meta').textContent = custom ? '직접 만든 문장 · 연습용' : option ? `${option.value} · 연습용` : '조건에 맞는 문장이 없어요';
  $('studio-next').disabled = isActive || record.disabled && !supported || !count || $('practice-next').disabled;
  $('studio-picker-open').disabled = isActive;
  document.body.classList.toggle('recording', isActive && !processing);
  document.body.classList.toggle('processing', processing);
  $('studio-state').textContent = hasError ? '확인이 필요해요' : processing ? '마무리 중이에요' : isActive ? '듣고 있어요' : !supported ? '지원 브라우저가 필요해요' : record.disabled ? '준비를 확인해 주세요' : '준비됐어요';
  $('studio-state').dataset.state = hasError ? 'error' : isActive ? 'recording' : 'ready';
  $('studio-instruction').textContent = processing ? '인식문과 녹음 파일을 보관하고 있어요' : isActive ? '끝까지 읽은 뒤, 다시 눌러 마쳐요' : '누르고, 끝까지 읽고, 다시 눌러 마쳐요';
  $('studio-mic-copy').textContent = isActive ? '마이크 사용 중' : '시작할 때 마이크 확인';
  $('practice-status').classList.toggle('is-visible', isActive || hasError || !supported);
  const unsupportedMessage = '이 브라우저는 음성 인식을 지원하지 않습니다. Chrome 또는 Edge에서 열어주세요.';
  if (!supported && $('practice-status').textContent !== unsupportedMessage) $('practice-status').textContent = unsupportedMessage;
  // During recording a recognizer may already have ended; do not advertise a
  // permanent device connection or successful capture before the actual result.
  if (abort.hidden !== !isActive) abort.hidden = !isActive;
  $('studio-count').textContent = $('practice-counts-total').textContent;
  for (const dialog of [settings, records]) {
    const feedback = dialog.querySelector('.dialog-feedback');
    const notice = $('practice-status').textContent;
    const message = hasError ? $('practice-error').textContent : dialog === records && /ZIP|JSON|복구|합쳤|실패|가득/.test(notice) ? notice : '';
    feedback.textContent = message;
    feedback.hidden = !message;
    feedback.classList.toggle('feedback-error', hasError);
  }
  const manual = $('practice-selected').textContent.includes('수동 모의');
  $('studio-transcript-label').textContent = manual ? '입력한 모의 인식문 · 마이크 테스트 아님' : '브라우저가 확정 전달한 인식문';
  $('studio-results-open').hidden = !$('practice-selected').textContent || $('practice-selected').textContent.startsWith('시도 목록');
  if (activeBefore && !isActive) pendingReveal = true;
  activeBefore = isActive;
  if (pendingReveal && !isActive && !skipReveal) {
    pendingReveal = false;
    showResults();
  }
}

$('studio-settings-open').addEventListener('click', () => showDialog(settings));
$('studio-picker-open').addEventListener('click', () => showDialog(settings, 'practice-case'));
$('studio-records-open').addEventListener('click', () => showDialog(records));
for (const button of document.querySelectorAll('[data-close-dialog]')) {
  button.addEventListener('click', () => button.closest('dialog').close());
}
$('studio-next').addEventListener('click', () => {
  $('practice-next').click();
  results.open = false;
  sync();
});
$('studio-again').addEventListener('click', beginAgain);
$('studio-results-open').addEventListener('click', () => showResults());
record.addEventListener('click', () => {
  if (abort.disabled) results.open = false;
});
$('practice-simulate').addEventListener('click', () => { pendingReveal=true; sync(); });
$('practice-records').addEventListener('click', event => {
  if (event.target.closest('button')) showResults();
});
$('practice-verify-next').addEventListener('click', () => {
  skipReveal = true;
  pendingReveal = false;
  results.open = false;
  $('studio-reading-title').scrollIntoView({behavior:'smooth', block:'start'});
  record.focus({preventScroll:true});
  queueMicrotask(() => { skipReveal=false; pendingReveal=false; });
});

// The original page intentionally initializes all cases. Begin the reading
// studio with its existing long-passage collection without changing the source
// datasets, authored answer labels, or saved collection.
$('practice-group').value = 'long';
$('practice-group').dispatchEvent(new Event('change', {bubbles:true}));
sync();

observer = new MutationObserver(changes => {
  if (changes.some(change => change.target === $('practice-selected') || $('practice-selected').contains(change.target))) pendingReveal=true;
  sync();
});
for (const id of ['practice-prompt','practice-record','practice-abort','practice-error','practice-status','practice-counts-total','practice-selected','practice-verify-next']) {
  observer.observe($(id), {childList:true, subtree:true, attributes:true, attributeFilter:['disabled','hidden'], characterData:true});
}
for (const id of ['practice-case','practice-group','practice-custom-enabled','practice-reference']) {
  $(id).addEventListener(id==='practice-reference'?'input':'change', sync);
}
// Keep observing when the browser restores this document from its back/forward
// cache. The observer belongs to this document and is released on real unload.
