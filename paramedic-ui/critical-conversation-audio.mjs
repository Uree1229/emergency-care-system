/** Local browser IO for explicit spoken review. No parser, acknowledgement or API writes. */
const DRAIN_MS = 500;
const RECOGNITION_MESSAGES = {
  'not-allowed': '마이크 권한이 거부되었습니다. 브라우저 권한을 확인한 뒤 다시 시작하세요.',
  'service-not-allowed': '브라우저 음성 인식 서비스를 사용할 수 없습니다.',
  'audio-capture': '마이크 입력을 사용할 수 없습니다. 장치 연결을 확인하세요.',
  'no-speech': '응답 음성이 감지되지 않았습니다. 화면에서 다시 시작하세요.',
  network: '음성 인식 서비스 연결을 확인한 뒤 다시 시작하세요.',
  aborted: '음성 인식이 중단되었습니다.',
};

function audioError(code, message, cause, causeCode) {
  const error = new Error(message);
  error.name = code === 'ABORTED' ? 'AbortError' : 'CriticalConversationAudioError';
  error.code = code;
  if (cause !== undefined) error.cause = cause;
  if (causeCode !== undefined) error.causeCode = causeCode;
  return error;
}

function duration(value, maximum, name) {
  if (!Number.isFinite(value) || value <= 0 || value > maximum) {
    throw audioError('INVALID_OPTIONS', `${name}는 0보다 크고 ${maximum} 이하여야 합니다.`);
  }
  return value;
}

