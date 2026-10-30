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
