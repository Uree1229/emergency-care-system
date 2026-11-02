import createRNNoiseModule from './vendor/jitsi-rnnoise-wasm-0.2.1/rnnoise-sync.mjs';

export const NOISE_SUPPRESSION_LIMITS = Object.freeze({
  sampleRate: 48000,
  frameSize: 480,
  maxDurationSeconds: 180,
  maxSamples: 8640000,
  maxPcmBytes: 34560000,
  algorithmicDelaySamples: 960,
  flushFrames: 2,
});

const RATE = 48000;
const FRAME = 480;
const DELAY = 960;
const SCALE = 32768;
const WASM_SHA256 = '4f513a50613de74378331237886138eab52fa2650e8b1a41eb587d932d9b8850';
const yieldToBrowser = () => new Promise(resolve => setTimeout(resolve, 0));

function failure(code, message) {
  const error = new Error(message);
  error.name = code === 'ABORTED' ? 'AbortError' : 'NoiseSuppressionError';
  error.code = code;
  return error;
}

function validatePcm(pcm, sampleRate) {
  if (sampleRate !== RATE) throw failure('INVALID_SAMPLE_RATE', '48 kHz mono PCM is required.');
  if (!(pcm instanceof Float32Array) || pcm.length === 0) {
    throw failure('INVALID_PCM', 'A nonempty Float32Array is required.');
  }
  if (pcm.length > NOISE_SUPPRESSION_LIMITS.maxSamples) {
    throw failure('INPUT_TOO_LARGE', 'PCM duration exceeds 180 seconds.');
  }
  for (let index = 0; index < pcm.length; index += 1) {
    const value = pcm[index];
    if (!Number.isFinite(value)) throw failure('INVALID_PCM', 'PCM contains a nonfinite sample.');
    if (value < -1 || value > 1) throw failure('PCM_OUT_OF_RANGE', 'PCM must already be within [-1, 1].');
  }
}

