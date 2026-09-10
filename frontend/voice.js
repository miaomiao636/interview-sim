/* Keep audio pieces until successful transcription; submission is always explicit. */
class InterviewVoice {
  constructor({ onText, onStatus, onState }) {
    this.onText = onText;
    this.onStatus = onStatus;
    this.onState = onState;
    this.samples = [];
    this.queue = [];
    this.text = '';
    this.recording = false;
    this.processing = false;
    this.stopping = false;
    this.failed = false;
    this.elapsed = 0;
  }
  async start(language) {
    this.language = language;
    this.text = '';
    this.queue = [];
    this.samples = [];
    this.failed = false;
    this.elapsed = 0;
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      this.context = new AudioContext();
      await this.context.audioWorklet.addModule('/assets/recorder-worklet.js');
      await this.context.resume();
      this.node = new AudioWorkletNode(this.context, 'interview-recorder');
      this.node.port.onmessage = (event) => {
        if (event.data.samples) this.samples.push(event.data.samples);
        if (event.data.stopped) this.finishStop?.();
      };
      this.source = this.context.createMediaStreamSource(this.stream);
      this.source.connect(this.node);
      // Worklet output is silence; connection keeps capture processing active.
      this.node.connect(this.context.destination);
      this.recording = true;
      this.onState();
      this.onText('');
      this.onStatus('录音中 · 每约 4 秒更新转写；停顿不会结束录音');
      this.timer = setInterval(() => {
        this.elapsed += 4;
        this.enqueue();
        if (this.elapsed >= 600) { this.stop(); this.onStatus('已达到单次 10 分钟上限，请确认文字后发送'); }
      }, 4000);
    } catch (error) {
      this.release();
      throw error;
    }
  }
  enqueue() {
    if (this.samples.length) {
      const raw = InterviewAudio.mergeSamples(this.samples);
      this.samples = [];
      const samples = InterviewAudio.resample(raw, this.context.sampleRate);
      // Silence is not submitted to ASR and never ends recording.
      const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / Math.max(samples.length, 1));
      if (rms > 0.002) this.queue.push(samples);
    }
    if (!this.failed) this.drain();
  }
  async drain() {
    if (this.processing) return;
    this.processing = true;
    this.onState();
    try {
      while (this.queue.length) {
        const samples = this.queue[0];
        this.controller = new AbortController();
        const timeout = setTimeout(() => this.controller.abort(), 60000);
        let partial = '';
        try {
          const response = await fetch('/api/transcribe/stream', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_base64: arrayBufferToBase64(encodeWav(samples, 16000)), language: this.language }),
            signal: this.controller.signal,
          });
          if (!response.ok) throw new Error(await responseError(response, '识别服务暂不可用'));
          await readTextStream(response, (chunk) => {
            partial += chunk;
            this.onText(this.text + partial);
          });
          // Some providers legitimately return empty text for non-speech sounds.
          this.text += partial.trim() ? partial.trim() + ' ' : '';
          this.queue.shift();
          this.onText(this.text.trim());
        } finally { clearTimeout(timeout); }
      }
    } catch (error) {
      this.failed = true;
      this.onText(this.text.trim());
      this.onStatus('有片段识别失败，录音仍保留在当前页面。结束后可重试识别或自行填写文字。');
    } finally {
      this.processing = false;
      if (!this.failed) this.onStatus(this.recording ? '录音中 · 转写已更新，停顿不会结束录音' : '录音已结束，请检查或修改文字，再确认发送');
      this.onState();
    }
  }
  async stop() {
    if (!this.recording || this.stopping) return;
    this.recording = false;
    this.stopping = true;
    clearInterval(this.timer);
    this.onStatus('正在结束录音并整理最后的文字…');
    this.onState();
    await new Promise((resolve) => {
      const timeout = setTimeout(resolve, 1500);
      this.finishStop = () => { clearTimeout(timeout); resolve(); };
      this.node.port.postMessage('stop');
    });
    this.enqueue();
    this.release();
    this.stopping = false;
    this.onState();
  }
  async retry() {
    if (this.recording || this.processing || this.stopping) return;
    this.failed = false;
    this.onStatus('正在重试尚未识别的片段…');
    await this.drain();
  }
  release() {
    clearInterval(this.timer);
    this.stream?.getTracks().forEach(track => track.stop());
    this.source?.disconnect();
    this.node?.disconnect();
    this.context?.close().catch(() => {});
  }
}
