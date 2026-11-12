import test from 'node:test';
import assert from 'node:assert/strict';
import {INPUT_PROFILES, normalizeAudioProfile, audioConstraints, pcmInputDiagnostics,
  browserInputDiagnostics, newSignalStats, collectSignalStats, signalQuality,
} from '../../paramedic-ui/audio-input-profile.mjs';

test('profiles preserve legacy processing flags and make raw/NS comparisons explicit', () => {
  assert.deepEqual(INPUT_PROFILES.legacy, {noiseSuppression:true, echoCancellation:true, autoGainControl:true});
  assert.deepEqual(audioConstraints('legacy'), {audio:{noiseSuppression:true, echoCancellation:true, autoGainControl:true, channelCount:{ideal:1}}});
  for (const profile of ['raw','noise_suppressed']) {
    const constraints = audioConstraints(profile).audio;
    assert.deepEqual(constraints.channelCount, {ideal:1});
    assert.deepEqual(constraints.sampleRate, {ideal:16000});
    assert.equal(constraints.autoGainControl, false);
    assert.equal(constraints.noiseSuppression, profile === 'noise_suppressed');
    assert.equal(constraints.echoCancellation, profile === 'noise_suppressed');
    assert.equal(JSON.stringify(constraints).includes('exact'), false);
  }
});

test('invalid profiles cannot silently select another capture policy', () => {
  for (const value of ['noise','toString','__proto__',true,{},null]) assert.throws(() => normalizeAudioProfile(value), RangeError);
});

test('requested, supported, and applied settings differ without leaking device identifiers', () => {
  const diagnostics = pcmInputDiagnostics('noise_suppressed', {
    getSupportedConstraints:() => ({noiseSuppression:true, echoCancellation:true, autoGainControl:true, channelCount:true, sampleRate:true}),
  }, {getAudioTracks:() => [{getSettings:() => ({noiseSuppression:false, echoCancellation:'remote-only', autoGainControl:false,
    sampleRate:48000, channelCount:2, deviceId:'private-device', groupId:'private-group', label:'private-microphone'})}]});
  assert.equal(diagnostics.requested.noiseSuppression, true);
  assert.equal(diagnostics.supported.noiseSuppression, true);
  assert.equal(diagnostics.applied.noiseSuppression, false);
  assert.equal(diagnostics.applied.echoCancellation, 'remote-only');
  assert.equal(diagnostics.requested.channelCount, 1);
  assert.equal(diagnostics.applied.channelCount, 2);
  assert.equal(diagnostics.applied.sampleRate, 48000);
  assert.equal(diagnostics.app_preprocessing_applied, false);
  assert.doesNotMatch(JSON.stringify(diagnostics), /private-|deviceId|groupId|microphone/);
});

test('missing or throwing settings and supported-constraint APIs are unknown, not applied', () => {
  for (const [devices, stream] of [
    [undefined, undefined],
    [{getSupportedConstraints(){throw Error('unsupported');}}, {getAudioTracks:() => [{getSettings(){throw Error('unsupported');}}]}],
  ]) {
    const d = pcmInputDiagnostics('raw', devices, stream);
    assert.ok(Object.values(d.supported).every(v => v === null));
    assert.ok(Object.values(d.applied).every(v => v === null));
  }
  const d = pcmInputDiagnostics('raw', {getSupportedConstraints:() => ({noiseSuppression:false})},
    {getTracks:() => [{getSettings:() => ({noiseSuppression:'true', sampleRate:NaN, channelCount:0})}]});
  assert.equal(d.supported.noiseSuppression, false);
  assert.equal(d.applied.noiseSuppression, null);
  assert.equal(d.applied.sampleRate, null);
});

test('browser recognition never claims an app-controlled audio pipeline', () => {
  const d = browserInputDiagnostics('noise_suppressed');
  assert.equal(d.route, 'browser_managed');
  assert.equal(d.profile, 'browser_managed');
  assert.equal(d.requested_profile, 'noise_suppressed');
  assert.equal(d.app_preprocessing_applied, false);
  assert.deepEqual(d.applied, {});
  assert.equal(d.quality, null);
});

test('signal diagnostics measure clipping, silence and invalid samples without an SNR/STT claim', () => {
  const stats = newSignalStats();
  collectSignalStats(stats, [-1,0,0.5]);collectSignalStats(stats, [1.2,NaN,Infinity]);
  const q = signalQuality(stats, 16000);
  assert.equal(q.sample_count, 6);
  assert.equal(q.nonfinite_sample_count, 2);
  assert.equal(q.clipping_sample_count, 2);
  assert.equal(q.silence_sample_count, 1);
  assert.equal(q.peak_absolute, 1.2);
  assert.equal(q.clipping_fraction, 1/3);
  assert.ok(Math.abs(q.rms-Math.sqrt((1+0.25+1.44)/6))<1e-12);
  assert.equal(q.noise_snr_measured, false);
  assert.equal(q.stt_accuracy_measured, false);
  assert.doesNotMatch(JSON.stringify(q), /NaN|Infinity/);
});

test('empty and quiet signal diagnostics serialize without negative infinity', () => {
  const empty = signalQuality(newSignalStats(), 48000);
  assert.equal(empty.rms_dbfs, null);assert.equal(empty.clipping_fraction, 0);
  const stats = newSignalStats();collectSignalStats(stats, new Float32Array(480).fill(0));
  const q = signalQuality(stats, 48000);
  assert.equal(q.silence_fraction, 1);assert.equal(q.duration_seconds, 0.01);
  for (const rate of [0,-1,NaN,Infinity]) assert.throws(() => signalQuality(stats, rate), RangeError);
});