// The input is never normalized or changed. PCM16 conversion uses the full signed range.
export function encodeMonoWav(pcm, sampleRate = RATE) {
  validatePcm(pcm, sampleRate);
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const view = new DataView(buffer);
  const word = (offset, text) => {
    for (let index = 0; index < text.length; index += 1) view.setUint8(offset + index, text.charCodeAt(index));
  };
  word(0, 'RIFF');
  view.setUint32(4, buffer.byteLength - 8, true);
  word(8, 'WAVE');
  word(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, RATE, true);
  view.setUint32(28, RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  word(36, 'data');
  view.setUint32(40, pcm.length * 2, true);
  for (let index = 0; index < pcm.length; index += 1) {
    const value = pcm[index];
    view.setInt16(44 + index * 2, Math.round(value < 0 ? value * 32768 : value * 32767), true);
  }
  return new Blob([buffer], { type: 'audio/wav' });
}

/**
 * Offline local RNNoise processing. Each process starts with fresh model state.
 * The pinned model has a 960-sample delay: one analysis/overlap frame and one
 * delayed-spectrum frame. Two zero frames flush it, then that delay is removed.
 * This aligns files for comparison; it does not make the model zero latency.
 */
export async function createNoiseSuppressor({ yieldEveryFrames = 100 } = {}) {
  if (!Number.isInteger(yieldEveryFrames) || yieldEveryFrames < 1 || yieldEveryFrames > 100) {
    throw failure('INVALID_OPTIONS', 'yieldEveryFrames must be an integer from 1 to 100.');
  }
  let module;
  try {
    // Embedded WASM only. The vendored sync build performs no network requests.
    module = await createRNNoiseModule().ready;
  } catch {
    throw failure('ENGINE_UNAVAILABLE', 'The local RNNoise WASM module could not be initialized.');
  }
  let disposed = false;
  let busy = false;

  return {
    async process(pcm, { sampleRate = RATE, signal, onProgress } = {}) {
      if (disposed) throw failure('DISPOSED', 'The processor has been disposed.');
      if (busy) throw failure('BUSY', 'This processor is already processing audio.');
      if (onProgress !== undefined && typeof onProgress !== 'function') {
        throw failure('INVALID_OPTIONS', 'onProgress must be a function.');
      }
      const check = () => {
        if (disposed) throw failure('DISPOSED', 'The processor was disposed while processing.');
        if (signal?.aborted) throw failure('ABORTED', 'Noise suppression was cancelled.');
      };
      check();
      validatePcm(pcm, sampleRate);
      // Snapshot before yielding so later caller mutation cannot change this run.
      const source = new Float32Array(pcm);
      busy = true;
      let state = 0;
      let inputPointer = 0;
      let outputPointer = 0;
      try {
        await yieldToBrowser();
        check();
        state = module._rnnoise_create(0);
        inputPointer = module._malloc(FRAME * 4);
        outputPointer = module._malloc(FRAME * 4);
        if (!state || !inputPointer || !outputPointer) throw failure('PROCESS_FAILED', 'RNNoise allocation failed.');
        const inputFrameCount = Math.ceil(source.length / FRAME);
        const processedFrameCount = inputFrameCount + 2;
        const output = new Float32Array(source.length);
        let clipped = 0;
        for (let frame = 0; frame < processedFrameCount; frame += 1) {
          check();
          // malloc/model creation may grow WASM memory, so read the current view.
          const inputIndex = inputPointer >>> 2;
          const outputIndex = outputPointer >>> 2;
          const heap = module.HEAPF32;
          for (let offset = 0; offset < FRAME; offset += 1) {
            const sourceIndex = frame * FRAME + offset;
            heap[inputIndex + offset] = sourceIndex < source.length ? source[sourceIndex] * SCALE : 0;
          }
          module._rnnoise_process_frame(state, outputPointer, inputPointer);
          for (let offset = 0; offset < FRAME; offset += 1) {
            const targetIndex = frame * FRAME + offset - DELAY;
            if (targetIndex < 0 || targetIndex >= output.length) continue;
            const value = module.HEAPF32[outputIndex + offset] / SCALE;
            if (!Number.isFinite(value)) throw failure('PROCESS_FAILED', 'RNNoise returned a nonfinite sample.');
            if (value < -1 || value > 1) clipped += 1;
            output[targetIndex] = Math.max(-1, Math.min(1, value));
          }
          if ((frame + 1) % yieldEveryFrames === 0 || frame + 1 === processedFrameCount) {
            onProgress?.({ framesProcessed: frame + 1, totalFrames: processedFrameCount, ratio: (frame + 1) / processedFrameCount });
            await yieldToBrowser();
            check();
          }
        }
        const descriptor = {
          schema: 1,
          engine: 'RNNoise',
          packageName: '@jitsi/rnnoise-wasm',
          packageVersion: '0.2.1',
          rnnoiseRevision: '372f7b4b76cde4ca1ec4605353dd17898a99de38',
          model: 'bundled-default-rnnoise-0.2',
          sampleRate: RATE,
          channels: 1,
          frameSize: FRAME,
          inputSamples: source.length,
          outputSamples: output.length,
          durationMs: source.length / RATE * 1000,
          inputFrameCount,
          processedFrameCount,
          tailPaddingSamples: inputFrameCount * FRAME - source.length,
          flushFrames: 2,
          algorithmicDelaySamples: DELAY,
          delayCompensationSamples: DELAY,
          delayBasis: 'pinned-source-and-wasm-impulse-peak',
          inputScale: SCALE,
          outputClippedSamples: clipped,
          wasmSha256: WASM_SHA256,
        };
        return { pcm: output, wav: encodeMonoWav(output), descriptor };
      } catch (error) {
        if (error?.code) throw error;
        throw failure('PROCESS_FAILED', 'Local RNNoise processing did not complete.');
      } finally {
        if (inputPointer) module._free(inputPointer);
        if (outputPointer) module._free(outputPointer);
        if (state) module._rnnoise_destroy(state);
        busy = false;
        if (disposed) module = null;
      }
    },
    dispose() {
      disposed = true;
      if (!busy) module = null;
    },
  };
}
