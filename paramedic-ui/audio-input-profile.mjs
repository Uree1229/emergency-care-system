/** Capture preferences and diagnostics, not a denoiser or an ASR accuracy estimate. */
export const INPUT_PROFILES = Object.freeze({
  legacy: Object.freeze({noiseSuppression:true, echoCancellation:true, autoGainControl:true}),
  raw: Object.freeze({noiseSuppression:false, echoCancellation:false, autoGainControl:false}),
  noise_suppressed: Object.freeze({noiseSuppression:true, echoCancellation:true, autoGainControl:false}),
});
const BOOLEAN_KEYS = ['noiseSuppression', 'echoCancellation', 'autoGainControl'];
const SETTING_KEYS = [...BOOLEAN_KEYS, 'channelCount', 'sampleRate'];
export const CLIPPING_THRESHOLD = 0.999;
export const SILENCE_THRESHOLD = 0.01;

export function normalizeAudioProfile(value = 'legacy') {
  if (typeof value !== 'string' || !Object.hasOwn(INPUT_PROFILES, value)) {
    throw new RangeError('지원하지 않는 오디오 입력 프로필입니다.');
  }
  return value;
}

export function requestedAudioSettings(profile = 'legacy') {
  profile = normalizeAudioProfile(profile);
  return {...INPUT_PROFILES[profile], channelCount:1, sampleRate:profile === 'legacy' ? null : 16000};
}

/** All preferences are optional. Unsupported settings must not prevent capture. */
export function audioConstraints(profile = 'legacy') {
  const {channelCount, sampleRate, ...processing} = requestedAudioSettings(profile);
  return {audio:{...processing, channelCount:{ideal:channelCount}, ...(sampleRate ? {sampleRate:{ideal:sampleRate}} : {})}};
}

export function supportedAudioSettings(mediaDevices) {
  let supported;
  try { supported = mediaDevices?.getSupportedConstraints?.(); } catch { /* Report unknown. */ }
  return Object.fromEntries(SETTING_KEYS.map(key => [key,
    typeof supported?.[key] === 'boolean' ? supported[key] : null]));
}

/** Whitelist only non-identifying properties. Never preserve device/group IDs. */
export function appliedAudioSettings(stream) {
  let settings;
  try {
    const track = stream?.getAudioTracks?.()[0] || stream?.getTracks?.()[0];
    settings = track?.getSettings?.();
  } catch { /* Capture remains usable when diagnostics are unavailable. */ }
  return Object.fromEntries(SETTING_KEYS.map(key => {
    const value = settings?.[key];
    const valid = BOOLEAN_KEYS.includes(key) ? typeof value === 'boolean'
      || (key === 'echoCancellation' && ['all','remote-only'].includes(value))
      : typeof value === 'number' && Number.isFinite(value) && value > 0;
    return [key, valid ? value : null];
  }));
}

export function browserInputDiagnostics(profile = 'legacy') {
  return {schema_version:1, route:'browser_managed', profile:'browser_managed',
    requested_profile:normalizeAudioProfile(profile), app_preprocessing_applied:false,
    processing_source:'recognition_service_managed', requested:{}, supported:{}, applied:{},
    quality:null};
}

export function pcmInputDiagnostics(profile = 'legacy', mediaDevices, stream) {
  profile = normalizeAudioProfile(profile);
  return {schema_version:1, route:'pcm_capture', profile, requested_profile:profile,
    app_preprocessing_applied:false, processing_source:'getUserMedia_constraints',
    requested:requestedAudioSettings(profile), supported:supportedAudioSettings(mediaDevices),
    applied:appliedAudioSettings(stream), quality:null};
}

export function newSignalStats() {
  return {sampleCount:0, nonfiniteCount:0, clippingCount:0, silenceCount:0, peak:0, squareSum:0};
}

export function collectSignalStats(stats, samples) {
  for (const value of samples) {
    stats.sampleCount += 1;
    if (!Number.isFinite(value)) { stats.nonfiniteCount += 1; continue; }
    const absolute = Math.abs(value);
    stats.peak = Math.max(stats.peak, absolute);
    stats.squareSum += value * value;
    if (absolute >= CLIPPING_THRESHOLD) stats.clippingCount += 1;
    if (absolute <= SILENCE_THRESHOLD) stats.silenceCount += 1;
  }
}

export function signalQuality(stats, sampleRate) {
  if (!Number.isFinite(sampleRate) || sampleRate <= 0) throw new RangeError('sampleRate must be positive');
  const count = stats.sampleCount;
  const rms = count ? Math.sqrt(stats.squareSum / count) : 0;
  return {measurement:'captured_pcm_before_wav_encoding', sample_count:count,
    sample_rate:sampleRate, duration_seconds:count / sampleRate,
    peak_absolute:stats.peak, rms, rms_dbfs:rms > 0 ? 20 * Math.log10(rms) : null,
    clipping_sample_count:stats.clippingCount, clipping_fraction:count ? stats.clippingCount/count : 0,
    silence_sample_count:stats.silenceCount, silence_fraction:count ? stats.silenceCount/count : 0,
    nonfinite_sample_count:stats.nonfiniteCount,
    clipping_threshold:CLIPPING_THRESHOLD, silence_threshold:SILENCE_THRESHOLD,
    stt_accuracy_measured:false, noise_snr_measured:false};
}
