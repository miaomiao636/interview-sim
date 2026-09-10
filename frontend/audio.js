/* Audio primitives shared by the recorder and the streaming player. */
(function (root) {
  class PcmPlayer {
    constructor(context, sampleRate, sources = new Set()) {
      this.context = context;
      this.sampleRate = sampleRate;
      this.sources = sources;
      this.pending = new Uint8Array(0);
      this.nextTime = 0;
      this.started = false;
    }
    push(bytes, final = false) {
      const joined = new Uint8Array(this.pending.length + bytes.length);
      joined.set(this.pending);
      joined.set(bytes, this.pending.length);
      this.pending = joined;
      // Start with 600 ms of audio; batch subsequent small network fragments.
      const threshold = this.sampleRate * 2 * (this.started ? 0.12 : 0.6);
      if (!final && joined.length < threshold) return;
      const length = joined.length - joined.length % 2;
      if (!length) return;
      const view = new DataView(joined.buffer, joined.byteOffset, length);
      const buffer = this.context.createBuffer(1, length / 2, this.sampleRate);
      const samples = buffer.getChannelData(0);
      for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
      const source = this.context.createBufferSource();
      source.buffer = buffer;
      source.connect(this.context.destination);
      source.addEventListener('ended', () => this.sources.delete(source));
      this.sources.add(source);
      // Rebase after an underrun. Past start times otherwise overlap queued chunks.
      const at = Math.max(this.nextTime, this.context.currentTime + 0.08);
      source.start(at);
      this.nextTime = at + buffer.duration;
      this.started = true;
      this.pending = joined.slice(length);
    }
  }

  function mergeSamples(chunks) {
    const output = new Float32Array(chunks.reduce((sum, part) => sum + part.length, 0));
    let offset = 0;
    for (const part of chunks) { output.set(part, offset); offset += part.length; }
    return output;
  }

  function resample(samples, fromRate, toRate = 16000) {
    if (fromRate === toRate) return samples;
    const output = new Float32Array(Math.floor(samples.length * toRate / fromRate));
    const ratio = fromRate / toRate;
    for (let i = 0; i < output.length; i++) {
      const left = Math.floor(i * ratio);
      const right = Math.min(samples.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      for (let j = left; j < right; j++) sum += samples[j];
      output[i] = sum / Math.max(1, right - left);
    }
    return output;
  }
  root.InterviewAudio = { PcmPlayer, mergeSamples, resample };
  if (typeof module !== 'undefined') module.exports = root.InterviewAudio;
})(typeof window === 'undefined' ? globalThis : window);
