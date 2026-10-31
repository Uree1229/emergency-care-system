// Keep microphone processing off the UI thread. The main thread owns the 60 s limit.
class ParamedicPcmCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channels = inputs[0];
    if (!channels?.length || !channels[0]?.length) return true;
    const mono = new Float32Array(channels[0].length);
    for (const channel of channels) {
      for (let i = 0; i < mono.length; i += 1) mono[i] += channel[i] / channels.length;
    }
    this.port.postMessage(mono, [mono.buffer]);
    // Outputs remain silent; the connected output keeps the capture graph running.
    return true;
  }
}

registerProcessor('paramedic-pcm-capture', ParamedicPcmCapture);
