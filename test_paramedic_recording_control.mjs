import test from 'node:test';
import assert from 'node:assert/strict';
import {RECORDING_LIMITS,splitStopCommand,findVoiceTrigger} from './paramedic-ui/recording-control.mjs';

test('recording limits distinguish toggle, hold, and API text',()=>{
  assert.deepEqual(RECORDING_LIMITS,{toggle:180,hold:30,text:4000});
});

for(const [raw,prefix,command] of [
  ['그만','','그만'],['종료','','종료'],['녹음 종료','','녹음 종료'],
  ['음성 그만','','음성 그만'],['음성 입력 종료','','음성 입력 종료'],
  ['  녹음 종료해주세요.','','녹음 종료해주세요'],
  ['혈압12080 맥박92 그만','혈압12080 맥박92','그만'],
  ['혈압 120에 80. 맥박 92. 녹음 종료','혈압 120에 80. 맥박 92.','녹음 종료'],
  ['산소포화도 97퍼센트, 종료해 주세요!','산소포화도 97퍼센트,','종료해 주세요'],
  ['47세 남성. 그만해','47세 남성.','그만해'],
])test(`final command preserves the original prefix: ${raw}`,()=>{
  const result=splitStopCommand(raw);assert.ok(result);assert.equal(result.prefix,prefix);assert.equal(result.command,command);
  assert.equal(raw.slice(result.index,result.end),command);
  assert.equal(splitStopCommand(raw,{isFinal:false}),null,'interim never controls recording');
});

for(const raw of [
  '',null,'그만큼 통증이 심합니다','그만큼','종료시각 14시','종료 시각은 14시',
  '종료하지마','종료하지 마세요','녹음 종료하지 마','그만하지 마세요',
  '환자가 그만이라고 했습니다','환자가그만이라고','환자가 그만','환자가 이제 그만',
  '환자가 말하기를 그만','환자 진술 그만','보호자가 그만',
  '환자가 “그만”','“종료”','환자가 “이제 그만','환자가 "그만"이라고 말했습니다',
  '처치 종료 후 혈압 120에 80','CPR 종료','CPR을 종료','심폐소생술을 종료',
  '처치 종료합니다','처치를 종료','치료 종료해 주세요','경련 종료','검사 종료',
])test(`clinical/quoted/negative text is not a recording command: ${raw}`,()=>{
  assert.equal(splitStopCommand(raw),null);
});

