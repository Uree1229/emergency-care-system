import test from 'node:test';
import assert from 'node:assert/strict';
import {createCriticalConversationAudio} from './paramedic-ui/critical-conversation-audio.mjs';

// All audio/recognition and timers below are injected fakes. No device or playback API is used.
function fixture(options = {}) {
  let now = 0, timerId = 0;
  const timers = new Map(), utterances = [], recognitions = [], states = [];
  const env = {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, {at: now + delay, fn}); return id; },
    clearTimeout(id) { timers.delete(id); },
    SpeechSynthesisUtterance: class { constructor(text) { this.text = text; utterances.push(this); } },
    SpeechRecognition: class {
      constructor() { recognitions.push(this); this.starts = 0; this.stops = 0; this.aborts = 0; }
      start() { ++this.starts; if (options.startThrows) throw Object.assign(new Error('mock start'), {name: 'NotAllowedError'}); }
      stop() { ++this.stops; if (options.stopThrows) throw new Error('mock stop'); if (options.endOnStop) this.onend?.(); }
      abort() { ++this.aborts; this.onend?.(); }
    },
    speechSynthesis: {
      speaking: false, pending: false, calls: 0, cancels: 0,
      speak() { ++this.calls; this.speaking = true; if (options.speakThrows) throw new Error('mock speak'); },
      cancel() { ++this.cancels; this.speaking = false; utterances.at(-1)?.onend?.(); },
    },
  };
  function tick(delta) {
    const target = now + delta;
    for (;;) {
      const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]); next[1].fn();
    }
    now = target;
  }
  const audio = createCriticalConversationAudio({windowRef: env, ...options.audio, onState: state => { states.push(state); options.onState?.(state, audio); }});
  const endSpeech = () => { env.speechSynthesis.speaking = false; utterances.at(-1).onend?.(); };
  const result = (index, entries) => recognitions.at(-1).onresult?.({resultIndex: index, results: entries.map(entry => Object.assign(entry.alternatives || [{transcript: entry.text}], {isFinal: entry.final !== false}))});
  return {env, audio, tick, timers, utterances, recognitions, states, endSpeech, result};
}
const rejectCode = (promise, code) => assert.rejects(promise, error => error.code === code);
const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

