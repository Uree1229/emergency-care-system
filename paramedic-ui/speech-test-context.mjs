/** Reproducible test conditions. Missing measurements stay unknown. */
const NOISE_KINDS = new Set(['unspecified','none','siren','engine','wind','voices','mixed']);
const SNR_BASES = new Set(['unknown','measured','controlled_mix']);
const SETTINGS = ['noiseSuppression','echoCancellation','autoGainControl','channelCount','sampleRate'];
const QUALITY = ['measurement','sample_count','sample_rate','duration_seconds','peak_absolute','rms','rms_dbfs',
  'clipping_sample_count','clipping_fraction','silence_sample_count','silence_fraction','nonfinite_sample_count',
  'clipping_threshold','silence_threshold','stt_accuracy_measured','noise_snr_measured'];
const pick = (object, keys) => Object.fromEntries(keys.filter(key=>Object.hasOwn(object||{},key)).map(key=>[key,object[key]]));

function optionalNumber(value, label, minimum, maximum) {
  const text=String(value??'').trim();
  if(!text)return null;
  if(!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/u.test(text))throw new Error(`${label}은 정확한 숫자로 입력하거나 비워 두세요.`);
  const number=Number(text);
  if(!Number.isFinite(number)||number<minimum||number>maximum)throw new Error(`${label} 범위를 확인하세요.`);
  return number;
}

function anonymousId(value, label) {
  const text=String(value??'').trim();
  if(!/^[A-Za-z0-9._-]{0,80}$/u.test(text))throw new Error(`${label}는 익명 영문·숫자 ID로 입력하세요.`);
  return text||null;
}

export function noiseTestContext({noiseKind='unspecified',snr='',snrBasis='unknown',distance='',pairId='',noiseAssetId=''}={}) {
  if(!NOISE_KINDS.has(noiseKind)||!SNR_BASES.has(snrBasis))throw new Error('시험 소음 조건을 확인하세요.');
  const snrDb=optionalNumber(snr,'SNR(dB)',-40,80);
  if(snrDb!==null&&snrBasis==='unknown')throw new Error('SNR은 실측 또는 제어 혼합으로 확인한 경우에만 입력하세요.');
  if(snrDb===null&&snrBasis!=='unknown')throw new Error('SNR 값이 없으면 출처를 미측정으로 선택하세요.');
  return {schema_version:1,noise_kind:noiseKind,noise_asset_id:anonymousId(noiseAssetId,'소음 음원 ID'),
    snr_db:snrDb,snr_basis:snrBasis,microphone_distance_cm:optionalNumber(distance,'마이크 거리(cm)',1,1000),
    pair_id:anonymousId(pairId,'짝 비교 ID'),environment_source:'operator_reported',
    note:'SNR is not ambient sound level (dBA). Unknown measurements remain null.'};
}

export function captureTestContext(diagnostics) {
  if(!diagnostics)return null;
  const result={schema_version:1,route:diagnostics.route,profile:diagnostics.profile,
    requested_profile:diagnostics.requested_profile,app_preprocessing_applied:diagnostics.app_preprocessing_applied,
    processing_source:diagnostics.processing_source,requested:pick(diagnostics.requested,SETTINGS),
    supported:pick(diagnostics.supported,SETTINGS),applied:pick(diagnostics.applied,SETTINGS),
    quality:diagnostics.quality?pick(diagnostics.quality,QUALITY):null};
  return JSON.parse(JSON.stringify(result));
}

export function captureDescription(diagnostics) {
  if(!diagnostics||diagnostics.route==='browser_managed')return '브라우저가 마이크 입력을 관리합니다. 앱 전처리·소음 억제 적용 여부는 확인할 수 없습니다.';
  const value=key=>diagnostics.applied?.[key]===true?'켜짐':diagnostics.applied?.[key]===false?'꺼짐':diagnostics.applied?.[key]??'확인 불가';
  let text=`PCM 입력 ${diagnostics.profile} · 기기가 보고한 소음 억제 ${value('noiseSuppression')} · 반향 제거 ${value('echoCancellation')} · 자동 음량 ${value('autoGainControl')}`;
  if(diagnostics.quality?.clipping_sample_count>0)text+=' · 입력 포화가 관찰됐습니다. 마이크 위치·입력 음량을 확인하세요.';
  return text+' 이 표시는 음성 인식 정확도 개선을 뜻하지 않습니다.';
}

export function localAudioTestMetadata(diagnostics) {
  return {schema_version:1,source:'unverified_microphone_audio',audio_input:captureTestContext(diagnostics),
    file_format:{container:'wav',codec:'PCM',sample_rate:16000,channels:1,bits_per_sample:16},
    audio_sent_to_stt:false,audio_sent_to_server:false,reference_verified:false,
    speech_recognition_accuracy_measured:false,
    note:'Local recording only. A reference transcript and controlled noise conditions are required for speech accuracy evaluation.'};
}
