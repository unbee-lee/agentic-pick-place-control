// Bound capture in the audio thread even when browser timers are throttled.
class PCMRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frames = 0;
    this.done = false;
    this.port.onmessage = ({data}) => {
      if (data === 'stop') {
        this.done = true;
        this.port.postMessage({stopped: true});
      }
    };
  }
  process(inputs) {
    if (this.done) return true;
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    const remaining = 30 * sampleRate - this.frames;
    const chunk = channel.slice(0, Math.min(channel.length, remaining));
    this.frames += chunk.length;
    this.port.postMessage({chunk}, [chunk.buffer]);
    if (this.frames >= 30 * sampleRate) {
      this.done = true;
      this.port.postMessage({limit: true});
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
