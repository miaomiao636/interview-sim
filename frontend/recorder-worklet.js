class InterviewRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
    this.running = true;
    this.port.onmessage = (event) => {
      if (event.data === 'stop') {
        this.running = false;
        this.flush();
        this.port.postMessage({ stopped: true });
      }
    };
  }
  flush() {
    if (this.offset) this.port.postMessage({ samples: this.buffer.slice(0, this.offset) });
    this.offset = 0;
  }
  process(inputs) {
    if (!this.running) return false;
    const samples = inputs[0]?.[0];
    if (samples) for (const value of samples) {
      this.buffer[this.offset++] = value;
      if (this.offset === this.buffer.length) this.flush();
    }
    return true;
  }
}
registerProcessor('interview-recorder', InterviewRecorder);
