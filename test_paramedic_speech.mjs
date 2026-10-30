import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import {
  VoiceCapture, encodeWav, resampleMono, bytesToBase64,
  OUTPUT_SAMPLE_RATE, MAX_WAV_BYTES,
} from './paramedic-ui/speech.mjs';

const originalEnvironments = new WeakMap();
function installEnvironment(t, env) {
  if (!originalEnvironments.has(t)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, 'window');
    originalEnvironments.set(t, previous);
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, 'window', previous);
      else delete globalThis.window;
    });
  }
  Object.defineProperty(globalThis, 'window', { value: env, configurable: true, writable: true });
}

function recognitionMock({ automaticStart = true } = {}) {
  const instances = [];
  class Recognition {
    constructor() { instances.push(this); this.aborts = 0; this.stops = 0; }
    start() { if (automaticStart) queueMicrotask(() => this.onstart?.()); }
    stop() { this.stops += 1; }
    abort() { this.aborts += 1; }
  }
  return { Recognition, instances };
}

function result(text, isFinal) {
  const item = [{ transcript: text }];
  item.isFinal = isFinal;
  return item;
}

function audioMock({ rate = 48_000, worklet = false, failWorklet = false, failResume = false, delayedPermission = false, supportedConstraints, trackSettings } = {}) {
  const tracks = [{ stopped: 0, stop() { this.stopped += 1; } }];
  if (trackSettings !== undefined) tracks[0].getSettings = () => trackSettings;
  const stream = { getTracks: () => tracks };
  const requests = [];
  const contexts = [];
  const processors = [];
  let allow;
  const pending = new Promise((resolve) => { allow = resolve; });
  const node = () => ({ connections: [], disconnected: 0, connect(other) { this.connections.push(other); }, disconnect() { this.disconnected += 1; } });
  class Context {
    constructor() {
      this.sampleRate = rate;
      this.state = 'running';
      this.closed = 0;
      this.destination = {};
      contexts.push(this);
      if (worklet) this.audioWorklet = { addModule: async (url) => {
        this.moduleUrl = url;
        if (failWorklet) throw new Error('worklet rejected');
      } };
    }
    createMediaStreamSource() { this.source = node(); return this.source; }
    createGain() { this.gainNode = { ...node(), gain: { value: 1 } }; return this.gainNode; }
    createScriptProcessor() {
      const processor = node();
      processors.push(processor);
      return processor;
    }
    async resume() { if (failResume) throw new Error('resume failed'); }
    async close() { this.closed += 1; this.state = 'closed'; }
  }
  class WorkletNode {
    constructor(context, name) {
      Object.assign(this, node());
      this.name = name;
      this.port = { closed: 0, close() { this.closed += 1; } };
      processors.push(this);
    }
  }
  const env = {
    navigator: { mediaDevices: {
      getUserMedia: async constraints => { requests.push(constraints); return delayedPermission ? pending : stream; },
      ...(supportedConstraints !== undefined ? {getSupportedConstraints:() => supportedConstraints} : {}),
    } },
    AudioContext: Context,
    ...(worklet ? { AudioWorkletNode: WorkletNode } : {}),
  };
  const emit = (samples) => {
    const processor = processors.at(-1);
    if (processor.port) processor.port.onmessage?.({ data: samples });
    else processor.onaudioprocess?.({ inputBuffer: {
      numberOfChannels: 1, length: samples.length, getChannelData: () => samples,
    } });
  };
  return { env, tracks, contexts, processors, requests, emit, allow: () => allow(stream) };
}