// These expected controls are specified independently of parser output. A trigger
// may end recording in the middle of a final, but its trailing text is not lost.
const TRIGGER_CASES = [
  ['그만','stop','','그만',''],
  ['중단','stop','','중단',''],
  ['종료','stop','','종료',''],
  ['녹음 중단','stop','','녹음 중단',''],
  ['음성 입력 중단해 주세요','stop','','음성 입력 중단해 주세요',''],
  ['음성종료해주세요','stop','','음성종료해주세요',''],
  ['말하기 그만할게요','stop','','말하기 그만할게요',''],
  ['종료해 줘요','stop','','종료해 줘요',''],
  ['그만하세요','stop','','그만하세요',''],
  ['중단하겠습니다','stop','','중단하겠습니다',''],
  ['  녹음 종료해주세요.','stop','','녹음 종료해주세요','.'],
  ['혈압 120에 80 중단 맥박 90','stop','혈압 120에 80','중단','맥박 90'],
  ['혈압 120에 80. 종료. 맥박 90.','stop','혈압 120에 80.','종료','. 맥박 90.'],
  ['나이는 47세, 녹음 그만해 주세요! 보호자 도착','stop','나이는 47세,','녹음 그만해 주세요','! 보호자 도착'],
  ['산소포화도 97퍼센트 종료해 주세요 맥박 92','stop','산소포화도 97퍼센트','종료해 주세요','맥박 92'],
  ['환자 47세 남성 맥박 90 종료','stop','환자 47세 남성 맥박 90','종료',''],
  ['처치 중 녹음 종료','stop','처치 중','녹음 종료',''],
  ['CPR 종료. 녹음 중단','stop','CPR 종료.','녹음 중단',''],
  ['종료하지 마. 녹음 중단','stop','종료하지 마.','녹음 중단',''],
  ['환자가 “종료”라고 말함. 녹음 그만','stop','환자가 “종료”라고 말함.','녹음 그만',''],
  ['그만 종료 병원 추천해줘','stop','','그만','종료 병원 추천해줘'],
  ['병원 추천해줘','recommend','','병원 추천해줘',''],
  ['병원추천해줘','recommend','','병원추천해줘',''],
  ['병원 추천해주세요','recommend','','병원 추천해주세요',''],
  ['병원 추천해 주세요','recommend','','병원 추천해 주세요',''],
  ['병원 추천 해 줘','recommend','','병원 추천 해 줘',''],
  ['병원 추천해 줘요','recommend','','병원 추천해 줘요',''],
  ['흉통 10분 지속 병원 추천해줘','recommend','흉통 10분 지속','병원 추천해줘',''],
  ['명료 병원 추천해줘 맥박은 90','recommend','명료','병원 추천해줘','맥박은 90'],
  ['혈압 120에 80. 병원 추천해주세요! 종료','recommend','혈압 120에 80.','병원 추천해주세요','! 종료'],
  ['CPR 중단 후 자발순환 회복. 병원 추천해줘','recommend','CPR 중단 후 자발순환 회복.','병원 추천해줘',''],
  ['환자가 “병원 추천해줘”라고 말함. 병원 추천해주세요','recommend','환자가 “병원 추천해줘”라고 말함.','병원 추천해주세요',''],
  ['종료 시각은 14시. 병원 추천해줘','recommend','종료 시각은 14시.','병원 추천해줘',''],
  ['47세 남성 흉통입니다 병원 추천해줘 혈압 120에80','recommend','47세 남성 흉통입니다','병원 추천해줘','혈압 120에80'],
  ['47세 남성 중단 추가정보','stop','47세 남성','중단','추가정보'],
  ['47세 남성 종료 혈압120','stop','47세 남성','종료','혈압120'],
  ['녹음 종료 후 병원 추천해줘','stop','','녹음 종료','후 병원 추천해줘'],
  ['CPR, 종료. 녹음 중단','stop','CPR, 종료.','녹음 중단',''],
];

for(const [raw,action,prefix,command,suffix] of TRIGGER_CASES) {
  test(`first final voice trigger preserves both sides: ${raw}`,()=>{
    const result=findVoiceTrigger(raw);
    assert.ok(result);
    assert.equal(result.action,action);
    assert.equal(result.prefix,prefix);
    assert.equal(result.command,command);
    assert.equal(result.suffix,suffix);
    assert.equal(raw.slice(result.index,result.end),command,'span is in the unmodified source');
    assert.equal(result.prefix,raw.slice(0,result.index).trimEnd());
    assert.equal(result.suffix,raw.slice(result.end).trimStart());
    assert.equal(findVoiceTrigger(raw,{isFinal:false}),null,'interim cannot execute a control');
  });
}

const NON_TRIGGER_CASES = [
  '',null,undefined,123,
  '그만큼 통증이 심합니다','중단점 확인','종료시각 14시','종료 시각은 14시',
  '종료 시간 확인','중단 여부를 알려주세요','그만이라고 했습니다','종료라고 적혀 있습니다',
  '종료하지마','종료하지 마세요','녹음 종료하지 마','중단하지 말고 계속',
  '종료 하지 마','중단 해 주지 마','그만 말고 계속 기록해','녹음 종료해 주세요 말고 계속해',
  '병원 추천해줘 말고 지금 정보만 저장해','병원 추천하지 마','병원 추천해 주지 마',
  '아직 종료','절대 중단','종료 안 해도 돼',
  '처치 중단','CPR 중단','CPR을 중단','심폐소생술을 종료',
  '처치를 종료','처치는 종료','처치 즉시 중단','약물 중단','약물 투여 중단',
  '투약을 중단해 주세요','산소공급 중단','산소 공급을 중단','산소 공급을 즉시 중단',
  '산소공급 종료','수액 주입 종료','이송을 중단','호흡 중단','압박 잠시 중단',
  '처치 종료 후 혈압 120에 80','경련 종료','검사 종료','통화 종료',
  '환자가 종료','환자가 이제 중단','환자가 말하기를 그만','보호자가 녹음 종료',
  '동승자는 병원 추천해줘','목격자가 종료해달라 함','환자가 종료해 달라 함',
  '환자가 병원 추천해줘 라고 말했다','병원 추천해줘라고 했다',
  '병원 추천해주세요 라는 문구가 보입니다','명령어 종료','트리거 중단',
  '“종료”','‘중단’','「그만」','『병원 추천해줘』','"녹음 종료"',
  '환자가 “이제 종료','환자가 ‘병원 추천해줘’라고 말함',
  '“말하다가 ‘종료’ 후에 다시 말함”','환자가 "중단"이라고 말했습니다',
  '병원 추천해주면 좋겠어요','병원을 추천해 줬습니다','추천해줘','병원 추천',
  'CPR, 종료','처치. 중단','환자가, 종료','약물을 일단 중단',
  '산소 공급은 지금 바로 중단','처치 진행을 즉시 중단','압박을 잠시만 중단',
  '아직은 종료','절대로 중단','중단 마세요','종료 됐습니다','종료 했습니다',
  '종료 요청했습니다',
];

