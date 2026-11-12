import test from 'node:test';
import assert from 'node:assert/strict';
import {findVoiceTrigger,splitStopCommand} from '../../paramedic-ui/recording-control.mjs';

// Independent authored reference: expected no control, not a parser-generated label.
const LONG20='실제 환자가 아닌 임상 표현 구분용 합성 대본입니다. 문서에 ‘CPR 종료 후 자발순환 확인’이라고 적혀 있다는 상황을 읽습니다. 이 문장의 종료는 처치에 대한 과거 기록이며 녹음 제어 요청이 아닙니다. 산소 공급 중단 시각 14시라는 표현도 과거 시각 설명일 뿐 지금 어떤 처치를 하라는 지시가 아닙니다. 가상 대상의 혈압은 118에 78, 맥박은 94로 설정했으며 호흡 상태는 재확인이 필요합니다. 구토는 없다고 답했지만 출혈 여부는 미확인이라고 읽습니다. 종료 시각과 증상 발생 시각은 서로 다를 수 있고 증상 발생 시각은 모른다는 설정입니다. ‘그만’이라는 단어는 인용으로만 제시합니다. 지금 녹음은 종료하지 마세요. 전체 대본의 의도는 명령 없이 관찰 설명을 끝까지 읽는 것이며 실제 처치나 환자 데이터는 포함하지 않습니다.';

test('LONG-20 full clinical/time/quotation reference contains no recording control',()=>{
  assert.equal(findVoiceTrigger(LONG20),null);assert.equal(findVoiceTrigger(LONG20,{isFinal:false}),null);assert.equal(splitStopCommand(LONG20),null);
});

// These are references to time nouns with Korean particles. No instruction to
// stop recording is intended; punctuation need not separate a noun and particle.
const TIME_REFERENCES=[
  '종료 시각과 증상 발생 시각은 서로 다릅니다.',
  '종료 시간과 발견 시간은 따로 기록합니다.',
  '중단 시각과 재개 시각을 비교했습니다.',
  '중단 시간과 대기 시간을 구분합니다.',
  '종료 일시와 발견 일시는 서로 다릅니다.',
  '중단 일시도 아직 확인 전입니다.',
  '종료 일시의 근거는 합성 연습 기록입니다.',
  '종료 시각도 아직 미확인입니다.',
  '중단 시간도 다시 확인해야 합니다.',
  '종료 시각만 오후 두 시로 확인했습니다.',
  '중단 시간만 기록되어 있습니다.',
  '종료 시각의 근거는 목격자 진술입니다.',
  '중단 시간의 정확성은 확인하지 못했습니다.',
  '종료 시간에 보호자가 현장에 있었다고 합니다.',
  '중단 시각에서 오 분이 지난 상황입니다.',
  '종료 시각으로 추정한 값은 확정하지 않습니다.',
  '중단 시간부터 얼마나 지났는지는 모릅니다.',
  '종료 시각까지의 기록은 확인 대상입니다.',
  '중단 시간보다 증상 발생 시간이 먼저입니다.',
  '종료 시각처럼 미확인인 시각은 비워 둡니다.',
  '중단 시간이라는 항목이 화면에 있습니다.',
  '종료 시간이랑 측정 시간은 다릅니다.',
  '중단 시각하고 신고 시각을 혼동했습니다.',
  '종료 시간에는 별도 관찰이 없었습니다.',
  '중단 시각에서도 기록의 공백이 보입니다.',
  '종료 시각마다 정리한 자료는 합성 자료입니다.',
  '중단 시간조차 아직 확인되지 않았습니다.',
  '종료 시각밖에 기록되지 않았습니다.',
  '종료 시각이 오후 두 시라는 설명입니다.',
  '중단 시간은 미확인으로 남깁니다.',
  '종료 시각을 아직 확인하지 못했습니다.',
  '중단 시간, 발생 시간, 측정 시간을 구분합니다.',
];
for(const reference of TIME_REFERENCES)test(`time reference is not a control: ${reference}`,()=>{
  assert.equal(findVoiceTrigger(reference),null);assert.equal(findVoiceTrigger(reference,{isFinal:false}),null);
});

function assertTrigger(raw,action,prefix,command,suffix){
  const result=findVoiceTrigger(raw);assert.ok(result,'the independently intended control remains available');
  assert.deepEqual(result,{action,prefix,command,suffix,index:raw.indexOf(command,prefix.length),end:raw.indexOf(command,prefix.length)+command.length});
  assert.equal(raw.slice(result.index,result.end),command,'command span uses unmodified source offsets');
  assert.equal(raw.slice(0,result.index).trimEnd(),prefix,'no preceding clinical text is lost');
  assert.equal(raw.slice(result.end).trimStart(),suffix,'no trailing clinical text is lost');
  assert.equal(findVoiceTrigger(raw,{isFinal:false}),null,'interim text never classifies an actionable control');
}