test('WAV header, 16-bit clipping, signed little-endian PCM, and base64 are valid', () => {
  const samples = new Float32Array([-2, -1, 0, 0.5, 1, 2, Number.NaN]);
  const wav = encodeWav(samples, 16_000);
  const view = new DataView(wav.buffer);
  assert.equal(Buffer.from(wav.subarray(0, 4)).toString(), 'RIFF');
  assert.equal(Buffer.from(wav.subarray(8, 16)).toString(), 'WAVEfmt ');
  assert.equal(view.getUint32(4, true), wav.length - 8);
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 16_000);
  assert.equal(view.getUint32(28, true), 32_000);
  assert.equal(view.getUint16(32, true), 2);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(Buffer.from(wav.subarray(36, 40)).toString(), 'data');
  assert.equal(view.getUint32(40, true), samples.length * 2);
  assert.deepEqual(Array.from({ length: samples.length }, (_, i) => view.getInt16(44 + i * 2, true)),
    [-32768, -32768, 0, 16384, 32767, 32767, 0]);
  assert.equal(wav[44], 0x00);
  assert.equal(wav[45], 0x80);
  assert.deepEqual(Buffer.from(bytesToBase64(wav), 'base64'), Buffer.from(wav));
});

test('44.1 and 48 kHz input produces exactly 16 kHz duration with averaged samples', () => {
  for (const rate of [44_100, 48_000]) {
    const input = new Float32Array(rate).fill(0.25);
    const output = resampleMono(input, rate);
    assert.equal(output.length, OUTPUT_SAMPLE_RATE);
    assert.ok(output.every((value) => Math.abs(value - 0.25) < 1e-6));
    const wav = encodeWav(input, rate);
    assert.equal(wav.length, 44 + OUTPUT_SAMPLE_RATE * 2);
  }
  assert.deepEqual([...resampleMono(new Float32Array([0, 1, 2, 3, 4, 5]), 48_000)], [1, 4]);
  assert.deepEqual([...resampleMono(new Float32Array([0, 1]), 8000)], [0, 0.5, 1, 1]);
});

test('WAV recording is capped at sixty seconds and invalid sample rates fail clearly', () => {
  const wav = encodeWav(new Float32Array(48_000 * 61), 48_000);
  assert.equal(wav.length, MAX_WAV_BYTES);
  assert.equal(new DataView(wav.buffer).getUint32(40, true), 16_000 * 60 * 2);
  for (const rate of [0, -1, Infinity, Number.NaN]) {
    assert.throws(() => encodeWav(new Float32Array(10), rate), RangeError);
  }
  assert.throws(() => encodeWav([0], 16_000), TypeError);
  assert.equal(encodeWav(new Float32Array(), 48_000).length, 44);
});

test('browser results are final once per index and stop drains final callbacks before onend', async (t) => {
  const mock = recognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition });
  const finals = [], interims = [], states = [], errors = [];
  const capture = new VoiceCapture({ onFinal: (text) => finals.push(text), onInterim: (text) => interims.push(text), onState: (state) => states.push(state), onError: (error) => errors.push(error) });
  assert.deepEqual(VoiceCapture.capabilities(), { browser: true, recording: false, tts: false });
  await capture.start();
  const recognition = mock.instances[0];
  assert.equal(recognition.lang, 'ko-KR');
  assert.equal(recognition.interimResults, true);
  assert.equal(recognition.continuous, true);
  recognition.onresult({ resultIndex: 0, results: [result('호흡 곤란', false)] });
  assert.equal(interims.at(-1), '호흡 곤란');
  recognition.onresult({ resultIndex: 0, results: [result('호흡 곤란', true)] });
  recognition.onresult({ resultIndex: 0, results: [result('호흡 곤란', true)] });
  const stopping = capture.stop();
  assert.equal(capture.active, true);
  recognition.onresult({ resultIndex: 1, results: [result('호흡 곤란', true), result('산소 포화도 90', true)] });
  recognition.onend();
  assert.equal(await stopping, null);
  assert.deepEqual(finals, ['호흡 곤란', '산소 포화도 90']);
  assert.deepEqual(states, ['listening', 'idle']);
  assert.equal(capture.active, false);
  assert.equal(recognition.stops, 1);
  assert.equal(mock.instances.length, 1);
  assert.deepEqual(errors, []);
});

