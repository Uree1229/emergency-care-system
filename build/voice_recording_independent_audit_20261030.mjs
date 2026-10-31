/** Independent, synthetic control-boundary probes. No UI, microphone, network, or DB. */
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync} from 'node:fs';

const sourceUrl = new URL('../paramedic-ui/recording-control.mjs', import.meta.url);
const hash = () => createHash('sha256').update(readFileSync(sourceUrl)).digest('hex');
const before = hash();
const {splitStopCommand, RECORDING_LIMITS} = await import(sourceUrl.href);

// Human-readable expectations are authored separately from the implementation.
// null means ordinary text, not a recording-control command.
const cases = [
  ['bare_stop', '그만', ''],
  ['bare_end', '종료', ''],
  ['record_stop', '녹음 그만', ''],
  ['record_end', '녹음 종료', ''],
  ['voice_stop', '음성 그만', ''],
  ['voice_end', '음성 종료', ''],
  ['voice_input_end', '음성 입력 종료', ''],
  ['speaking_end', '말하기 종료', ''],
  ['polite_end', '종료해 주세요', ''],
  ['polite_stop', '그만 해줘', ''],
  ['direct_end', '종료해', ''],
  ['direct_stop', '그만해', ''],
  ['terminal_prefix', '혈압 120에 80. 종료', '혈압 120에 80.'],
  ['explicit_terminal_prefix', '47세 남성. 음성 입력 종료', '47세 남성.'],
  ['unicode_punctuation', '맥박 90회。 종료！', '맥박 90회。'],
  ['punctuation_only_command', '  종료。  ', ''],
  ['plain_clinical', '환자 47세 남성, 흉통 있음.', null],
  ['similar_word', '통증이 그만큼 심합니다.', null],
  ['compound_noun', '처치 종료시각은 14시입니다.', null],
  ['clinical_after', '처치 종료 후 혈압 120에 80.', null],
  ['clinical_bare', '처치 종료', null],
  ['clinical_particle', '처치를 종료', null],
  ['resuscitation_particle', '심폐소생술을 종료', null],
  ['clinical_polite', '치료 종료해 주세요', null],
  ['clinical_declarative', '처치 종료합니다', null],
  ['cpr_declarative', 'CPR 종료합니다', null],
  ['cpr_intention', 'CPR 종료 하겠습니다', null],
  ['negative_end', '종료하지 마', null],
  ['negative_record_end', '녹음 종료하지 마', null],
  ['negative_stop', '그만하지 마세요', null],
  ['negative_end_with_instruction', '종료하지 말고 기록하세요', null],
  ['reported_suffix', '환자가 종료라고 말함', null],
  ['reported_bare', '환자가 그만', null],
  ['reported_with_adverb', '환자가 이제 그만', null],
  ['reported_speaking', '환자가 말하기를 그만', null],
  ['reported_statement', '환자 진술 그만', null],
  ['quoted_curly', '환자가 “그만”이라고 말함', null],
  ['quoted_ascii', '환자가 "종료"', null],
  ['open_quote', '환자가 “종료', null],
  ['quoted_corner', '환자가 「종료」라고 말함', null],
  ['clinical_explicit_end', '처치는 종료, 녹음 종료', '처치는 종료,'],
  ['end_followed_by_clinical', '종료 후 혈압 측정합니다.', null],
  ['quoted_command_word', '“녹음 종료”', null],
  ['empty', '', null],
  ['non_string', 123, null],
  ['null', null, null],
];
const rows = cases.map(([id, text, expectedPrefix]) => {
  const result = splitStopCommand(text, {isFinal: true});
  const ok = expectedPrefix === null ? result === null : result?.prefix === expectedPrefix;
  return {id, text, expectedPrefix, result, ok};
});
for (const text of ['종료', '그만', '혈압 120에 80. 종료']) {
  const result = splitStopCommand(text, {isFinal: false});
  rows.push({id: `interim_${text}`, text, expectedPrefix: null, result, ok: result === null});
}
assert.equal(hash(), before, 'helper source changed during audit');
const report = {
  kind: 'independent_synthetic_control_boundary_probe',
  createdAt: new Date().toISOString(),
  source: 'paramedic-ui/recording-control.mjs',
  sourceSha256: before,
  limitations: ['No real audio or STT accuracy measurement.', 'No UI, microphone, external network, DB, or clinical validity checks.'],
  limits: RECORDING_LIMITS,
  total: rows.length,
  passed: rows.filter(row => row.ok).length,
  failed: rows.filter(row => !row.ok).length,
  rows,
};
writeFileSync(new URL('./voice_recording_independent_audit_20261030.json', import.meta.url), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify({total: report.total, passed: report.passed, failed: report.failed, sourceSha256: before, failures: rows.filter(row => !row.ok)}, null, 2));
process.exitCode = report.failed ? 1 : 0;