for(const raw of NON_TRIGGER_CASES)test(`clinical, negative, quoted, or non-imperative is not a trigger: ${raw}`,()=>{
  assert.equal(findVoiceTrigger(raw),null);
});

test('combined final fragments locate the complete recommendation command once',()=>{
  const combined=['혈압 120에 80','병원','추천해줘','맥박 90'].join(' ');
  assert.deepEqual(findVoiceTrigger(combined),{
    action:'recommend',prefix:'혈압 120에 80',command:'병원 추천해줘',suffix:'맥박 90',
    index:combined.indexOf('병원'),end:combined.indexOf(' 맥박'),
  });
});

test('the terminal compatibility helper never consumes a middle command or recommendation',()=>{
  assert.equal(splitStopCommand('혈압 120 종료 맥박 90'),null);
  assert.equal(splitStopCommand('병원 추천해줘'),null);
  assert.equal(splitStopCommand('종료 병원 추천해줘'),null);
  assert.deepEqual(splitStopCommand('혈압 120 중단!'),{
    prefix:'혈압 120',command:'중단',index:7,end:9,
  });
});

// Patient commands are explicit UI controls, never inferred from a patient
// description. Expectations are authored before extending the parser grammar.
const PATIENT_TRIGGER_CASES = [
  ['새 환자 시작','new_patient','','새 환자 시작',''],
  ['새환자시작','new_patient','','새환자시작',''],
  ['다음 환자 시작','new_patient','','다음 환자 시작',''],
  ['다음환자시작','new_patient','','다음환자시작',''],
  ['새 환자 시작해줘','new_patient','','새 환자 시작해줘',''],
  ['새 환자 시작해주세요','new_patient','','새 환자 시작해주세요',''],
  ['다음 환자 시작해 주세요','new_patient','','다음 환자 시작해 주세요',''],
  ['새환자 시작해 줘요','new_patient','','새환자 시작해 줘요',''],
  ['새 환자 시작하겠습니다','new_patient','','새 환자 시작하겠습니다',''],
  ['다음 환자 시작합니다','new_patient','','다음 환자 시작합니다',''],
  ['혈압 120에 80 새 환자 시작 맥박 92','new_patient','혈압 120에 80','새 환자 시작','맥박 92'],
  ['흉통 없음. 다음 환자 시작! 혈압 90에 60.','new_patient','흉통 없음.','다음 환자 시작','! 혈압 90에 60.'],
  ['이전 환자로 돌아가','previous_patient','','이전 환자로 돌아가',''],
  ['이전환자로돌아가','previous_patient','','이전환자로돌아가',''],
  ['이전 환자로 돌아가주세요','previous_patient','','이전 환자로 돌아가주세요',''],
  ['이전 환자로 돌아가 주세요','previous_patient','','이전 환자로 돌아가 주세요',''],
  ['이전환자로 돌아가줘','previous_patient','','이전환자로 돌아가줘',''],
  ['이전 환자 기록으로 돌아가','previous_patient','','이전 환자 기록으로 돌아가',''],
  ['이전환자기록으로돌아가주세요','previous_patient','','이전환자기록으로돌아가주세요',''],
  ['이전 환자 기록으로 돌아가 줘요','previous_patient','','이전 환자 기록으로 돌아가 줘요',''],
  ['47세 남성. 이전 환자로 돌아가 맥박 90','previous_patient','47세 남성.','이전 환자로 돌아가','맥박 90'],
  ['새 환자 시작 병원 추천해줘','new_patient','','새 환자 시작','병원 추천해줘'],
  ['이전 환자로 돌아가 새 환자 시작','previous_patient','','이전 환자로 돌아가','새 환자 시작'],
  ['종료 새 환자 시작','stop','','종료','새 환자 시작'],
  ['병원 추천해줘 다음 환자 시작','recommend','','병원 추천해줘','다음 환자 시작'],
  ['새 환자 시작하지 마. 이전 환자로 돌아가','previous_patient','새 환자 시작하지 마.','이전 환자로 돌아가',''],
  ['환자가 “새 환자 시작”이라고 말함. 다음 환자 시작','new_patient','환자가 “새 환자 시작”이라고 말함.','다음 환자 시작',''],
];
for(const [raw,action,prefix,command,suffix] of PATIENT_TRIGGER_CASES)test(`explicit patient action preserves old-patient speech: ${raw}`,()=>{
  const result=findVoiceTrigger(raw);assert.ok(result);
  assert.equal(result.action,action);assert.equal(result.prefix,prefix);assert.equal(result.command,command);assert.equal(result.suffix,suffix);
  assert.equal(raw.slice(result.index,result.end),command);
  assert.equal(findVoiceTrigger(raw,{isFinal:false}),null);
});

