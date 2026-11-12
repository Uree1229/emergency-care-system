import test from 'node:test';
import assert from 'node:assert/strict';
import {isUnavailableAnswer, isRemainingUnavailableAnswer, answerFits, chooseAnswer, answerVocabulary} from '../../paramedic-ui/answer-match.mjs';

// Strings marked "real" are browser STT outputs from the 2026-10-30 18:47 live-microphone session
// (verification server, virtual patient). The rest are synthetic. No accuracy is measured here.
const q = id => ({id});

test('"not measured" survives real STT damage and common spoken forms', () => {
  for (const text of ['비 측정', '및 측정', // real
    '미측정', '미측', '미 측정이요', '혈당 비측정', '측정 안 했어요', '아직 안 쟀어요', '못 쟀어',
    '모르겠습니다', '잘 몰라요', '모름', '확인 불가', '미확인', '패스', '넘어가 주세요', '나중에 잴게요']) {
    assert.equal(isUnavailableAnswer(text), true, text);
  }
});

test('values, absences and look-alike words are never read as "not measured"', () => {
  for (const text of ['비정상', '호흡 없음', '반응 없음', '정상', '92회', '미측정 92', '분당 20분', '구십이',
    '편안했어요', '모르핀', '넘어졌어요', '안 좋아요', '호흡 정상', '고급 정장', '', '   ',
    '모르겠어요 그런데 아까 측정했을 때는 맥박이 구십이였어요']) {
    assert.equal(isUnavailableAnswer(text), false, text);
  }
});

test('answer shapes: numbers for measurements, option words for enumerated questions', () => {
  assert.equal(answerFits(q('respiratory_rate'), '분당 20분'), true);  // real
  assert.equal(answerFits(q('respiratory_rate'), '분당 입회'), false); // real
  assert.equal(answerFits(q('respiratory_rate'), '분당 이사'), true);  // real: syllables that look like number words (weak)
  assert.equal(answerFits(q('pulse'), '구십이 회'), true);
  assert.equal(answerFits(q('blood_pressure'), '혈압 110에 70'), true); // real
  assert.equal(answerFits(q('blood_pressure'), '120 80'), true);
  assert.equal(answerFits(q('blood_pressure'), '120'), false);
  assert.equal(answerFits(q('breathing'), '고급 정장'), false); // real
  assert.equal(answerFits(q('breathing'), '호흡 정상'), true);  // real
  assert.equal(answerFits(q('consciousness'), '의식 명료'), true); // real
  assert.equal(answerFits(q('glucose'), '및 측정'), true); // real, as "not measured"
  assert.equal(answerFits(q('unknown_question'), '아무 말'), false);
});

test('a lower-ranked hypothesis is used only when the top one does not fit the question', () => {
  assert.deepEqual(chooseAnswer(q('respiratory_rate'), ['분당 입회', '분당 20회', '분당 21회']),
    {text: '분당 20회', rank: 1, alternatives: ['분당 입회', '분당 20회', '분당 21회'], unavailable: false});
  assert.equal(chooseAnswer(q('breathing'), ['고급 정장', '호흡 정상']).text, '호흡 정상');
  assert.deepEqual([chooseAnswer(q('respiratory_rate'), ['분당 이사', '분당 24회']).text, chooseAnswer(q('respiratory_rate'), ['분당 이사']).rank], ['분당 24회', 0]);
  assert.equal(chooseAnswer(q('blood_pressure'), ['혈압 백십에 칠십', '혈압 110에 70']).text, '혈압 110에 70');
  assert.equal(chooseAnswer(q('glucose'), ['혈당 105', '혈당 미측정']).text, '혈당 105');
  assert.deepEqual(chooseAnswer(q('pulse'), ['92회', '91회']), {text: '92회', rank: 0, alternatives: ['92회', '91회'], unavailable: false});
  assert.deepEqual(chooseAnswer(q('glucose'), ['비 측정']), {text: '비 측정', rank: 0, alternatives: ['비 측정'], unavailable: true});
  const none = chooseAnswer(q('respiratory_rate'), ['분당 시', '분당 씨']);
  assert.deepEqual([none.text, none.rank], ['분당 시', 0]);
  assert.deepEqual(chooseAnswer(q('pulse'), [' ', '', ' 92 ', '92']).alternatives, ['92']);
  assert.equal(chooseAnswer(q('pulse'), []), null);
});

test('answer vocabulary lists every option together and never a specific number', () => {
  assert.deepEqual(answerVocabulary(q('breathing')), ['호흡 정상', '호흡 비정상', '호흡 없음', '정상', '비정상', '미측정', '모름', '확인 불가']);
  assert.deepEqual(answerVocabulary(q('consciousness')).slice(0, 3), ['의식 명료', '의식 저하', '반응 없음']);
  for (const id of ['age', 'blood_pressure', 'pulse', 'spo2', 'temperature', 'respiratory_rate', 'glucose', 'onset_at', 'nope']) {
    assert.ok(answerVocabulary(q(id)).every(word => !/[0-9]|[일이삼사오육칠팔구십백]{2,}/.test(word)), id);
    assert.ok(answerVocabulary(q(id)).includes('미측정'), id);
  }
});

test('explicit remaining-unknown scope is ranked without coercing it into a single unavailable answer',()=>{
  for(const text of ['남은 항목 전부 다 모름으로 해줘','항목 전부 다 모름 처리','남은 항목 전부 미확인으로 남겨줘','나머지는 모르겠어요','전부 미확인']){
    assert.equal(isRemainingUnavailableAnswer(text),true,text);
    assert.equal(isUnavailableAnswer(text),false,text);
    assert.equal(answerFits(q('breathing'),text),true,text);
    assert.equal(chooseAnswer(q('breathing'),[text]).unavailable,false,text);
  }
});

test('damaged STT, negation, reports and mixed values cannot expand into unknown commands',()=>{
  for(const text of ['남은 항목 전부 다 무릎으로 해줘','항목 전부 다 모름 처리 모윰으로 처리','모름 처리하지 마',
    '남은 항목 전부 모름 처리하지 말고','남은 항목 전부 미확인이라고 했어요','남은 항목 전부 미확인?',
    '“남은 항목 전부 미확인”','모름이 아니에요','남은 항목 미확인 혈압 120에 80','남은 항목 전부 모름이면']){
    assert.equal(isRemainingUnavailableAnswer(text),false,text);
    assert.equal(isUnavailableAnswer(text),false,text);
  }
});