test('abort suppresses saved late callbacks and does not affect the next recognition session', async (t) => {
  const mock = recognitionMock();
  installEnvironment(t, { webkitSpeechRecognition: mock.Recognition });
  const finals = [], errors = [];
  const capture = new VoiceCapture({ onFinal: (text) => finals.push(text), onError: (error) => errors.push(error) });
  await capture.start();
  const old = mock.instances[0];
  const lateResult = old.onresult;
  const lateEnd = old.onend;
  const lateError = old.onerror;
  capture.abort();
  assert.equal(old.aborts, 1);
  await capture.start();
  lateResult({ results: [result('취소한 문장', true)] });
  lateEnd();
  lateError({ error: 'network' });
  assert.equal(capture.active, true);
  const current = mock.instances[1];
  current.onresult({ results: [result('현재 문장', true)] });
  assert.deepEqual(finals, ['현재 문장']);
  assert.deepEqual(errors, []);
  capture.abort();
});

test('browser permission, network, and no-speech errors surface and never restart', async (t) => {
  for (const code of ['not-allowed', 'network', 'no-speech']) {
    const mock = recognitionMock();
    installEnvironment(t, { SpeechRecognition: mock.Recognition });
    const errors = [], states = [];
    const capture = new VoiceCapture({ onError: (error) => errors.push(error), onState: (state) => states.push(state) });
    await capture.start();
    const recognition = mock.instances[0];
    const lateEnd = recognition.onend;
    recognition.onerror({ error: code });
    lateEnd();
    assert.equal(capture.active, false);
    assert.equal(errors.length, 1);
    assert.equal(errors[0].code, code);
    assert.ok(errors[0].message.length > 10);
    assert.deepEqual(states, ['listening', 'idle']);
    assert.equal(recognition.aborts, 1);
    assert.equal(mock.instances.length, 1);
    assert.equal(await capture.stop(), null);
  }
});

test('cancel while browser start is pending rejects start and suppresses late onstart', async (t) => {
  const mock = recognitionMock({ automaticStart: false });
  installEnvironment(t, { SpeechRecognition: mock.Recognition });
  const states = [], errors = [];
  const capture = new VoiceCapture({ onState: (state) => states.push(state), onError: (error) => errors.push(error) });
  const starting = capture.start();
  const rejected = assert.rejects(starting, { code: 'aborted', name: 'AbortError' });
  const lateStart = mock.instances[0].onstart;
  assert.equal(capture.active, true);
  assert.equal(await capture.stop(), null);
  await rejected;
  lateStart();
  assert.equal(capture.active, false);
  assert.deepEqual(states, ['idle']);
  assert.deepEqual(errors, []);
});

test('ScriptProcessor recording returns real WAV and releases microphone, graph, and context', async (t) => {
  const mock = audioMock();
  installEnvironment(t, mock.env);
  const states = [], finals = [], errors = [];
  const capture = new VoiceCapture({ onState: (state) => states.push(state), onFinal: (text) => finals.push(text), onError: (error) => errors.push(error) });
  assert.equal(VoiceCapture.capabilities().recording, true);
  await capture.start('azure');
  const processor = mock.processors[0];
  const lateAudio = processor.onaudioprocess;
  mock.emit(new Float32Array(48_000).fill(0.25));
  const recording = await capture.stop();
  assert.equal(recording.content_type, 'audio/wav');
  const wav = Buffer.from(recording.audio_base64, 'base64');
  assert.equal(wav.length, 32_044);
  assert.equal(wav.readUInt32LE(24), 16_000);
  assert.equal(wav.readInt16LE(44), 8192);
  assert.equal(capture.active, false);
  assert.equal(mock.tracks[0].stopped, 1);
  assert.equal(mock.contexts[0].closed, 1);
  assert.equal(mock.contexts[0].source.disconnected, 1);
  assert.equal(mock.contexts[0].gainNode.disconnected, 1);
  assert.equal(mock.contexts[0].gainNode.gain.value, 0);
  assert.equal(processor.disconnected, 1);
  lateAudio({ inputBuffer: { numberOfChannels: 1, length: 10, getChannelData: () => new Float32Array(10) } });
  assert.deepEqual(states, ['recording', 'idle']);
  assert.deepEqual(finals, []);
  assert.deepEqual(errors, []);
});