test('capabilities report support without permission requests, speech or recognition start', () => {
  const f = fixture(); assert.deepEqual(f.audio.capabilities(), {tts: true, browser: true});
  assert.equal(f.utterances.length + f.recognitions.length + f.timers.size, 0);
});
test('unsupported APIs reject without starting another mode', async () => {
  const audio = createCriticalConversationAudio({windowRef: {}});
  assert.deepEqual(audio.capabilities(), {tts: false, browser: false});
  await rejectCode(audio.speak('읽기'), 'UNSUPPORTED'); await rejectCode(audio.listen(), 'UNSUPPORTED');
});
test('TTS resolves only after the owned utterance end and never opens a microphone', async () => {
  const f = fixture(); let done = false; const p = f.audio.speak('맥박 90. 맞나요?').then(() => { done = true; });
  assert.equal(f.utterances[0].lang, 'ko-KR'); await flush(); assert.equal(done, false);
  f.tick(2000); await flush(); assert.equal(done, false); assert.equal(f.recognitions.length, 0);
  f.endSpeech(); await p; assert.equal(done, true); assert.equal(f.timers.size, 0);
});
test('TTS failure rejects and does not start recognition', async () => {
  const f = fixture(), p = f.audio.speak('검토'); const rejected = rejectCode(p, 'TTS_ERROR');
  f.utterances[0].onerror({error: 'synthesis-failed'}); await rejected;
  assert.equal(f.recognitions.length, 0); assert.equal(f.env.speechSynthesis.cancels, 1); assert.equal(f.timers.size, 0);
});
test('TTS timeout cancels owned speech and settles without a microphone', async () => {
  const f = fixture(), p = f.audio.speak('검토'), rejected = rejectCode(p, 'TTS_TIMEOUT');
  f.tick(60000); await rejected; assert.equal(f.env.speechSynthesis.cancels, 1); assert.equal(f.recognitions.length, 0); assert.equal(f.timers.size, 0);
});
test('one task at a time blocks both TTS and microphone overlap', async () => {
  const f = fixture(), p = f.audio.speak('검토'); await rejectCode(f.audio.listen(), 'BUSY'); await rejectCode(f.audio.speak('또'), 'BUSY');
  f.endSpeech(); await p;
  const q = f.audio.listen(); await rejectCode(f.audio.speak('겹침'), 'BUSY'); await rejectCode(f.audio.listen(), 'BUSY');
  const rejected = rejectCode(q, 'ABORTED'); f.audio.abort(); await rejected;
});
test('listening is noncontinuous Korean and ignores interim until recognition end', async () => {
  const f = fixture(), p = f.audio.listen(); let done = false; p.then(() => { done = true; }); const r = f.recognitions[0];
  assert.equal(r.lang, 'ko-KR'); assert.equal(r.continuous, false); assert.equal(r.interimResults, false); assert.equal(r.maxAlternatives, 1);
  f.result(0, [{text: '맞아요', final: false}]); await flush(); assert.equal(done, false);
  f.result(0, [{text: '아니요'}]); await flush(); assert.equal(done, false);
  r.onend(); assert.deepEqual(await p, {text: '아니요', finals: ['아니요']}); assert.equal(f.timers.size, 0);
});
test('all final indexes are joined and repeated result indexes are not duplicated', async () => {
  const f = fixture(), p = f.audio.listen();
  f.result(0, [{text: '네'}, {text: '틀렸어요', final: false}]);
  f.result(1, [{text: '네'}, {text: '아니요'}]);
  f.result(1, [{text: '네'}, {text: '아니요'}, {text: '수정할게요'}]);
  f.recognitions[0].onend(); assert.deepEqual(await p, {text: '네 아니요 수정할게요', finals: ['네', '아니요', '수정할게요']});
});
test('lower rank alternatives cannot become the recognized response', async () => {
  const f = fixture(), p = f.audio.listen(); f.result(0, [{alternatives: [{transcript: '아니요'}, {transcript: '맞아요'}]}]);
  f.recognitions[0].onend(); assert.equal((await p).text, '아니요');
});
test('whitespace is trimmed and final indexes retain their order', async () => {
  const f = fixture(), p = f.audio.listen(); f.result(1, [{text: 'ignored'}, {text: '  단계  '}]);
  f.result(0, [{text: ' 다음 '}, {text: ' 단계 '}]); f.recognitions[0].onend();
  assert.deepEqual(await p, {text: '다음 단계', finals: ['다음', '단계']});
});
test('interim-only or empty end is not a successful confirmation', async () => {
  for (const entries of [[], [{text: '네', final: false}], [{text: '  '}]]) {
    const f = fixture(), p = f.audio.listen(), rejected = rejectCode(p, 'EMPTY'); f.result(0, entries); f.recognitions[0].onend();
    await rejected; assert.equal(f.recognitions.length, 1); assert.equal(f.timers.size, 0);
  }
});
test('permission denial preserves the native error code and never restarts', async () => {
  const f = fixture(), p = f.audio.listen(), rejected = assert.rejects(p, error => error.code === 'RECOGNITION_ERROR' && error.causeCode === 'not-allowed' && /권한/.test(error.message));
  f.recognitions[0].onerror({error: 'not-allowed'}); await rejected; f.tick(100000);
  assert.equal(f.recognitions.length, 1); assert.equal(f.recognitions[0].aborts, 1); assert.equal(f.timers.size, 0);
});
test('network or no-speech errors terminate the one recognition without success', async () => {
  for (const code of ['network', 'no-speech', 'audio-capture', 'aborted', 'service-not-allowed', 'unknown-native']) {
    const f = fixture(), p = f.audio.listen(), rejected = assert.rejects(p, error => error.code === 'RECOGNITION_ERROR' && error.causeCode === code);
    f.recognitions[0].onerror({error: code}); await rejected; assert.equal(f.timers.size, 0); assert.equal(f.recognitions.length, 1);
  }
});
test('eight-second deadline stops recognition and accepts only a final onend during short drain', async () => {
  const f = fixture(), p = f.audio.listen(); f.tick(7999); assert.equal(f.recognitions[0].stops, 0);
  f.tick(1); assert.equal(f.recognitions[0].stops, 1); assert.equal(f.recognitions[0].aborts, 0);
  f.result(0, [{text: '맞아요'}]); f.tick(499); f.recognitions[0].onend();
  assert.equal((await p).text, '맞아요'); assert.equal(f.timers.size, 0);
});
test('no end after deadline aborts at 8.5 seconds and leaves no timers', async () => {
  const f = fixture(), p = f.audio.listen(), rejected = rejectCode(p, 'RECOGNITION_TIMEOUT');
  f.tick(8500); await rejected; assert.equal(f.recognitions[0].stops, 1); assert.equal(f.recognitions[0].aborts, 1); assert.equal(f.timers.size, 0);
});
test('empty synchronous onend during stop cleans the drain timer', async () => {
  const f = fixture({endOnStop: true}), p = f.audio.listen(), rejected = rejectCode(p, 'RECOGNITION_TIMEOUT');
  f.tick(8000); await rejected; assert.equal(f.recognitions[0].aborts, 0); assert.equal(f.timers.size, 0);
});
test('stop exception aborts and does not leave a pending operation', async () => {
  const f = fixture({stopThrows: true}), p = f.audio.listen(), rejected = rejectCode(p, 'RECOGNITION_TIMEOUT');
  f.tick(8000); await rejected; assert.equal(f.recognitions[0].aborts, 1); assert.equal(f.timers.size, 0);
});
test('synchronous recognition start failure settles and cleans every callback', async () => {
  const f = fixture({startThrows: true}); await rejectCode(f.audio.listen(), 'RECOGNITION_ERROR');
  assert.equal(f.recognitions[0].onresult, null); assert.equal(f.recognitions[0].onend, null); assert.equal(f.timers.size, 0);
});
test('synchronous speech start failure cancels owned utterance and releases task', async () => {
  const f = fixture({speakThrows: true}); await rejectCode(f.audio.speak('검토'), 'TTS_ERROR');
  assert.equal(f.utterances[0].onend, null); assert.equal(f.env.speechSynthesis.cancels, 1); assert.equal(f.timers.size, 0);
});
test('abort speech rejects immediately and stale end cannot settle a new speech', async () => {
  const f = fixture(), p = f.audio.speak('첫째'), oldEnd = f.utterances[0].onend, rejected = rejectCode(p, 'ABORTED');
  f.audio.abort('patient-switch'); await rejected;
  let done = false; const q = f.audio.speak('둘째').then(() => { done = true; }); oldEnd(); await flush(); assert.equal(done, false);
  f.endSpeech(); await q; assert.equal(f.timers.size, 0);
});
test('abort recognition rejects immediately and stale result/end cannot finish the new operation', async () => {
  const f = fixture(), p = f.audio.listen(), old = f.recognitions[0], oldResult = old.onresult, oldEnd = old.onend;
  const rejected = rejectCode(p, 'ABORTED'); f.audio.abort('hidden'); await rejected; assert.equal(old.onresult, null); assert.equal(old.onend, null);
  const q = f.audio.listen(); oldResult({resultIndex: 0, results: [Object.assign([{transcript: '맞아요'}], {isFinal: true})]}); oldEnd();
  f.result(0, [{text: '아니요'}]); f.recognitions[1].onend(); assert.deepEqual(await q, {text: '아니요', finals: ['아니요']});
});
test('repeated abort without an owned task does not cancel someone else speech', async () => {
  const f = fixture(); f.env.speechSynthesis.speaking = true; f.audio.abort(); f.audio.abort(); f.audio.dispose();
  assert.equal(f.env.speechSynthesis.cancels, 0); assert.equal(f.recognitions.length, 0);
});
test('external speech or queued speech prevents recognition and extra playback', async () => {
  for (const key of ['speaking', 'pending']) {
    const f = fixture(); f.env.speechSynthesis[key] = true; await rejectCode(f.audio.listen(), 'BUSY'); await rejectCode(f.audio.speak('읽기'), 'BUSY');
    assert.equal(f.recognitions.length + f.utterances.length, 0); assert.equal(f.env.speechSynthesis.cancels, 0);
  }
});
test('dispose aborts once and future calls reject without starting audio', async () => {
  const f = fixture(), p = f.audio.listen(), rejected = rejectCode(p, 'ABORTED'); f.audio.dispose(); f.audio.dispose(); await rejected;
  await rejectCode(f.audio.listen(), 'DISPOSED'); await rejectCode(f.audio.speak('읽기'), 'DISPOSED'); assert.equal(f.recognitions[0].aborts, 1);
});
test('invalid text and duration configuration do not start any IO', async () => {
  const f = fixture(); for (const text of ['', '  ', null, 42]) await rejectCode(f.audio.speak(text), 'INVALID_TEXT');
  for (const options of [{recognitionMs: 0}, {recognitionMs: 8001}, {recognitionMs: NaN}, {ttsMs: -1}, {ttsMs: Infinity}, {ttsMs: 60001}]) {
    assert.throws(() => createCriticalConversationAudio({windowRef: f.env, ...options}), error => error.code === 'INVALID_OPTIONS');
  }
  assert.equal(f.utterances.length + f.recognitions.length + f.timers.size, 0);
});
test('webkit recognition fallback works without a TTS constructor', async () => {
  const f = fixture(); f.env.webkitSpeechRecognition = f.env.SpeechRecognition; delete f.env.SpeechRecognition; delete f.env.SpeechSynthesisUtterance;
  assert.deepEqual(f.audio.capabilities(), {tts: false, browser: true}); const p = f.audio.listen();
  f.result(0, [{text: '다음 단계'}]); f.recognitions[0].onend(); assert.equal((await p).text, '다음 단계');
});
test('onState rendering error cannot strand a listening operation', async () => {
  const f = fixture({onState: () => { throw new Error('mock render'); }}), p = f.audio.listen();
  f.result(0, [{text: '네'}]); f.recognitions[0].onend(); assert.equal((await p).text, '네'); assert.equal(f.timers.size, 0);
});
test('abort from onState prevents the scheduled recognition start', async () => {
  const f = fixture({onState: (state, audio) => { if (state.phase === 'listening') audio.abort(); }});
  await rejectCode(f.audio.listen(), 'ABORTED'); assert.equal(f.recognitions[0].starts, 0); assert.equal(f.recognitions[0].aborts, 1); assert.equal(f.timers.size, 0);
});
test('custom shorter timeouts are bounded and use the injected timer clock', async () => {
  const f = fixture({audio: {recognitionMs: 20, ttsMs: 50}}), p = f.audio.speak('읽기'), rejected = rejectCode(p, 'TTS_TIMEOUT');
  f.tick(50); await rejected; const q = f.audio.listen(), rejectedQ = rejectCode(q, 'RECOGNITION_TIMEOUT');
  f.tick(520); await rejectedQ; assert.equal(f.timers.size, 0);
});
