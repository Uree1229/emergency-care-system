/** Browser recognition and bounded, local microphone recording. No credentials leave this module. */
export const OUTPUT_SAMPLE_RATE = 16_000;
export const MAX_RECORDING_SECONDS = 60;
export const MAX_WAV_BYTES = 44 + OUTPUT_SAMPLE_RATE * MAX_RECORDING_SECONDS * 2;
// Web Speech contextual biasing (SpeechRecognitionPhrase). Spec default boost; stronger
// biasing could turn unclear speech into a listed term instead of leaving it unclear.
export const PHRASE_BOOST = 1.0;
export const MAX_PHRASES = 50;

const ERROR_MESSAGES = {
  'not-allowed': '마이크 사용 권한이 거부되었습니다. 브라우저의 마이크 권한을 확인하세요.',
  'service-not-allowed': '이 브라우저에서 음성 인식 서비스 사용이 허용되지 않았습니다.',
  'audio-capture': '마이크를 사용할 수 없습니다. 연결 상태와 다른 앱의 사용 여부를 확인하세요.',
  'no-speech': '음성이 감지되지 않았습니다. 마이크를 확인하고 다시 시작하세요.',
  network: '브라우저 음성 인식 서비스에 연결하지 못했습니다. 네트워크 상태를 확인하세요.',
  aborted: '음성 입력이 취소되었습니다.',
  'phrases-not-supported': '이 브라우저는 음성 인식 문맥 힌트를 지원하지 않습니다.',
  'language-not-supported': '이 브라우저 음성 인식은 한국어를 지원하지 않습니다.',
};

function environment() {
  return globalThis.window || globalThis;
}

function voiceError(code, message, cause) {
  const error = new Error(message || ERROR_MESSAGES[code] || '음성 입력 중 오류가 발생했습니다.');
  error.name = code === 'aborted' ? 'AbortError' : 'VoiceCaptureError';
  error.code = code;
  if (cause) error.cause = cause;
  return error;
}

function permissionError(error) {
  if (error?.code && typeof error.code === 'string') return error;
  const code = {
    NotAllowedError: 'not-allowed',
    PermissionDeniedError: 'not-allowed',
    NotFoundError: 'audio-capture',
    DevicesNotFoundError: 'audio-capture',
    NotReadableError: 'audio-capture',
    TrackStartError: 'audio-capture',
    SecurityError: 'not-allowed',
  }[error?.name] || 'recording-failed';
  return voiceError(code, ERROR_MESSAGES[code] || '마이크 녹음을 시작하지 못했습니다.', error);
}

function later(callback, milliseconds) {
  const timer = setTimeout(callback, milliseconds);
  timer?.unref?.();
  return timer;
}

function stopTracks(stream) {
  for (const track of stream?.getTracks?.() || []) {
    try { track.stop(); } catch { /* Release every remaining track, even if one is already closed. */ }
  }
}

/** Box-average downsampling avoids dropping samples at 44.1/48 kHz. */
export function resampleMono(samples, inputSampleRate, outputSampleRate = OUTPUT_SAMPLE_RATE) {
  if (!(samples instanceof Float32Array)) throw new TypeError('samples must be a Float32Array');
  if (!Number.isFinite(inputSampleRate) || inputSampleRate <= 0 ||
      !Number.isFinite(outputSampleRate) || outputSampleRate <= 0) {
    throw new RangeError('Sample rates must be finite, positive numbers');
  }
  const inputLength = Math.min(samples.length, Math.floor(inputSampleRate * MAX_RECORDING_SECONDS));
  const length = Math.min(Math.round(inputLength * outputSampleRate / inputSampleRate),
    Math.floor(outputSampleRate * MAX_RECORDING_SECONDS));
  const output = new Float32Array(length);
  if (!inputLength) return output;
  const ratio = inputSampleRate / outputSampleRate;
  const finite = (value) => Number.isFinite(value) ? value : 0;
  for (let i = 0; i < length; i += 1) {
    const start = Math.min(i * ratio, inputLength - 1);
    if (ratio < 1) {
      const left = Math.floor(start);
      const right = Math.min(left + 1, inputLength - 1);
      output[i] = finite(samples[left]) * (1 - (start - left)) + finite(samples[right]) * (start - left);
      continue;
    }
    const end = Math.min((i + 1) * ratio, inputLength);
    let position = start;
    let sum = 0;
    while (position < end) {
      const index = Math.floor(position);
      const next = Math.min(end, index + 1);
      sum += finite(samples[index]) * (next - position);
      position = next;
    }
    output[i] = end > start ? sum / (end - start) : 0;
  }
  return output;
}

