const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const InterviewAudio = require('../frontend/audio.js');

function fixture() {
  const texts = [], requests = [], intervals = [];
  let failed = false, released = false;
  const context = {
    InterviewAudio, AbortController, Float32Array, Math,
    navigator: {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop() { released = true; }}]})}},
    AudioContext: class {
      sampleRate = 16000;
      audioWorklet = {addModule: async () => {}};
      resume = async () => {};
      close = async () => {};
      createMediaStreamSource = () => ({connect() {}, disconnect() {}});
    },
    AudioWorkletNode: class {
      port = {postMessage: () => this.port.onmessage({data: {stopped: true}})};
      connect() {}
      disconnect() {}
    },
    setInterval: fn => { intervals.push(fn); return intervals.length; }, clearInterval() {},
    setTimeout, clearTimeout,
    encodeWav: () => new ArrayBuffer(44), arrayBufferToBase64: () => 'synthetic-audio',
    responseError: async () => 'test failure',
    fetch: async url => { requests.push(url); return {ok: !failed}; },
    readTextStream: async (_, onChunk) => { onChunk('测试'); await Promise.resolve(); onChunk('回答'); },
  };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require.resolve('../frontend/voice.js'), 'utf8') + '\nthis.Voice = InterviewVoice;', context);
  const voice = new context.Voice({onText: text => texts.push(text), onStatus() {}, onState() {}});
  return {voice, texts, requests, intervals, fail: value => {failed = value;}, released: () => released};
}
const settle = () => new Promise(resolve => setImmediate(resolve));

test('live text appears before stop; silence never stops or submits an answer', async () => {
  const f = fixture();
  await f.voice.start('zh');
  f.voice.samples.push(new Float32Array(16000).fill(.1));
  f.intervals[0]();
  await settle();
  assert.ok(f.texts.includes('测试'));
  assert.equal(f.texts.at(-1), '测试回答');
  assert.equal(f.voice.recording, true);
  f.voice.samples.push(new Float32Array(16000));
  f.intervals[0]();
  await settle();
  assert.equal(f.voice.recording, true);
  assert.equal(f.requests.length, 1);
  await f.voice.stop();
  assert.equal(f.voice.recording, false);
  assert.equal(f.released(), true);
  assert.ok(f.requests.every(url => url === '/api/transcribe/stream'));
});

test('failed audio survives stopping and retry, without auto-sending', async () => {
  const f = fixture();
  await f.voice.start('zh');
  f.fail(true);
  f.voice.samples.push(new Float32Array(16000).fill(.1));
  f.intervals[0]();
  await settle();
  assert.equal(f.voice.failed, true);
  assert.equal(f.voice.queue.length, 1);
  await f.voice.stop();
  assert.equal(f.voice.queue.length, 1);
  f.fail(false);
  await f.voice.retry();
  assert.equal(f.voice.failed, false);
  assert.equal(f.voice.queue.length, 0);
  assert.equal(f.texts.at(-1), '测试回答');
  assert.equal(f.requests.length, 2);
});

test('manual stop flushes the final piece before releasing capture', async () => {
  const f = fixture();
  await f.voice.start('zh');
  f.voice.node.port.postMessage = () => {
    f.voice.node.port.onmessage({data: {samples: new Float32Array(8000).fill(.1)}});
    f.voice.node.port.onmessage({data: {stopped: true}});
  };
  await f.voice.stop();
  await settle();
  assert.equal(f.requests.length, 1);
  assert.equal(f.voice.queue.length, 0);
  assert.equal(f.texts.at(-1), '测试回答');
});