test('AudioWorklet records at actual 44.1 kHz and safely falls back if its module fails', async (t) => {
  for (const failWorklet of [false, true]) {
    const mock = audioMock({ rate: 44_100, worklet: true, failWorklet });
    installEnvironment(t, mock.env);
    const capture = new VoiceCapture();
    await capture.start('azure');
    const context = mock.contexts[0];
    assert.match(context.moduleUrl, /pcm-worklet\.js$/);
    assert.equal(Boolean(mock.processors[0].port), !failWorklet);
    mock.emit(new Float32Array(44_100).fill(-0.5));
    const wav = Buffer.from((await capture.stop()).audio_base64, 'base64');
    assert.equal(wav.length, 32_044);
    assert.equal(wav.readInt16LE(44), -16384);
    assert.equal(mock.tracks[0].stopped, 1);
    assert.equal(context.closed, 1);
    if (!failWorklet) assert.equal(mock.processors[0].port.closed, 1);
  }
});

test('sample limit stops hardware immediately, caps bytes, and retains WAV for stop()', async (t) => {
  const mock = audioMock({ rate: 16_000, worklet: true });
  installEnvironment(t, mock.env);
  const states = [];
  const capture = new VoiceCapture({ onState: (state) => states.push(state) });
  await capture.start('azure');
  const lateAudio = mock.processors[0].port.onmessage;
  mock.emit(new Float32Array(16_000 * 61).fill(0.1));
  assert.deepEqual(states, ['recording', 'limit']);
  assert.equal(mock.tracks[0].stopped, 1);
  assert.equal(mock.contexts[0].closed, 1);
  lateAudio({ data: new Float32Array(100) });
  const wav = Buffer.from((await capture.stop()).audio_base64, 'base64');
  assert.equal(wav.length, MAX_WAV_BYTES);
  assert.deepEqual(states, ['recording', 'limit', 'idle']);
  assert.equal(mock.tracks[0].stopped, 1);
});

test('aborted permission requests reject immediately and release a stream that arrives later', { timeout: 1000 }, async (t) => {
  const mock = audioMock({ delayedPermission: true });
  installEnvironment(t, mock.env);
  const errors = [];
  const capture = new VoiceCapture({ onError: (error) => errors.push(error) });
  const starting = capture.start('azure');
  const rejected = assert.rejects(starting, { code: 'aborted' });
  capture.abort();
  await rejected;
  assert.equal(mock.tracks[0].stopped, 0);
  mock.allow();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(capture.active, false);
  assert.equal(mock.tracks[0].stopped, 1);
  assert.equal(mock.contexts.length, 0);
  assert.deepEqual(errors, []);
});

test('recording setup errors and zero-audio stop release all acquired resources', async (t) => {
  const mock = audioMock({ failResume: true });
  installEnvironment(t, mock.env);
  const errors = [];
  const capture = new VoiceCapture({ onError: (error) => errors.push(error) });
  await assert.rejects(capture.start('azure'), { code: 'recording-failed' });
  assert.equal(capture.active, false);
  assert.equal(mock.tracks[0].stopped, 1);
  assert.equal(mock.contexts[0].closed, 1);
  assert.equal(errors[0].code, 'recording-failed');

  const empty = audioMock();
  installEnvironment(t, empty.env);
  await capture.start('azure');
  assert.equal(await capture.stop(), null);
  assert.equal(errors.at(-1).code, 'no-audio');
  assert.equal(empty.tracks[0].stopped, 1);
  assert.equal(empty.contexts[0].closed, 1);
});

test('microphone permission errors are visible and unsupported browsers fail explicitly', async (t) => {
  installEnvironment(t, {
    navigator: { mediaDevices: { getUserMedia: async () => { const error = new Error('denied'); error.name = 'NotAllowedError'; throw error; } } },
    AudioContext: class {},
  });
  const errors = [];
  const capture = new VoiceCapture({ onError: (error) => errors.push(error) });
  await assert.rejects(capture.start('azure'));
  assert.equal(errors[0].code, 'not-allowed');
  assert.equal(capture.active, false);
  installEnvironment(t, {});
  await assert.rejects(capture.start(), { code: 'recognition-unavailable' });
  await assert.rejects(capture.start('azure'), { code: 'recording-unavailable' });
  assert.equal(capture.active, false);
});

