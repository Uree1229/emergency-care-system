import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { createNoiseSuppressor, encodeMonoWav, NOISE_SUPPRESSION_LIMITS } from './paramedic-ui/noise-suppression.mjs';
import createRawModule from './paramedic-ui/vendor/jitsi-rnnoise-wasm-0.2.1/rnnoise-sync.mjs';

// These are deterministic synthetic waveforms, not recordings or ASR accuracy data.
const processor = await createNoiseSuppressor();
after(() => processor.dispose());
const reject = (promise, code) => assert.rejects(promise, error => error.code === code);
const rms = values => Math.sqrt(values.reduce((sum, value) => sum + value * value, 0) / values.length);
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const noise = (length, amplitude = 0.08) => {
  const values = new Float32Array(length);
  let state = 123;
  for (let index = 0; index < length; index += 1) {
    state = (Math.imul(state, 1664525) + 1013904223) >>> 0;
    values[index] = (state / 4294967296 * 2 - 1) * amplitude;
  }
  return values;
};
const peak = values => {
  let max = -1, index = -1;
  values.forEach((value, offset) => { if (Math.abs(value) > max) { max = Math.abs(value); index = offset; } });
  return index;
};
async function rawFrames(source, extraFrames = 2) {
  const module = await createRawModule().ready;
  const state = module._rnnoise_create(0);
  const input = module._malloc(1920), output = module._malloc(1920);
  const frames = Math.ceil(source.length / 480) + extraFrames;
  const values = new Float32Array(frames * 480);
  try {
    for (let frame = 0; frame < frames; frame += 1) {
      for (let offset = 0; offset < 480; offset += 1) {
        module.HEAPF32[(input >>> 2) + offset] = (source[frame * 480 + offset] ?? 0) * 32768;
      }
      module._rnnoise_process_frame(state, output, input);
      for (let offset = 0; offset < 480; offset += 1) values[frame * 480 + offset] = module.HEAPF32[(output >>> 2) + offset] / 32768;
    }
    return values;
  } finally {
    module._free(input); module._free(output); module._rnnoise_destroy(state);
  }
}

test('the bound explicitly separates decoded Float32 size from an encoded file size', () => {
  assert.deepEqual(NOISE_SUPPRESSION_LIMITS, { sampleRate: 48000, frameSize: 480, maxDurationSeconds: 180, maxSamples: 8640000, maxPcmBytes: 34560000, algorithmicDelaySamples: 960, flushFrames: 2 });
  assert.equal(NOISE_SUPPRESSION_LIMITS.maxSamples * 4, NOISE_SUPPRESSION_LIMITS.maxPcmBytes);
  assert.ok(Object.isFrozen(NOISE_SUPPRESSION_LIMITS));
});

test('vendored sync artifact and all upstream source/license files match pinned SHA256 values', async () => {
  const directory = new URL('./paramedic-ui/vendor/jitsi-rnnoise-wasm-0.2.1/', import.meta.url);
  const provenance = JSON.parse(await readFile(new URL('PROVENANCE.json', directory), 'utf8'));
  assert.equal(provenance.version, '0.2.1');
  assert.equal(provenance.gitCommit, 'cb529a59a8478fe604e57986fc96afdaecfa6fb7');
  assert.equal(provenance.rnnoiseCommit, '372f7b4b76cde4ca1ec4605353dd17898a99de38');
  assert.equal(provenance.files['rnnoise-sync.mjs'].sha256, '05a553f523d59502d133a6d05dbf1878137c9e7bcff06edf5561f7001b62f95f');
  for (const [name, expected] of Object.entries(provenance.files)) {
    const bytes = await readFile(new URL(name, directory));
    assert.equal(bytes.byteLength, expected.bytes, name);
    assert.equal(sha(bytes), expected.sha256, name);
  }
  assert.match(await readFile(new URL('LICENSE.apache-2.0.txt', directory), 'utf8'), /Apache License/);
  assert.match(await readFile(new URL('COPYING.rnnoise.txt', directory), 'utf8'), /Redistribution and use in source and binary forms/);
});