/** Return at most 60 seconds of 16 kHz, mono, signed 16-bit little-endian PCM WAV. */
export function encodeWav(samples, inputSampleRate) {
  const mono = resampleMono(samples, inputSampleRate);
  const bytes = new Uint8Array(44 + mono.length * 2);
  const view = new DataView(bytes.buffer);
  const ascii = (offset, value) => {
    for (let i = 0; i < value.length; i += 1) bytes[offset + i] = value.charCodeAt(i);
  };
  ascii(0, 'RIFF');
  view.setUint32(4, bytes.length - 8, true);
  ascii(8, 'WAVE');
  ascii(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, OUTPUT_SAMPLE_RATE, true);
  view.setUint32(28, OUTPUT_SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  ascii(36, 'data');
  view.setUint32(40, mono.length * 2, true);
  for (let i = 0; i < mono.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, mono[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 0x8000 : 0x7fff)), true);
  }
  return bytes;
}

export function bytesToBase64(bytes) {
  if (typeof globalThis.btoa === 'function') {
    const chunks = [];
    for (let i = 0; i < bytes.length; i += 8192) {
      chunks.push(String.fromCharCode(...bytes.subarray(i, i + 8192)));
    }
    return globalThis.btoa(chunks.join(''));
  }
  if (globalThis.Buffer) return globalThis.Buffer.from(bytes).toString('base64');
  throw voiceError('encoding-unavailable', '이 브라우저에서는 녹음 파일을 변환할 수 없습니다.');
}

export class VoiceCapture {
  static capabilities() {
    const env = environment();
    return {
      browser: Boolean(env.SpeechRecognition || env.webkitSpeechRecognition),
      recording: Boolean(env.navigator?.mediaDevices?.getUserMedia && (env.AudioContext || env.webkitAudioContext)),
      tts: Boolean(env.speechSynthesis && env.SpeechSynthesisUtterance),
    };
  }

  /** Whether the browser exposes contextual biasing at all. Support is not an accuracy gain. */
  static phraseSupport() {
    return Boolean(environment().SpeechRecognitionPhrase);
  }

  constructor({ onInterim, onFinal, onState, onError, phrases = [] } = {}) {
    this._callbacks = { onInterim, onFinal, onState, onError };
    this._session = null;
    this._generation = 0;
    this._speechGeneration = 0;
    this._phrasesRejected = false;
    this.setPhrases(phrases);
  }

  get active() { return Boolean(this._session); }

  /**
   * phraseStatus: none | unsupported | pending | applied | rejected.
   * 'applied' only means the browser started with the phrases; the effect is unmeasured.
   */
  setPhrases(list = []) {
    this._phrases = [...new Set((Array.isArray(list) ? list : []).filter((p) => typeof p === 'string')
      .map((p) => p.trim()).filter(Boolean))].slice(0, MAX_PHRASES);
    this.phraseStatus = !this._phrases.length ? 'none' : this._phrasesRejected ? 'rejected'
      : VoiceCapture.phraseSupport() ? 'pending' : 'unsupported';
    return this._phrases.slice();
  }

  _notify(name, value, detail) {
    try { this._callbacks[name]?.(value, detail); }
    catch (error) { globalThis.console?.error?.(`VoiceCapture ${name} callback failed`, error); }
  }

  _current(session) {
    return this._session === session && session.id === this._generation && !session.cancelled;
  }

  /** `alternatives` asks the browser for up to that many hypotheses per final result (onFinal's 2nd argument). */
  async start(mode = 'browser', { alternatives = 1 } = {}) {
    if (this.active) throw voiceError('already-active', '이미 음성 입력을 진행하고 있습니다.');
    if (mode !== 'browser' && mode !== 'azure') throw voiceError('unsupported-mode', '지원하지 않는 음성 입력 방식입니다.');
    this.stopSpeaking();
    const session = {
      id: ++this._generation, mode, cancelled: false, started: false, stopping: false,
      chunks: [], frameCount: 0, finalIndices: new Set(),
      alternatives: Math.min(10, Math.max(1, Math.trunc(Number(alternatives)) || 1)),
    };
    this._session = session;
    try {
      if (mode === 'browser') await this._startBrowser(session);
      else await new Promise((resolve, reject) => {
        // Permission prompts may remain open after Cancel. Reject the public start
        // immediately, while the underlying request still releases any late stream.
        session.startReject = reject;
        this._startRecording(session).then(() => {
          if (!this._current(session)) { reject(voiceError('aborted')); return; }
          session.startReject = null;
          resolve();
        }, reject);
      });
    } catch (error) {
      const failure = permissionError(error);
      if (this._current(session)) this._fail(session, failure);
      throw failure;
    }
  }

