import test from 'node:test';
import assert from 'node:assert/strict';
import {RECORDING_LIMITS,splitStopCommand} from './paramedic-ui/recording-control.mjs';

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