test('TTS uses Korean, cancels before microphone capture, and refuses capture feedback', async (t) => {
  const mock = recognitionMock();
  const spoken = [];
  let cancelled = 0;
  installEnvironment(t, {
    SpeechRecognition: mock.Recognition,
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; } },
    speechSynthesis: { cancel() { cancelled += 1; }, speak(utterance) { spoken.push(utterance); } },
  });
  const capture = new VoiceCapture();
  capture.speak('환자 상태를 확인하세요');
  assert.equal(spoken[0].lang, 'ko-KR');
  assert.equal(spoken[0].text, '환자 상태를 확인하세요');
  await capture.start();
  assert.equal(cancelled, 2);
  assert.throws(() => capture.speak('입력 중 안내'), { code: 'capture-active' });
  capture.abort();
  capture.speak('안내 재개');
  assert.equal(spoken.length, 2);
});

test('worklet averages channels, transfers its PCM, and stays silent', async () => {
  let Processor;
  const messages = [];
  const context = vm.createContext({
    AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (data, transfers) => messages.push({ data, transfers }) }; } },
    Float32Array,
    registerProcessor(name, implementation) { assert.equal(name, 'paramedic-pcm-capture'); Processor = implementation; },
  });
  vm.runInContext(await readFile(new URL('./paramedic-ui/pcm-worklet.js', import.meta.url), 'utf8'), context);
  const processor = new Processor();
  const output = new Float32Array(2);
  assert.equal(processor.process([[new Float32Array([1, -1]), new Float32Array([0, 1])]], [[output]]), true);
  assert.deepEqual([...messages[0].data], [0.5, 0]);
  assert.equal(messages[0].transfers[0], messages[0].data.buffer);
  assert.deepEqual([...output], [0, 0]);
  assert.equal(processor.process([]), true);
});

// Contextual biasing (Web Speech `phrases`) with FAKE recognizers: lifecycle only, not accuracy.
class Phrase {
  constructor(phrase, boost = 1) {
    if (boost < 0 || boost > 10) throw new SyntaxError('boost out of range');
    this.phrase = phrase; this.boost = boost;
  }
}
function phraseRecognitionMock() {
  const mock = recognitionMock({ automaticStart: false });
  const Base = mock.Recognition;
  class Recognition extends Base { constructor() { super(); this.phrases = []; } }
  return { Recognition, instances: mock.instances };
}

test('phrases are skipped when the browser has no SpeechRecognitionPhrase', async (t) => {
  const mock = recognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition });
  const capture = new VoiceCapture({ phrases: ['혈압', '맥박'] });
  assert.equal(VoiceCapture.phraseSupport(), false);
  assert.equal(capture.phraseStatus, 'unsupported');
  await capture.start();
  assert.equal(Object.hasOwn(mock.instances[0], 'phrases'), false);
  assert.equal(capture.phraseStatus, 'unsupported');
  capture.abort();
});

test('phrases use the spec default boost, are deduplicated and are only reported after a clean start', async (t) => {
  const mock = phraseRecognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition, SpeechRecognitionPhrase: Phrase });
  const capture = new VoiceCapture();
  assert.deepEqual(capture.setPhrases(['혈압', ' 혈압 ', '', 7, '맥박']), ['혈압', '맥박']);
  assert.equal(capture.phraseStatus, 'pending');
  const starting = capture.start();
  const recognition = mock.instances[0];
  assert.deepEqual(recognition.phrases.map((p) => [p.phrase, p.boost]), [['혈압', 1], ['맥박', 1]]);
  assert.equal(capture.phraseStatus, 'pending');
  recognition.onstart();
  await starting;
  assert.equal(capture.phraseStatus, 'applied');
  assert.deepEqual(capture.setPhrases(Array.from({ length: 80 }, (_, i) => `용어${i}`)).length, 50);
  capture.abort();
});