  _startBrowser(session) {
    const env = environment();
    const Recognition = env.SpeechRecognition || env.webkitSpeechRecognition;
    if (!Recognition) throw voiceError('recognition-unavailable', '이 브라우저는 실시간 음성 인식을 지원하지 않습니다. Azure 녹음 또는 직접 입력을 사용하세요.');
    return new Promise((resolve, reject) => {
      session.startResolve = resolve;
      session.startReject = reject;
      this._launchRecognition(session, Recognition, this._phrases.length > 0 && !this._phrasesRejected);
    });
  }

  _launchRecognition(session, Recognition, usePhrases) {
    const env = environment();
    const recognition = new Recognition();
    session.recognition = recognition;
    session.finalIndices = new Set();
    session.phrasesApplied = false;
    // After a phrases fallback the session outlives this recognizer; only the active one may touch it.
    const owns = () => this._current(session) && session.recognition === recognition;
    recognition.lang = 'ko-KR';
    recognition.interimResults = true;
    recognition.continuous = true;
    recognition.maxAlternatives = session.alternatives;
    if (usePhrases) {
      if (!env.SpeechRecognitionPhrase || !('phrases' in recognition)) this.phraseStatus = 'unsupported';
      else {
        try {
          recognition.phrases = this._phrases.map((phrase) => new env.SpeechRecognitionPhrase(phrase, PHRASE_BOOST));
          session.phrasesApplied = true;
        } catch {
          this._phrasesRejected = true;
          this.phraseStatus = 'rejected';
        }
      }
    }
    recognition.onstart = () => {
      if (!owns()) return;
      const first = !session.started;
      session.started = true;
      if (session.phrasesApplied) this.phraseStatus = 'applied';
      session.startResolve?.();
      session.startResolve = session.startReject = null;
      if (first) this._notify('onState', 'listening');
    };
    recognition.onresult = (event) => {
      if (!owns()) return;
      const results = event.results || [];
      const firstChanged = Math.max(0, event.resultIndex || 0);
      for (let i = firstChanged; i < results.length; i += 1) {
        if (!owns()) return;
        if (results[i].isFinal && !session.finalIndices.has(i)) {
          session.finalIndices.add(i);
          const transcript = String(results[i][0]?.transcript || '').trim();
          const hypotheses = Array.from({ length: Math.min(results[i].length || 1, session.alternatives) },
            (_, k) => String(results[i][k]?.transcript || '').trim()).filter(Boolean);
          if (transcript) this._notify('onFinal', transcript, [...new Set([transcript, ...hypotheses])]);
        }
      }
      if (!owns()) return;
      const interim = [];
      for (let i = 0; i < results.length; i += 1) {
        if (!results[i].isFinal) interim.push(String(results[i][0]?.transcript || '').trim());
      }
      this._notify('onInterim', interim.filter(Boolean).join(' '));
    };
    recognition.onerror = (event) => {
      if (!owns()) return;
      if (event.error === 'phrases-not-supported' && session.phrasesApplied && !session.stopping) {
        // The spec fires this at start. Retry once with plain recognition; never retry other errors.
        this._phrasesRejected = true;
        this.phraseStatus = 'rejected';
        this._detachRecognition(session, true);
        try { this._launchRecognition(session, Recognition, false); }
        catch (error) { this._fail(session, voiceError('recognition-failed', '브라우저 음성 인식을 다시 시작하지 못했습니다.', error)); }
        return;
      }
      this._fail(session, voiceError(event.error || 'recognition-failed', ERROR_MESSAGES[event.error] || '브라우저 음성 인식에 실패했습니다.', event));
    };
    recognition.onend = () => {
      if (!owns()) return;
      if (!session.started) {
        this._fail(session, voiceError('no-speech'));
        return;
      }
      this._endSession(session);
      session.stopResolve?.(null);
    };
    recognition.start();
  }