test('actual embedded WASM bytes match an independent pinned fingerprint', async () => {
  const source = await readFile(new URL('./paramedic-ui/vendor/jitsi-rnnoise-wasm-0.2.1/rnnoise-sync.mjs', import.meta.url), 'utf8');
  const bytes = Buffer.from(source.match(/data:application\/octet-stream;base64,([A-Za-z0-9+/=]+)/)[1], 'base64');
  assert.equal(bytes.length, 1440118);
  assert.equal(sha(bytes), '4f513a50613de74378331237886138eab52fa2650e8b1a41eb587d932d9b8850');
  assert.deepEqual([...bytes.subarray(0, 4)], [0, 97, 115, 109]);
});

test('a synthetic impulse verifies the pinned engine delay before adapter compensation', async () => {
  const values = new Float32Array(9600); values[4873] = 0.61;
  const actual = await rawFrames(values);
  assert.equal(peak(actual), 5833);
  assert.equal(peak(actual) - 4873, 960);
  assert.equal(Math.max(...actual.subarray(0, 4800).map(Math.abs)), 0);
  const aligned = await processor.process(values);
  assert.equal(peak(aligned.pcm), 4873);
});

for (const count of [1, 73, 479, 480, 481, 959, 960, 961, 1373, 4800]) {
  test(`output and WAV have exactly ${count} samples including partial frames`, async () => {
    const input = noise(count), before = input.slice();
    const result = await processor.process(input);
    assert.deepEqual(input, before);
    assert.equal(result.pcm.length, count);
    assert.equal(result.wav.type, 'audio/wav');
    assert.equal(result.wav.size, 44 + count * 2);
    assert.equal(result.descriptor.inputSamples, count);
    assert.equal(result.descriptor.outputSamples, count);
    assert.equal(result.descriptor.inputFrameCount, Math.ceil(count / 480));
    assert.equal(result.descriptor.processedFrameCount, Math.ceil(count / 480) + 2);
    assert.equal(result.descriptor.tailPaddingSamples, Math.ceil(count / 480) * 480 - count);
    assert.ok(result.pcm.every(Number.isFinite));
    assert.ok(result.pcm.every(value => value >= -1 && value <= 1));
  });
}

for (const count of [480, 481, 960, 1021]) {
  test(`independent direct WASM frames establish tail flush, scale and alignment for ${count}`, async () => {
    const input = noise(count, 0.4), direct = await rawFrames(input);
    const expected = direct.slice(960, 960 + count).map(value => Math.max(-1, Math.min(1, value)));
    const result = await processor.process(input);
    assert.deepEqual(result.pcm, expected);
  });
}

test('end-of-file impulse survives flush rather than being silently dropped', async () => {
  const input = new Float32Array(5003); input[5002] = 0.6;
  const result = await processor.process(input);
  assert.ok(Math.abs(result.pcm[5002]) > 0.00001);
  assert.equal(peak(result.pcm), 5002);
});

test('actual engine overshoot is clipped only at output and counted against direct WASM samples', async () => {
  const input = Float32Array.from({ length: 48000 }, (_, index) => index % 48 < 24 ? 1 : -1);
  const direct = (await rawFrames(input)).slice(960, 48960);
  const expectedClips = direct.reduce((count, value) => count + Number(value < -1 || value > 1), 0);
  assert.ok(expectedClips > 0);
  const result = await processor.process(input);
  assert.equal(result.descriptor.outputClippedSamples, expectedClips);
  assert.deepEqual(result.pcm, direct.map(value => Math.max(-1, Math.min(1, value))));
  assert.equal(result.pcm.some(value => Math.abs(value) === 1), true);
});

test('real RNNoise changes and attenuates deterministic stationary noise without claiming speech quality', async () => {
  const input = noise(48000), result = await processor.process(input);
  assert.notDeepEqual(result.pcm, input);
  assert.ok(rms(result.pcm) < rms(input) * 0.25);
  assert.ok(rms(result.pcm) > 0);
  assert.equal(result.descriptor.engine, 'RNNoise');
  assert.equal(result.descriptor.model, 'bundled-default-rnnoise-0.2');
  assert.equal('accuracy' in result.descriptor, false);
  assert.equal('speechRecognized' in result.descriptor, false);
});

test('fresh state on every process prevents preceding patient/audio adaptation leakage', async () => {
  const input = noise(9600);
  const first = await processor.process(input);
  await processor.process(new Float32Array(4800).fill(0.5));
  const again = await processor.process(input);
  assert.deepEqual(first.pcm, again.pcm);
  assert.deepEqual(first.descriptor, again.descriptor);
});