test('phrases-not-supported restarts once with plain recognition, keeps one session, and later starts skip phrases', async (t) => {
  const mock = phraseRecognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition, SpeechRecognitionPhrase: Phrase });
  const finals = [], states = [], errors = [];
  const capture = new VoiceCapture({ phrases: ['혈압'], onFinal: (text) => finals.push(text), onState: (state) => states.push(state), onError: (error) => errors.push(error) });
  const starting = capture.start();
  const first = mock.instances[0];
  const lateFirstResult = first.onresult;
  first.onerror({ error: 'phrases-not-supported' });
  assert.equal(first.aborts, 1);
  assert.equal(mock.instances.length, 2);
  const second = mock.instances[1];
  assert.deepEqual(second.phrases, []);
  second.onstart();
  await starting;
  lateFirstResult?.({ results: [result('이전 인식기', true)] });
  second.onresult({ resultIndex: 0, results: [result('혈압 120에 80', true)] });
  const stopping = capture.stop();
  second.onend();
  await stopping;
  assert.deepEqual(finals, ['혈압 120에 80']);
  assert.deepEqual(states, ['listening', 'idle']);
  assert.deepEqual(errors, []);
  assert.equal(capture.phraseStatus, 'rejected');
  const next = capture.start();
  assert.deepEqual(mock.instances[2].phrases, []);
  mock.instances[2].onstart();
  await next;
  assert.equal(capture.setPhrases(['맥박']).length, 1);
  assert.equal(capture.phraseStatus, 'rejected');
  capture.abort();
});

test('with phrases attached, other recognition errors still fail once and never restart', async (t) => {
  const mock = phraseRecognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition, SpeechRecognitionPhrase: Phrase });
  const errors = [];
  const capture = new VoiceCapture({ phrases: ['혈압'], onError: (error) => errors.push(error) });
  const starting = capture.start();
  const rejected = assert.rejects(starting, { code: 'network' });
  mock.instances[0].onerror({ error: 'network' });
  await rejected;
  assert.equal(mock.instances.length, 1);
  assert.equal(errors.length, 1);
  assert.equal(capture.active, false);
  assert.equal(capture.phraseStatus, 'pending');
});

test('alternatives: the start option sets maxAlternatives and final callbacks receive every hypothesis', async (t) => {
  const mock = recognitionMock();
  installEnvironment(t, { SpeechRecognition: mock.Recognition });
  const finals = [];
  const capture = new VoiceCapture({ onFinal: (text, alternatives) => finals.push([text, alternatives]) });
  await capture.start();
  assert.equal(mock.instances[0].maxAlternatives, 1);
  mock.instances[0].onresult({ resultIndex: 0, results: [result('47세 남성', true)] });
  capture.abort();
  await capture.start('browser', { alternatives: 5 });
  const recognition = mock.instances[1];
  assert.equal(recognition.maxAlternatives, 5);
  const item = [{ transcript: ' 분당 입회 ' }, { transcript: '분당 20회' }, { transcript: '분당 입회' }, { transcript: '' }];
  item.isFinal = true;
  recognition.onresult({ resultIndex: 0, results: [item] });
  assert.deepEqual(finals, [['47세 남성', ['47세 남성']], ['분당 입회', ['분당 입회', '분당 20회']]]);
  capture.abort();
  for (const [asked, used] of [[0, 1], [-3, 1], ['x', 1], [2.7, 2], [99, 10]]) {
    await capture.start('browser', { alternatives: asked });
    assert.equal(mock.instances.at(-1).maxAlternatives, used, String(asked));
    capture.abort();
  }
});