  async _startRecording(session) {
    const env = environment();
    const Context = env.AudioContext || env.webkitAudioContext;
    if (!env.navigator?.mediaDevices?.getUserMedia || !Context) {
      throw voiceError('recording-unavailable', '이 브라우저는 마이크 녹음을 지원하지 않습니다. HTTPS 또는 localhost에서 실행하고 마이크를 확인하세요.');
    }
    const stream = await env.navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    if (!this._current(session)) {
      stopTracks(stream);
      throw voiceError('aborted');
    }
    session.stream = stream;
    const context = new Context();
    session.context = context;
    if (!Number.isFinite(context.sampleRate) || context.sampleRate <= 0) {
      throw voiceError('sample-rate-unavailable', '마이크의 오디오 샘플 속도를 확인하지 못했습니다.');
    }
    session.sampleRate = context.sampleRate;
    session.maxFrames = Math.floor(context.sampleRate * MAX_RECORDING_SECONDS);
    session.source = context.createMediaStreamSource(stream);
    let workletFailure;
    if (context.audioWorklet?.addModule && env.AudioWorkletNode) {
      try {
        await context.audioWorklet.addModule(new URL('./pcm-worklet.js', import.meta.url).href);
        if (!this._current(session)) throw voiceError('aborted');
        session.processor = new env.AudioWorkletNode(context, 'paramedic-pcm-capture', {
          numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
        });
        session.processor.port.onmessage = (event) => this._collect(session, event.data);
        session.processor.onprocessorerror = (event) => {
          if (this._current(session)) this._fail(session, voiceError('recording-failed', '마이크 오디오 처리가 중단되었습니다.', event));
        };
      } catch (error) {
        if (!this._current(session)) throw voiceError('aborted');
        workletFailure = error;
      }
    }
    if (!session.processor) {
      if (!context.createScriptProcessor) {
        throw voiceError('recording-unavailable', '이 브라우저는 필요한 오디오 녹음 기능을 지원하지 않습니다.', workletFailure);
      }
      session.processor = context.createScriptProcessor(4096, 1, 1);
      session.processor.onaudioprocess = (event) => {
        if (!this._current(session) || session.stopping || session.limited) return;
        const input = event.inputBuffer;
        const count = input.numberOfChannels || 1;
        const mono = new Float32Array(input.length ?? input.getChannelData(0).length);
        for (let channel = 0; channel < count; channel += 1) {
          const samples = input.getChannelData(channel);
          for (let i = 0; i < mono.length; i += 1) mono[i] += samples[i] / count;
        }
        this._collect(session, mono);
      };
    }
    session.gain = context.createGain();
    session.gain.gain.value = 0;
    session.source.connect(session.processor);
    session.processor.connect(session.gain);
    session.gain.connect(context.destination);
    await context.resume?.();
    if (!this._current(session)) throw voiceError('aborted');
    session.started = true;
    session.limitTimer = later(() => this._limit(session), MAX_RECORDING_SECONDS * 1000);
    this._notify('onState', 'recording');
  }

  _collect(session, samples) {
    if (!this._current(session) || session.stopping || session.limited || !samples?.length) return;
    const remaining = session.maxFrames - session.frameCount;
    const chunk = Float32Array.from(samples.subarray ? samples.subarray(0, remaining) : Array.from(samples).slice(0, remaining));
    if (chunk.length) {
      session.chunks.push(chunk);
      session.frameCount += chunk.length;
    }
    if (session.frameCount >= session.maxFrames) this._limit(session);
  }

  _limit(session) {
    if (!this._current(session) || session.limited || session.stopping) return;
    session.limited = true;
    this._releaseAudio(session);
    this._notify('onState', 'limit');
  }

  _detachRecognition(session, abort = false) {
    const recognition = session.recognition;
    if (!recognition) return;
    recognition.onstart = recognition.onresult = recognition.onerror = recognition.onend = null;
    if (abort) {
      try { recognition.abort(); } catch { /* Already stopped. */ }
    }
    session.recognition = null;
  }

