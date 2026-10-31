/**
 * Input-stage helpers for follow-up answers. They only pick among the recognizer's own
 * hypotheses and spot "not measured" answers; the server still interprets every value.
 */
const squeeze = text => String(text || '').toLowerCase().replace(/[\s.,!?~·'"。]/g, '');

// Real STT renderings of 미측정 include 비 측정 / 및 측정 / 미측. Stems are matched inside short
// answers only, and never when a number is present, so a spoken value is not turned into "unmeasured".
const UNAVAILABLE = /[미비및]측정?|[미비]확인|(?:측정|확인)(?:불가|못|안)|(?:못|안)(?:쟀|재|잼|잰|측정)|모르겠|모름|몰라|모르는|패스|pass|스킵|skip|넘어가|넘겨|건너뛰|나중에/;
const NUMERAL = /[0-9]|[일이삼사오육칠팔구십백]{2,}/;

export function isUnavailableAnswer(text) {
  const value = squeeze(text);
  return value.length > 0 && value.length <= 16 && !NUMERAL.test(value) && UNAVAILABLE.test(value);
}

// [strong, weak] shapes per question. Digits beat Korean number words, which STT also produces
// for unrelated syllables (real: 분당 이사).
const KOREAN_NUMBER = /[일이삼사오육칠팔구십백]{2,}|(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스물|서른|마흔|쉰|예순|일흔|여든|아흔)/;
const MEASURE = [/[0-9]/, KOREAN_NUMBER];
const SHAPES = {
  age: MEASURE, pulse: MEASURE, spo2: MEASURE, temperature: MEASURE, respiratory_rate: MEASURE, glucose: MEASURE,
  blood_pressure: [/[0-9]{2,3}\D{0,4}[0-9]{2,3}/, /(?:[0-9]{2,3}|[일이삼사오육칠팔구십백]{2,})\D{0,4}(?:[0-9]{2,3}|[일이삼사오육칠팔구십백]{2,})/],
  consciousness: [/명료|명확|또렷|저하|혼미|기면|반응|깨어|의식없|alert/],
  breathing: [/정상|비정상|없|곤란|힘들|가쁘|빠르|느리|얕|무호흡/],
  symptoms: [/있|없|네|아니|예/],
  sex: [/남|여/],
  onset_at: [/[0-9]|(?:분|시간|일)전|아까|방금/, /(?:한|두|세|네|다섯|열|스물|서른|마흔|쉰|예순)(?:시간|분|일)/],
};

/** 2: clearly answer-shaped (or "not measured"), 1: only Korean number words, 0: no fit. Ranking only. */
function fitLevel(question, text) {
  if (isUnavailableAnswer(text)) return 2;
  const [strong, weak] = SHAPES[question?.id] || [];
  const value = squeeze(text);
  return strong?.test(value) ? 2 : weak?.test(value) ? 1 : 0;
}

/** Whether a hypothesis has the shape of an answer to this question (ranking only, not parsing). */
export function answerFits(question, text) {
  return fitLevel(question, text) > 0;
}

/** Best-fitting hypothesis (earliest on ties), else the recognizer's top choice. */
export function chooseAnswer(question, alternatives) {
  const list = [...new Set((alternatives || []).map(item => String(item || '').trim()).filter(Boolean))];
  if (!list.length) return null;
  const levels = list.map(text => fitLevel(question, text)), best = Math.max(...levels);
  const rank = best > 0 ? levels.indexOf(best) : 0;
  return {text: list[rank], rank, alternatives: list, unavailable: isUnavailableAnswer(list[rank])};
}

// Balanced answer vocabulary for contextual biasing while a question is answered: every option of
// an enumerated question is listed together, and no specific number is ever listed.
const VOCABULARY = {
  consciousness: ['의식 명료', '의식 저하', '반응 없음', '명료', '저하'],
  breathing: ['호흡 정상', '호흡 비정상', '호흡 없음', '정상', '비정상'],
  blood_pressure: ['혈압', '수축기', '이완기'], pulse: ['맥박', '분당', '회'], spo2: ['산소포화도', '퍼센트'],
  temperature: ['체온', '도'], respiratory_rate: ['호흡수', '분당', '회'], glucose: ['혈당'],
  symptoms: ['있음', '없음'], onset_at: ['분 전', '시간 전'],
};
export function answerVocabulary(question) {
  return [...(VOCABULARY[question?.id] || []), '미측정', '모름', '확인 불가'];
}