const PATIENT_NON_TRIGGER_CASES = [
  '새 환자 시작하지마','새 환자 시작 하지 마','새 환자 시작해 주지 마','새 환자 시작 해달라',
  '새 환자 시작 말고 기존 기록 유지','새 환자 시작 안돼','아직 새 환자 시작','절대 다음 환자 시작',
  '환자가 새 환자 시작','보호자가 다음 환자 시작','환자분께서 새 환자 시작',
  '환자가 새 환자 시작이라고 했습니다','다음 환자 시작해달라고 했습니다',
  '명령어 새 환자 시작','트리거 다음 환자 시작','“새 환자 시작”','‘다음 환자 시작’',
  '「새 환자 시작」','『이전 환자로 돌아가』','"이전 환자 기록으로 돌아가"',
  '새 환자 처치 시작','새 환자의 이송 시작','다음 환자 진료 시작','새 환자 시작시각 14시',
  '새 환자 시작 시간은 14시','새 환자 시작 예정입니다','새 환자 시작 여부 확인','새 환자 시작했습니다',
  '새 환자 시작이 필요합니다','다음 환자','이전 환자로 돌아가지 마','이전 환자로 돌아가 지 마',
  '이전 환자로 돌아가 주지 마','이전 환자로 돌아가면 안 됩니다','이전 환자로 돌아가기',
  '이전 환자로 돌아가 라고 보호자가 말함','이전 환자로 돌아가 안돼','환자가 이전 환자로 돌아가',
  '명령어 이전 환자 기록으로 돌아가','이전 환자로 돌아가 버튼','이전 환자 기록을 보여줘',
];
for(const raw of PATIENT_NON_TRIGGER_CASES)test(`patient description, report, negation or quote is not a switch: ${raw}`,()=>{
  assert.equal(findVoiceTrigger(raw),null);
});

test('split final patient commands work only when complete and preserve earlier source',()=>{
  for(const [parts,action,command] of [
    [['혈압 120에 80','새','환자','시작','맥박 92'],'new_patient','새 환자 시작'],
    [['호흡 정상','이전 환자','기록으로','돌아가주세요','혈당 105'],'previous_patient','이전 환자 기록으로 돌아가주세요'],
  ]){
    const result=findVoiceTrigger(parts.join(' '));assert.equal(result.action,action);assert.equal(result.command,command);
    assert.equal(result.prefix,parts[0]);assert.equal(result.suffix,parts.at(-1));
  }
  assert.equal(findVoiceTrigger('새 환자'),null);assert.equal(findVoiceTrigger('이전 환자 기록으로'),null);
});

test('new patient actions never change the terminal stop compatibility return shape',()=>{
  for(const raw of ['새 환자 시작','다음 환자 시작','이전 환자로 돌아가','이전 환자 기록으로 돌아가주세요'])assert.equal(splitStopCommand(raw),null);
  assert.deepEqual(splitStopCommand('혈압 120 종료.'),{prefix:'혈압 120',command:'종료',index:7,end:9});
});