test('silence stays zero and records delay compensation honestly', async () => {
  const result = await processor.process(new Float32Array(481));
  assert.ok(result.pcm.every(value => value === 0));
  assert.equal(result.descriptor.algorithmicDelaySamples, 960);
  assert.equal(result.descriptor.delayCompensationSamples, 960);
  assert.equal(result.descriptor.flushFrames, 2);
  assert.equal(result.descriptor.inputScale, 32768);
  assert.equal(result.descriptor.outputClippedSamples, 0);
  assert.equal(result.descriptor.wasmSha256, '4f513a50613de74378331237886138eab52fa2650e8b1a41eb587d932d9b8850');
  assert.deepEqual(JSON.parse(JSON.stringify(result.descriptor)), result.descriptor);
});

for (const input of [undefined, null, [], [0, 1], new Float64Array([0]), new Int16Array([0]), new Float32Array()]) {
  test(`wrong or empty PCM is rejected: ${input?.constructor?.name ?? String(input)}`, async () => {
    await reject(processor.process(input), 'INVALID_PCM');
    assert.throws(() => encodeMonoWav(input), error => error.code === 'INVALID_PCM');
  });
}
for (const value of [NaN, Infinity, -Infinity]) {
  test(`nonfinite PCM ${String(value)} cannot reach WASM or WAV`, async () => {
    const input = new Float32Array([0, value]);
    await reject(processor.process(input), 'INVALID_PCM');
    assert.throws(() => encodeMonoWav(input), error => error.code === 'INVALID_PCM');
  });
}
for (const value of [-1.0001, 1.0001, 32768]) {
  test(`out of range PCM ${value} is never normalized silently`, async () => {
    const input = new Float32Array([value]);
    await reject(processor.process(input), 'PCM_OUT_OF_RANGE');
    assert.throws(() => encodeMonoWav(input), error => error.code === 'PCM_OUT_OF_RANGE');
  });
}
for (const sampleRate of [16000, 44100, 96000, '48000', NaN, null]) {
  test(`sample rate ${String(sampleRate)} requires explicit resampling outside the core`, async () => {
    await reject(processor.process(new Float32Array([0]), { sampleRate }), 'INVALID_SAMPLE_RATE');
    assert.throws(() => encodeMonoWav(new Float32Array([0]), sampleRate), error => error.code === 'INVALID_SAMPLE_RATE');
  });
}
for (const yieldEveryFrames of [0, 101, 1.5, NaN, '10', null]) {
  test(`invalid yield interval ${String(yieldEveryFrames)} rejects before module creation`, async () => {
    await reject(createNoiseSuppressor({ yieldEveryFrames }), 'INVALID_OPTIONS');
  });
}

test('duration overflow is rejected with no truncation, preserving the input', async () => {
  const input = new Float32Array(8640001); input[input.length - 1] = 0.25;
  await reject(processor.process(input), 'INPUT_TOO_LARGE');
  assert.equal(input[input.length - 1], 0.25);
  assert.throws(() => encodeMonoWav(input), error => error.code === 'INPUT_TOO_LARGE');
});

test('WAV has canonical mono PCM16 headers and correctly signed endpoints without changing input', async () => {
  const input = new Float32Array([-1, -0.5, 0, 0.5, 1]), before = input.slice();
  const wav = encodeMonoWav(input), buffer = await wav.arrayBuffer(), view = new DataView(buffer);
  assert.equal(new TextDecoder().decode(buffer.slice(0, 4)), 'RIFF');
  assert.equal(new TextDecoder().decode(buffer.slice(8, 12)), 'WAVE');
  assert.equal(view.getUint32(4, true), 46);
  assert.equal(view.getUint16(20, true), 1);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint32(28, true), 96000);
  assert.equal(view.getUint16(32, true), 2);
  assert.equal(view.getUint16(34, true), 16);
  assert.equal(view.getUint32(40, true), 10);
  assert.deepEqual([0, 1, 2, 3, 4].map(index => view.getInt16(44 + 2 * index, true)), [-32768, -16384, 0, 16384, 32767]);
  assert.deepEqual(input, before);
});

test('detached PCM is rejected as empty instead of reading inaccessible samples', async () => {
  const input = new Float32Array(2); structuredClone(input.buffer, { transfer: [input.buffer] });
  await reject(processor.process(input), 'INVALID_PCM');
});

