const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const RAW = '我，我，嗯，也也参与接口对接。';
const preferenceKey = 'interview-sim-auto-voice-cleanup';
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const elements = new Map(), requests = [], answers = [], intervals = [];
  const saved = new Map([[preferenceKey, 'true'], ['unrelated-preference', 'keep']]);
  let sequence = 0;
  function element(id) {
    if (!elements.has(id)) elements.set(id, {value: '', textContent: '', checked: false, disabled: false, open: false,
      classList: {toggle() {}, add() {}, remove() {}}, handlers: {},
      addEventListener(name, handler) { this.handlers[name] = handler; },
      setAttribute() {}, focus() {}, replaceChildren() {}, close() {}, remove() {}});
    return elements.get(id);
  }
  const context = {
    AbortController, Float32Array, ArrayBuffer, DataView, Uint8Array, TextDecoder, Response,
    setTimeout, clearTimeout, clearInterval() {}, setInterval(fn) { intervals.push(fn); return intervals.length; },
    crypto: {randomUUID: () => `synthetic-operation-${++sequence}`},
    btoa: value => Buffer.from(value, 'binary').toString('base64'), confirm: () => true,
    document: {getElementById: element, addEventListener() {}},
    localStorage: {getItem: key => saved.get(key) || null, setItem: (key, value) => saved.set(key, value), removeItem: key => saved.delete(key)},
    navigator: {mediaDevices: {getUserMedia: async () => ({getTracks: () => [{stop() {}}]})}},
    AudioContext: class {
      sampleRate = 16000;
      audioWorklet = {addModule: async () => {}};
      async resume() {}
      async close() {}
      createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
    },
    AudioWorkletNode: class {
      port = {postMessage: () => this.port.onmessage({data: {stopped: true}})};
      connect() {}
      disconnect() {}
    },
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, resolve, reject})),
    recordAnswer: body => answers.push(body),
  };
  context.window = context;
  vm.createContext(context);
  for (const name of ['audio', 'voice', 'app']) {
    const filename = path.join(__dirname, '../frontend', name + '.js');
    vm.runInContext(fs.readFileSync(filename, 'utf8'), context);
  }
  const run = code => vm.runInContext(code, context);
  run(`
    stopSpeaking = () => {};
    stopTimer = () => {};
    skipQuestion = () => {};
    renderLiveSession = () => {};
    showToast = () => {};
    addSystemMessage = () => ({remove() {}});
    streamQuestionRequest = async (context, path, body) => { recordAnswer(body); await new Promise(() => {}); };
    state.sessionId = 'synthetic-session';
    state.serviceReady = true;
    applyActiveQuestion({question_id: 'q-1', attempt: 1, question: '合成问题'});
    bindInterview();
  `);
  return {element, requests, answers, intervals, saved, run};
}

async function recordAndStop(f, {finishBeforeStop = false} = {}) {
  await f.run('startRecording()');
  assert.equal(f.run('state.voice instanceof InterviewVoice'), true);
  // Inject PCM at the real AudioWorklet message boundary, not into a draft.
  f.run('state.voice.node.port.onmessage({data: {samples: new Float32Array(4000).fill(0.1)}})');
  if (finishBeforeStop) {
    f.intervals[0]();
    f.requests[0].resolve(new Response(RAW));
    await tick();
    assert.equal(f.run('state.voice.recording'), true, 'a pause or ASR result must not stop recording');
  }
  await f.run('stopRecording()');
  if (!finishBeforeStop) {
    assert.equal(f.element('btn-send-voice').disabled, true, 'wait for the last ASR chunk before confirmation');
    f.requests[0].resolve(new Response(RAW));
    await tick();
  }
  await tick();
}

test('retired auto-cleanup preference is removed without touching other preferences', () => {
  const f = fixture();
  assert.equal(f.saved.has(preferenceKey), false);
  assert.equal(f.saved.get('unrelated-preference'), 'keep');
});

for (const finishBeforeStop of [false, true]) {
  test(`real Voice leaves ASR verbatim and never auto-sends (ASR finishes ${finishBeforeStop ? 'before' : 'after'} stop)`, async () => {
    const f = fixture();
    await recordAndStop(f, {finishBeforeStop});
    assert.equal(f.element('voice-transcript').value, RAW);
    assert.equal(f.element('subtitle-text').textContent, RAW);
    assert.deepEqual(f.requests.map(request => request.url), ['/api/transcribe/stream']);
    assert.equal(f.answers.length, 0);
    assert.equal(f.element('btn-send-voice').disabled, false);
    void f.element('btn-send-voice').handlers.click();
    assert.equal(f.answers.length, 1);
    assert.equal(f.answers[0].asr_text, RAW);
    assert.equal(Object.hasOwn(f.answers[0], 'voice_input'), false);
  });
}

test('manual ASR corrections survive UI refresh and are submitted only on confirmation', async () => {
  const f = fixture();
  await recordAndStop(f);
  const corrected = '我，我，嗯，也也参与接口联调。';
  f.element('voice-transcript').value = corrected;
  f.element('voice-transcript').handlers.input();
  f.run('syncVoiceUI()');
  assert.equal(f.element('voice-transcript').value, corrected);
  assert.equal(f.answers.length, 0);
  void f.element('btn-send-voice').handlers.click();
  assert.equal(f.answers[0].asr_text, corrected);
  assert.equal(Object.hasOwn(f.answers[0], 'voice_input'), false);
});

for (const change of ['clearAnswerDrafts()', "applyActiveQuestion({question_id: 'q-2', attempt: 1, question: '新问题'})"]) {
  test(`late ASR callbacks cannot refill the draft after ${change}`, async () => {
    const f = fixture();
    await f.run('startRecording()');
    f.run('state.voice.node.port.onmessage({data: {samples: new Float32Array(4000).fill(0.1)}})');
    await f.run('stopRecording()');
    f.run(change);
    f.requests[0].resolve(new Response(RAW));
    await tick();
    assert.equal(f.element('voice-transcript').value, '');
    assert.equal(f.answers.length, 0);
  });
}

test('HTML has no cleanup controls/module and versions every script and stylesheet consistently', () => {
  const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
  assert.equal(fs.existsSync(path.join(__dirname, '../frontend/voice-cleanup.js')), false);
  assert.doesNotMatch(html, /voice-cleanup\.js|btn-cleanup-voice|auto-cleanup-voice|保真整理/);
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^\"]+\.(?:js|css)(?:\?[^\"]*)?)"/g)].map(match => match[1]);
  assert.ok(assets.length >= 8);
  for (const asset of assets) assert.match(asset, /\?v=voice-raw-20260922$/);
});