export function createCriticalConversationAudio({
  windowRef = globalThis.window || globalThis,
  recognitionMs = 8000,
  ttsMs = 60000,
  onState,
} = {}) {
  recognitionMs = duration(recognitionMs, 8000, 'recognitionMs');
  ttsMs = duration(ttsMs, 60000, 'ttsMs');
  const env = windowRef || {};
  const later = (env.setTimeout || globalThis.setTimeout).bind(env);
  const cancelLater = (env.clearTimeout || globalThis.clearTimeout).bind(env);
  let epoch = 0, active = null, disposed = false;

  const recognitionType = () => env.SpeechRecognition || env.webkitSpeechRecognition;
  const synthesis = () => env.speechSynthesis;
  const capabilities = () => ({
    tts: typeof env.SpeechSynthesisUtterance === 'function' &&
      typeof synthesis()?.speak === 'function' && typeof synthesis()?.cancel === 'function',
    browser: typeof recognitionType() === 'function',
  });
  const emit = (phase, reason) => {
    try { onState?.({phase, ...(reason ? {reason} : {})}); }
    catch { /* A rendering callback cannot keep audio running or prevent cleanup. */ }
  };
  const current = operation => active === operation && operation.epoch === epoch;
  const clear = operation => {
    for (const timer of operation.timers) cancelLater(timer);
    operation.timers.clear();
    if (operation.resource) {
      for (const name of ['onstart', 'onend', 'onerror', 'onresult', 'onnomatch']) {
        operation.resource[name] = null;
      }
    }
  };
  const stopOwned = operation => {
    try {
      if (operation.phase === 'speaking') operation.synth?.cancel();
      else operation.resource?.abort();
    } catch { /* State and the pending promise are still released. */ }
  };
  const finish = (operation, error, result, stop = false) => {
    if (!current(operation)) return;
    clear(operation);
    active = null;
    ++epoch;
    if (stop) stopOwned(operation);
    if (error) operation.reject(error);
    else operation.resolve(result);
    emit('idle', error?.code);
  };
  const arm = (operation, callback, milliseconds) => {
    const timer = later(() => {
      operation.timers.delete(timer);
      if (current(operation)) callback();
    }, milliseconds);
    operation.timers.add(timer);
  };
  const available = feature => {
    if (disposed) throw audioError('DISPOSED', '종료된 음성 확인입니다. 화면에서 다시 시작하세요.');
    if (active || synthesis()?.speaking || synthesis()?.pending) {
      throw audioError('BUSY', '진행 중인 말하기·듣기가 끝난 뒤 다시 시작하세요.');
    }
    if (!capabilities()[feature]) throw audioError('UNSUPPORTED', '이 브라우저에서 필요한 음성 기능을 지원하지 않습니다.');
  };

  async function speak(text) {
    available('tts');
    if (typeof text !== 'string' || !text.trim()) throw audioError('INVALID_TEXT', '읽어줄 내용이 없습니다.');
    return new Promise((resolve, reject) => {
      const operation = {epoch: ++epoch, phase: 'speaking', resource: null, synth: synthesis(), timers: new Set(), resolve, reject};
      active = operation;
      try {
        const utterance = new env.SpeechSynthesisUtterance(text);
        operation.resource = utterance;
        utterance.lang = 'ko-KR';
        utterance.onend = () => finish(operation);
        utterance.onerror = event => finish(operation, audioError('TTS_ERROR', '음성 안내를 끝내지 못했습니다. 화면에서 다시 시작하세요.', event, event?.error), undefined, true);
        arm(operation, () => finish(operation, audioError('TTS_TIMEOUT', '음성 안내 종료를 확인하지 못했습니다. 화면에서 다시 시작하세요.'), undefined, true), ttsMs);
        emit('speaking');
        if (current(operation)) operation.synth.speak(utterance);
      } catch (cause) {
        finish(operation, audioError('TTS_ERROR', '음성 안내를 시작하지 못했습니다.', cause), undefined, true);
      }
    });
  }

  async function listen() {
    available('browser');
    return new Promise((resolve, reject) => {
      const operation = {epoch: ++epoch, phase: 'listening', resource: null, timers: new Set(), resolve, reject};
      const finals = new Map();
      let timedOut = false;
      active = operation;
      try {
        const Recognition = recognitionType();
        const recognition = new Recognition();
        operation.resource = recognition;
        recognition.lang = 'ko-KR';
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.maxAlternatives = 1;
        recognition.onresult = event => {
          if (!current(operation)) return;
          try {
            if (!event?.results || !Number.isInteger(event.resultIndex) || event.resultIndex < 0) return;
            for (let index = event.resultIndex; index < event.results.length; ++index) {
              const result = event.results[index], text = result?.[0]?.transcript;
              if (result?.isFinal === true && typeof text === 'string') {
                const trimmed = text.trim();
                if (trimmed) finals.set(index, trimmed);
                else finals.delete(index);
              }
            }
          } catch (cause) {
            finish(operation, audioError('RECOGNITION_ERROR', '응답 인식 결과를 읽지 못했습니다.', cause), undefined, true);
          }
        };
        recognition.onend = () => {
          if (!current(operation)) return;
          const pieces = [...finals].sort(([a], [b]) => a - b).map(([, text]) => text);
          if (!pieces.length) {
            finish(operation, audioError(timedOut ? 'RECOGNITION_TIMEOUT' : 'EMPTY', timedOut ? '응답을 기다리는 시간이 끝났습니다. 화면에서 다시 시작하세요.' : '확정된 응답을 듣지 못했습니다. 화면에서 다시 시작하세요.'));
          } else finish(operation, undefined, {text: pieces.join(' '), finals: pieces});
        };
        recognition.onerror = event => finish(operation, audioError('RECOGNITION_ERROR', RECOGNITION_MESSAGES[event?.error] || '응답 음성 인식을 끝내지 못했습니다. 화면에서 다시 시작하세요.', event, event?.error), undefined, true);
        arm(operation, () => {
          timedOut = true;
          arm(operation, () => finish(operation, audioError('RECOGNITION_TIMEOUT', '응답 듣기를 종료하지 못해 중단했습니다. 화면에서 다시 시작하세요.'), undefined, true), DRAIN_MS);
          try { recognition.stop(); }
          catch (cause) { finish(operation, audioError('RECOGNITION_TIMEOUT', '응답 듣기를 중단했습니다. 화면에서 다시 시작하세요.', cause), undefined, true); }
        }, recognitionMs);
        emit('listening');
        if (current(operation)) recognition.start();
      } catch (cause) {
        finish(operation, audioError('RECOGNITION_ERROR', '응답 음성 인식을 시작하지 못했습니다.', cause, cause?.name), undefined, true);
      }
    });
  }

  function abort(reason = 'aborted') {
    const operation = active;
    if (!operation) { ++epoch; return; }
    finish(operation, audioError('ABORTED', '음성 확인을 중단했습니다.', undefined, String(reason)), undefined, true);
  }
  function dispose() {
    if (disposed) return;
    disposed = true;
    abort('disposed');
  }
  return {capabilities, speak, listen, abort, dispose};
}