test('PCM profiles request separate A/B settings and expose actual returned track settings', async (t) => {
  for (const profile of ['legacy','raw','noise_suppressed']) {
    const mock = audioMock({supportedConstraints:{noiseSuppression:true, echoCancellation:true, autoGainControl:true},
      trackSettings:{noiseSuppression:false, echoCancellation:true, autoGainControl:false, sampleRate:48000, channelCount:1,
        deviceId:'must-not-be-reported', groupId:'must-not-be-reported'}});
    installEnvironment(t, mock.env);
    const capture = new VoiceCapture();
    await capture.start('azure', {audioProfile:profile});
    const requested = mock.requests[0].audio;
    assert.equal(requested.noiseSuppression, profile !== 'raw');
    assert.equal(requested.autoGainControl, profile === 'legacy');
    const diagnostics = capture.inputDiagnostics;
    assert.equal(diagnostics.route, 'pcm_capture');assert.equal(diagnostics.profile, profile);
    assert.equal(diagnostics.applied.noiseSuppression, false, 'a request is not an application guarantee');
    assert.equal(diagnostics.requested.noiseSuppression, profile !== 'raw');
    assert.equal(diagnostics.applied.sampleRate, 48000);
    assert.doesNotMatch(JSON.stringify(diagnostics), /must-not-be-reported|deviceId|groupId/);
    capture.abort();
  }
});

test('PCM diagnostics survive stop without changing the strict audio API payload', async (t) => {
  const mock = audioMock();installEnvironment(t, mock.env);
  const capture = new VoiceCapture();
  assert.equal(capture.inputDiagnostics, null);
  await capture.start('azure', {audioProfile:'noise_suppressed'});
  mock.emit(new Float32Array([-1,0,0.5,1.2,NaN]));
  assert.equal(capture.inputDiagnostics.quality.sample_count, 5);
  const audio = await capture.stop();
  assert.deepEqual(Object.keys(audio).sort(), ['audio_base64','content_type']);
  assert.equal('metadata' in audio, false);
  const d = capture.inputDiagnostics;
  assert.equal(d.quality.sample_count, 5);assert.equal(d.quality.clipping_sample_count, 2);
  assert.equal(d.quality.silence_sample_count, 1);assert.equal(d.quality.nonfinite_sample_count, 1);
  assert.equal(d.quality.noise_snr_measured, false);
  d.applied.noiseSuppression = 'changed';d.quality.sample_count = 99;
  assert.equal(capture.inputDiagnostics.applied.noiseSuppression, null);
  assert.equal(capture.inputDiagnostics.quality.sample_count, 5);
});

test('browser mode reports app preprocessing absent and never opens a second capture stream', async (t) => {
  const mock = recognitionMock();let micRequests=0;
  installEnvironment(t, {SpeechRecognition:mock.Recognition,
    navigator:{mediaDevices:{getUserMedia(){micRequests++;throw Error('not allowed');}}}});
  const capture = new VoiceCapture();
  await capture.start('browser', {audioProfile:'noise_suppressed'});
  assert.equal(capture.inputDiagnostics.route, 'browser_managed');
  assert.equal(capture.inputDiagnostics.app_preprocessing_applied, false);
  assert.deepEqual(capture.inputDiagnostics.applied, {});
  assert.equal(micRequests, 0);
  capture.abort();
  assert.equal(capture.inputDiagnostics.route, 'browser_managed');
});

test('invalid audio profiles fail before permission or recognition starts', async (t) => {
  const mock = audioMock();installEnvironment(t, mock.env);
  const capture = new VoiceCapture();
  await assert.rejects(capture.start('azure', {audioProfile:'unknown'}), {code:'unsupported-audio-profile'});
  assert.equal(mock.requests.length, 0);assert.equal(capture.active, false);
  assert.equal(capture.inputDiagnostics, null);
});

test('PCM diagnostic sample counts follow the recording cap and ignore late chunks', async (t) => {
  const mock = audioMock({rate:16000, worklet:true});installEnvironment(t, mock.env);
  const capture = new VoiceCapture();
  await capture.start('azure');
  const late = mock.processors[0].port.onmessage;
  mock.emit(new Float32Array(16000*61).fill(0.2));
  late({data:new Float32Array(16).fill(1)});
  await capture.stop();
  assert.equal(capture.inputDiagnostics.quality.sample_count, 16000*60);
  assert.equal(capture.inputDiagnostics.quality.duration_seconds, 60);
  assert.equal(capture.inputDiagnostics.quality.clipping_sample_count, 0);
});
