const { test } = require('node:test');
const assert = require('node:assert/strict');
const { PcmPlayer, resample } = require('../frontend/audio.js');
function context() {
  return { currentTime: 0, destination: {}, starts: [], buffers: [],
    createBuffer(channels, size, rate) {
      const data = new Float32Array(size);
      const buffer = { duration: size / rate, getChannelData: () => data };
      this.buffers.push(buffer); return buffer;
    },
    createBufferSource() { return { connect() {}, addEventListener() {}, start: t => this.starts.push(t) }; },
  };
}
test('TTS waits for prebuffer then rebases late chunks without overlap', () => {
  const ctx = context();
  const player = new PcmPlayer(ctx, 24000);
  player.push(new Uint8Array(4800));
  assert.equal(ctx.starts.length, 0);
  ctx.currentTime = 2;
  player.push(new Uint8Array(24000));
  assert.ok(ctx.starts[0] >= 2.08);
  const firstEnd = player.nextTime;
  player.push(new Uint8Array(6000));
  assert.ok(ctx.starts[1] >= firstEnd);
  ctx.currentTime = 8;
  player.push(new Uint8Array(6000));
  assert.ok(ctx.starts[2] >= 8.08);
});
test('PCM decoding preserves samples across odd network byte boundaries', () => {
  const ctx = context();
  const player = new PcmPlayer(ctx, 24000);
  player.push(Uint8Array.from([255]));
  player.push(Uint8Array.from([127, 0, 128]), true);
  assert.deepEqual(Array.from(ctx.buffers[0].getChannelData(0)), [32767 / 32768, -1]);
});
test('short utterances flush even below the startup threshold', () => {
  const ctx = context(); const player = new PcmPlayer(ctx, 24000);
  player.push(new Uint8Array(20));
  player.push(new Uint8Array(0), true);
  assert.equal(ctx.starts.length, 1);
});
test('recorder resamples 48kHz microphone data to 16kHz', () => {
  assert.equal(resample(new Float32Array(48000), 48000).length, 16000);
});