const PRESERVED_CONTROLS=[
  ['종료 혈압120','stop','','종료','혈압120'],
  ['중단 맥박90','stop','','중단','맥박90'],
  ['종료. 맥박90','stop','','종료','. 맥박90'],
  ['그만 혈압 120에 80','stop','','그만','혈압 120에 80'],
  ['녹음 종료 후 맥박90을 확인합니다','stop','','녹음 종료','후 맥박90을 확인합니다'],
  ['음성 입력 종료 뒤 추가 관찰을 읽습니다','stop','','음성 입력 종료','뒤 추가 관찰을 읽습니다'],
  ['말하기 중단 후 뒤쪽 원문도 보관합니다','stop','','말하기 중단','후 뒤쪽 원문도 보관합니다'],
  ['혈압 120에 80. 녹음 종료','stop','혈압 120에 80.','녹음 종료',''],
  ['종료 시각과 발생 시각은 다릅니다. 병원 추천해줘 맥박 90','recommend','종료 시각과 발생 시각은 다릅니다.','병원 추천해줘','맥박 90'],
  ['중단 시간도 미확인입니다. 새 환자 시작 혈압은 미측정','new_patient','중단 시간도 미확인입니다.','새 환자 시작','혈압은 미측정'],
  ['종료 시각만 기록했습니다. 이전 환자로 돌아가 주세요 구토 없음','previous_patient','종료 시각만 기록했습니다.','이전 환자로 돌아가 주세요','구토 없음'],
  ['중단 시간의 근거는 미확인입니다. 병원 인계 요약해줘 체온 36.7도','handoff_summary','중단 시간의 근거는 미확인입니다.','병원 인계 요약해줘','체온 36.7도'],
  ['종료 시간에 관찰했습니다. 종료 혈압 120','stop','종료 시간에 관찰했습니다.','종료','혈압 120'],
  ['중단 시각에서 오 분 지났습니다. 녹음 종료 후 메모','stop','중단 시각에서 오 분 지났습니다.','녹음 종료','후 메모'],
  ['병원 추천해줘 종료 시각과 발생 시각은 다릅니다','recommend','','병원 추천해줘','종료 시각과 발생 시각은 다릅니다'],
  ['새 환자 시작 중단 시간도 미확인입니다','new_patient','','새 환자 시작','중단 시간도 미확인입니다'],
  ['이전 환자로 돌아가 종료 시각의 근거를 확인합니다','previous_patient','','이전 환자로 돌아가','종료 시각의 근거를 확인합니다'],
  ['인계 요약해줘 종료 시간이랑 측정 시간은 다릅니다','handoff_summary','','인계 요약해줘','종료 시간이랑 측정 시간은 다릅니다'],
];
for(const [raw,...expected]of PRESERVED_CONTROLS)test(`explicit control and both original sides remain intact: ${raw}`,()=>assertTrigger(raw,...expected));

// Synthetic lexical-boundary invariants, not claims about a clinical intention.
// A longer following token must not be split into a time noun plus a partial
// particle. Preserve the baseline stop span and the entire uninterpreted tail.
const LEXICAL_BOUNDARIES=[
  ['종료 시각화면 확인','종료','시각화면 확인'],
  ['중단 시간차 확인','중단','시간차 확인'],
  ['종료 시각에서버 확인','종료','시각에서버 확인'],
  ['종료 시각과학 문제','종료','시각과학 문제'],
  ['중단 시간표 확인','중단','시간표 확인'],
  ['그만 시간대 확인','그만','시간대 확인'],
  ['종료 일시정지 표시','종료','일시정지 표시'],
  ['중단 시각장애 여부 확인','중단','시각장애 여부 확인'],
];
for(const [raw,command,suffix]of LEXICAL_BOUNDARIES)test(`lexical boundary preserves baseline control without inferring a medical intent: ${raw}`,()=>assertTrigger(raw,'stop','',command,suffix));

test('fragmented final time reference stays non-command after the particle arrives',()=>{
  const fragments=['종료','시각과 증상 발생 시각은','서로 다릅니다'];assert.equal(findVoiceTrigger(fragments.join(' ')),null);assert.equal(findVoiceTrigger(fragments[0],{isFinal:false}),null);
});
test('fragmented later recommendation is found after skipping the complete earlier time reference',()=>{
  const fragments=['종료 시각과 발생 시각은 다릅니다.','병원','추천해줘','맥박 90'],raw=fragments.join(' ');
  assertTrigger(raw,'recommend','종료 시각과 발생 시각은 다릅니다.','병원 추천해줘','맥박 90');assert.deepEqual(fragments,['종료 시각과 발생 시각은 다릅니다.','병원','추천해줘','맥박 90']);
});
test('fragmented explicit recording end still wins before its after-text',()=>{
  const raw=['혈압 120.','녹음 종료','후 맥박 90을 확인합니다'].join(' ');assertTrigger(raw,'stop','혈압 120.','녹음 종료','후 맥박 90을 확인합니다');
});
test('terminal compatibility API keeps its original stop-command shape',()=>{
  assert.deepEqual(splitStopCommand('혈압 120. 녹음 종료!'),{prefix:'혈압 120.',command:'녹음 종료',index:8,end:13});
  assert.equal(splitStopCommand('종료 시각과 발생 시각은 다릅니다'),null);assert.equal(splitStopCommand('종료 혈압120'),null);
});
