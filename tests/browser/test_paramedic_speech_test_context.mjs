import test from 'node:test';
import assert from 'node:assert/strict';
import {noiseTestContext,captureTestContext,captureDescription,localAudioTestMetadata} from '../../paramedic-ui/speech-test-context.mjs';

test('unknown real-world noise measurements stay unknown rather than becoming zero',()=>{
  const result=noiseTestContext({noiseKind:'siren'});
  assert.equal(result.snr_db,null);assert.equal(result.microphone_distance_cm,null);
  assert.equal(result.snr_basis,'unknown');
});
test('zero and negative calibrated SNR are valid, with their provenance',()=>{
  assert.equal(noiseTestContext({snr:'0',snrBasis:'controlled_mix'}).snr_db,0);
  const result=noiseTestContext({snr:'-5',snrBasis:'measured',distance:'12',pairId:'S01-A',noiseAssetId:'siren-v1'});
  assert.equal(result.snr_db,-5);assert.equal(result.microphone_distance_cm,12);assert.equal(result.pair_id,'S01-A');
});
test('guessed or incoherent SNR and invalid identifying inputs are rejected',()=>{
  for(const values of [{snr:'10'},{snrBasis:'measured'},{snr:'NaN',snrBasis:'measured'},
    {snr:'Infinity',snrBasis:'measured'},{snr:'0x10',snrBasis:'measured'},{snr:'1e2',snrBasis:'measured'},
    {distance:'0'},{pairId:'name@example.com'},{noiseKind:'fake'},{snrBasis:'estimated'}])assert.throws(()=>noiseTestContext(values));
});
test('capture metadata distinguishes requested vs applied and never preserves device identity',()=>{
  const source={route:'pcm_capture',profile:'noise_suppressed',requested:{noiseSuppression:true},
    supported:{noiseSuppression:true},applied:{noiseSuppression:false,deviceId:'private',groupId:'private'},
    quality:{clipping_fraction:0,stt_accuracy_measured:false,noise_snr_measured:false,deviceId:'private'}};
  const saved=captureTestContext(source);
  assert.equal(saved.requested.noiseSuppression,true);assert.equal(saved.applied.noiseSuppression,false);
  assert.doesNotMatch(JSON.stringify(saved),/private|deviceId|groupId/);
  source.applied.noiseSuppression=true;assert.equal(saved.applied.noiseSuppression,false);
});
test('browser mode never claims the PCM noise profile was applied',()=>{
  assert.match(captureDescription({route:'browser_managed',requested_profile:'noise_suppressed'}),/확인할 수 없습니다/);
  const text=captureDescription({route:'pcm_capture',profile:'raw',applied:{noiseSuppression:false,echoCancellation:null,autoGainControl:false},quality:{clipping_sample_count:1}});
  assert.match(text,/소음 억제 꺼짐/);assert.match(text,/반향 제거 확인 불가/);assert.match(text,/입력 포화/);
});
test('local WAV test metadata never claims a server upload or verified human reference',()=>{
  const result=localAudioTestMetadata({route:'pcm_capture',profile:'raw',applied:{sampleRate:48000},quality:{sample_rate:48000}});
  assert.equal(result.audio_sent_to_stt,false);assert.equal(result.audio_sent_to_server,false);
  assert.equal(result.reference_verified,false);assert.equal(result.speech_recognition_accuracy_measured,false);
  assert.equal(result.file_format.sample_rate,16000);assert.equal(result.audio_input.applied.sampleRate,48000);
});