test('pre-aborted signal rejects before a result or progress is reported', async () => {
  const controller = new AbortController(); controller.abort(); let calls = 0;
  await assert.rejects(processor.process(noise(480), { signal: controller.signal, onProgress() { calls += 1; } }), error => error.code === 'ABORTED' && error.name === 'AbortError');
  assert.equal(calls, 0);
});

test('cooperative yielding lets an unrelated browser task cancel before the first frame', async () => {
  const controller = new AbortController();
  const pending = processor.process(noise(48000), { signal: controller.signal });
  setTimeout(() => controller.abort(), 0);
  await reject(pending, 'ABORTED');
});

test('cancellation during frame processing returns no partial successful result and the processor remains reusable', async () => {
  const controller = new AbortController(), counts = [];
  await reject(processor.process(noise(96000), { signal: controller.signal, onProgress(value) { counts.push(value.framesProcessed); controller.abort(); } }), 'ABORTED');
  assert.deepEqual(counts, [100]);
  assert.equal((await processor.process(new Float32Array(480))).pcm.length, 480);
});

test('cancellation at final progress still cannot publish a successful WAV/result', async () => {
  const controller = new AbortController(), input = noise(481), original = input.slice();
  await reject(processor.process(input, { signal: controller.signal, onProgress(value) { assert.equal(value.ratio, 1); controller.abort(); } }), 'ABORTED');
  assert.deepEqual(input, original);
});

test('disposal during processing cancels work and blocks all later runs', async () => {
  const own = await createNoiseSuppressor({ yieldEveryFrames: 1 });
  await reject(own.process(noise(4800), { onProgress() { own.dispose(); } }), 'DISPOSED');
  own.dispose();
  await reject(own.process(noise(480)), 'DISPOSED');
});

test('disposing an idle processor is idempotent and does not affect another processor', async () => {
  const own = await createNoiseSuppressor(); own.dispose(); own.dispose();
  await reject(own.process(noise(480)), 'DISPOSED');
  assert.equal((await processor.process(new Float32Array(1))).pcm[0], 0);
});

test('concurrent runs on one processor are rejected rather than sharing recurrent state', async () => {
  const first = processor.process(noise(4800));
  await reject(processor.process(noise(480)), 'BUSY');
  await first;
});

test('input is snapshotted before the first yield and never written by the processor', async () => {
  const input = noise(4800), initial = input.slice();
  const expected = await processor.process(initial);
  const pending = processor.process(input); input.fill(0);
  const actual = await pending;
  assert.deepEqual(actual.pcm, expected.pcm);
  assert.ok(input.every(value => value === 0));
});

test('progress is bounded, monotonically complete, and contains no waveform or private data', async () => {
  const own = await createNoiseSuppressor({ yieldEveryFrames: 2 }), progress = [];
  try {
    await own.process(noise(960), { onProgress(value) { progress.push(value); } });
    assert.deepEqual(progress, [{ framesProcessed: 2, totalFrames: 4, ratio: 0.5 }, { framesProcessed: 4, totalFrames: 4, ratio: 1 }]);
  } finally { own.dispose(); }
});

test('callback failure frees all recurrent state and allows a clean retry', async () => {
  await reject(processor.process(noise(480), { onProgress() { throw Error('synthetic listener failure'); } }), 'PROCESS_FAILED');
  const again = await processor.process(new Float32Array(480));
  assert.ok(again.pcm.every(value => value === 0));
});

test('invalid callback does not lock the processor', async () => {
  await reject(processor.process(noise(480), { onProgress: true }), 'INVALID_OPTIONS');
  await processor.process(new Float32Array(1));
});

test('real WASM processing does not call global fetch or require an external asset request', async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = () => { calls += 1; throw Error('network forbidden in local processing test'); };
  try {
    const own = await createNoiseSuppressor();
    try { assert.equal((await own.process(noise(480))).pcm.length, 480); } finally { own.dispose(); }
    assert.equal(calls, 0);
  } finally { globalThis.fetch = original; }
});

test('maximum advertised 180 seconds is actually processable without silent truncation', async () => {
  const input = new Float32Array(8640000);
  const result = await processor.process(input);
  assert.equal(result.pcm.length, 8640000);
  assert.equal(result.wav.size, 17280044);
  assert.equal(result.descriptor.durationMs, 180000);
  assert.equal(result.descriptor.processedFrameCount, 18002);
  assert.equal(result.descriptor.tailPaddingSamples, 0);
  assert.ok(result.pcm.every(value => value === 0));
});