  _releaseAudio(session) {
    if (session.releasePromise) return session.releasePromise;
    clearTimeout(session.limitTimer);
    stopTracks(session.stream);
    session.stream = null;
    if (session.processor?.port) {
      session.processor.port.onmessage = null;
      try { session.processor.port.close?.(); } catch { /* Continue releasing the rest of the graph. */ }
    }
    if (session.processor) {
      session.processor.onaudioprocess = null;
      session.processor.onprocessorerror = null;
    }
    for (const node of [session.source, session.processor, session.gain]) {
      try { node?.disconnect(); } catch { /* An already disconnected node needs no further work. */ }
    }
    session.source = session.processor = session.gain = null;
    const context = session.context;
    session.context = null;
    try {
      session.releasePromise = Promise.resolve(context && context.state !== 'closed' ? context.close() : undefined).catch(() => {});
    } catch {
      session.releasePromise = Promise.resolve();
    }
    return session.releasePromise;
  }

  _endSession(session) {
    if (!this._current(session)) return;
    clearTimeout(session.endTimer);
    clearTimeout(session.limitTimer);
    this._detachRecognition(session);
    this._session = null;
    this._generation += 1;
    this._notify('onInterim', '');
    this._notify('onState', 'idle');
  }

  _fail(session, error) {
    if (!this._current(session)) return;
    this._detachRecognition(session, true);
    this._releaseAudio(session);
    session.startReject?.(error);
    session.startResolve = session.startReject = null;
    this._endSession(session);
    session.cancelled = true;
    session.chunks = [];
    session.stopResolve?.(null);
    this._notify('onError', error);
  }

  async stop() {
    const session = this._session;
    if (!session) return null;
    if (session.stopPromise) return session.stopPromise;
    if (!session.started) {
      this.abort();
      return null;
    }
    session.stopping = true;
    if (session.mode === 'browser') {
      session.stopPromise = new Promise((resolve) => { session.stopResolve = resolve; });
      session.endTimer = later(() => {
        if (this._current(session)) this._fail(session, voiceError('stop-timeout', '음성 인식 종료가 지연되어 마이크 연결을 해제했습니다.'));
      }, 5000);
      try { session.recognition.stop(); }
      catch (error) { this._fail(session, voiceError('stop-failed', '음성 인식을 종료하지 못했습니다.', error)); }
      return session.stopPromise;
    }
    session.stopPromise = (async () => {
      await this._releaseAudio(session);
      if (!this._current(session)) return null;
      if (!session.frameCount) {
        this._fail(session, voiceError('no-audio', '녹음된 오디오가 없습니다. 마이크를 확인하고 다시 시작하세요.'));
        return null;
      }
      try {
        const samples = new Float32Array(session.frameCount);
        let offset = 0;
        for (const chunk of session.chunks) {
          samples.set(chunk, offset);
          offset += chunk.length;
        }
        const wav = encodeWav(samples, session.sampleRate);
        const result = { audio_base64: bytesToBase64(wav), content_type: 'audio/wav' };
        session.chunks = [];
        this._endSession(session);
        return result;
      } catch (error) {
        this._fail(session, voiceError('encoding-failed', '녹음 파일을 변환하지 못했습니다.', error));
        return null;
      }
    })();
    return session.stopPromise;
  }

  abort() {
    const session = this._session;
    if (!session) return;
    this._detachRecognition(session, true);
    this._releaseAudio(session);
    session.startReject?.(voiceError('aborted'));
    session.startResolve = session.startReject = null;
    this._endSession(session);
    session.cancelled = true;
    session.chunks = [];
    session.stopResolve?.(null);
  }

  speak(text) {
    if (this.active) throw voiceError('capture-active', '마이크 입력을 먼저 종료한 뒤 안내 음성을 재생하세요.');
    const env = environment();
    if (!String(text || '').trim()) return;
    if (!env.speechSynthesis || !env.SpeechSynthesisUtterance) {
      throw voiceError('tts-unavailable', '이 브라우저는 안내 음성 재생을 지원하지 않습니다.');
    }
    this.stopSpeaking();
    const generation = this._speechGeneration;
    const utterance = new env.SpeechSynthesisUtterance(String(text));
    utterance.lang = 'ko-KR';
    utterance.onerror = (event) => {
      if (generation !== this._speechGeneration || ['canceled', 'interrupted'].includes(event.error)) return;
      this._notify('onError', voiceError('tts-failed', '안내 음성을 재생하지 못했습니다.', event));
    };
    env.speechSynthesis.speak(utterance);
  }

  stopSpeaking() {
    this._speechGeneration += 1;
    try { environment().speechSynthesis?.cancel(); } catch { /* The synthesizer may already be closed. */ }
  }
}