const HANDOFF_TRIGGER_CASES = [
  ['인계 요약해줘','handoff_summary','','인계 요약해줘',''],
  ['인계요약해줘','handoff_summary','','인계요약해줘',''],
  ['인계용 요약해 주세요','handoff_summary','','인계용 요약해 주세요',''],
  ['인계 요약해주세요','handoff_summary','','인계 요약해주세요',''],
  ['인계 요약 해 줘','handoff_summary','','인계 요약 해 줘',''],
  ['인계 요약해 줘요','handoff_summary','','인계 요약해 줘요',''],
  ['병원 인계 요약해줘','handoff_summary','','병원 인계 요약해줘',''],
  ['병원인계용요약해주세요','handoff_summary','','병원인계용요약해주세요',''],
  ['인계 요약 보여줘','handoff_summary','','인계 요약 보여줘',''],
  ['인계용 요약 보여 주세요','handoff_summary','','인계용 요약 보여 주세요',''],
  ['혈압 120에 80 인계 요약해줘 맥박 92','handoff_summary','혈압 120에 80','인계 요약해줘','맥박 92'],
  ['출혈 없음. 병원 인계 요약해 주세요! 호흡 정상.','handoff_summary','출혈 없음.','병원 인계 요약해 주세요','! 호흡 정상.'],
  ['인계 요약해줘 새 환자 시작','handoff_summary','','인계 요약해줘','새 환자 시작'],
  ['새 환자 시작 인계 요약해줘','new_patient','','새 환자 시작','인계 요약해줘'],
  ['종료 인계 요약해줘','stop','','종료','인계 요약해줘'],
  ['인계 요약해주지 마. 인계 요약 보여줘','handoff_summary','인계 요약해주지 마.','인계 요약 보여줘',''],
  ['환자가 “인계 요약해줘”라고 말함. 인계용 요약해 주세요','handoff_summary','환자가 “인계 요약해줘”라고 말함.','인계용 요약해 주세요',''],
];
for(const [raw,action,prefix,command,suffix] of HANDOFF_TRIGGER_CASES)test(`dedicated handoff preview trigger preserves both sides: ${raw}`,()=>{
  const result=findVoiceTrigger(raw);assert.ok(result);assert.equal(result.action,action);
  assert.equal(result.prefix,prefix);assert.equal(result.command,command);assert.equal(result.suffix,suffix);
  assert.equal(raw.slice(result.index,result.end),command);assert.equal(findVoiceTrigger(raw,{isFinal:false}),null);
});

for(const raw of [
  '요약해줘','요약 보여줘','병원 요약해줘','인계해줘','인계 요약','인계용 요약문',
  '인계 요약해주지마','인계 요약해 주지 마','인계 요약 보여주지 마','인계 요약해줘 말고 계속',
  '인계 요약해줘라고 했다','인계요약이라고환자가말함','인계 요약해줘 라고 전달했다',
  '인계 요약해 주세요 라는 문구','인계 요약해주면 좋겠어요','인계 요약해야 합니다',
  '환자가 인계 요약해줘','보호자가 인계용 요약 보여줘','명령어 인계 요약해줘',
  '트리거 병원 인계 요약해줘','아직 인계 요약해줘','절대 인계 요약 보여줘',
  '“인계 요약해줘”','‘인계용 요약해 주세요’','「병원 인계 요약해줘」','"인계 요약 보여줘"',
])test(`handoff reports, negation, quotes and generic summaries remain non-controls: ${raw}`,()=>{
  assert.equal(findVoiceTrigger(raw),null);
});

test('a complete dedicated handoff command may span final segments and never becomes a stop compatibility result',()=>{
  const raw=['혈압 120에 80','병원 인계용','요약해','주세요','맥박 92'].join(' '),result=findVoiceTrigger(raw);
  assert.equal(result.action,'handoff_summary');assert.equal(result.prefix,'혈압 120에 80');
  assert.equal(result.command,'병원 인계용 요약해 주세요');assert.equal(result.suffix,'맥박 92');
  assert.equal(findVoiceTrigger('병원 인계용 요약해'),null);
  for(const text of ['인계 요약해줘','인계용 요약해 주세요','병원 인계 요약 보여줘'])assert.equal(splitStopCommand(text),null);
});
