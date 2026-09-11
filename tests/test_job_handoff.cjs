const test = require('node:test');
const assert = require('node:assert/strict');
const Handoff = require('../frontend/job-handoff.js');

const source = 'http://127.0.0.1:8686';
const job = { company: '示例公司', target_role: '工程师', city: '示例城市', salary: '10-15K', education: '本科', jd: '负责开发与验证。' };
const message = (value = job) => ({ type: 'interview-sim:job', version: 1, job: value });

function fixture(query = `?import=job&source=${encodeURIComponent(source)}`) {
  const listeners = new Map(), outgoing = [], received = [], errors = [], timers = new Map();
  let timerId = 0;
  const opener = { postMessage: (data, origin) => outgoing.push({ data, origin }) };
  const win = {
    location: new URL('http://127.0.0.1:8800/' + query), opener,
    history: { replaceState: (_, __, url) => { win.location = new URL(url, win.location); } },
    addEventListener: (type, callback) => listeners.set(type, callback),
    removeEventListener: type => listeners.delete(type),
    setTimeout: callback => { timers.set(++timerId, callback); return timerId; },
    clearTimeout: id => timers.delete(id),
  };
  return { win, opener, outgoing, received, errors, listeners, timers,
    start: (accept = value => received.push(value)) => Handoff.receive(win, accept, error => errors.push(error)),
    send: (data = message(), origin = source, sender = opener) => listeners.get('message')?.({ data, origin, source: sender }),
  };
}

test('only canonical loopback origins with explicit non-default ports are accepted', () => {
  for (const value of [source, 'http://localhost:65535']) assert.equal(Handoff.isLocalOrigin(value), true);
  for (const value of ['http://localhost', 'http://localhost:80', 'http://localhost:0', 'http://localhost:65536', 'http://localhost:08686', 'http://localhost:8686/', 'http://localhost:8686/path', 'http://user@localhost:8686', 'https://localhost:8686', 'http://127.1:8686', 'http://[::1]:8686', 'http://localhost.evil:8686', 'http://localhost:8686?x=1']) {
    assert.equal(Handoff.isLocalOrigin(value), false, value);
  }
});

test('ordinary visits never listen for or acknowledge incoming jobs', () => {
  const f = fixture(); f.win.location.search = '';
  assert.equal(f.start(), false);
  f.send();
  assert.equal(f.listeners.size, 0); assert.equal(f.outgoing.length, 0); assert.equal(f.received.length, 0);
});

test('requested import clears handshake parameters and receives exactly once from its opener', () => {
  const f = fixture();
  assert.equal(f.start(), true);
  assert.equal(f.win.location.search, '');
  assert.deepEqual(f.outgoing, [{ data: { type: 'interview-sim:ready', version: 1 }, origin: source }]);
  f.send(); f.send();
  assert.deepEqual(f.received, [job]);
  assert.deepEqual(f.outgoing.at(-1), { data: { type: 'interview-sim:received', version: 1 }, origin: source });
  assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0); assert.equal(f.win.opener, null);
});

test('wrong origin and same-origin unrelated windows cannot inject a job', () => {
  const f = fixture(); f.start();
  f.send(message(), 'http://127.0.0.1:8687');
  f.send(message(), source, {});
  assert.equal(f.received.length, 0);
  f.send(); assert.equal(f.received.length, 1);
});

test('missing, malformed, duplicate, self, or non-local source never starts a handshake', () => {
  for (const query of ['?import=job', '?import=job&source=https://example.com', '?import=job&source=http://127.0.0.1:8800', `?import=job&source=${source}&source=${source}`, `?import=job&source=${source}&session=old`, `?import=job&import=job&source=${source}`]) {
    const f = fixture(query); assert.equal(f.start(), true);
    assert.equal(f.listeners.size, 0); assert.equal(f.outgoing.length, 0); assert.equal(f.errors.length, 1);
  }
  const f = fixture(); f.win.opener = null; f.start(); assert.equal(f.errors.length, 1);
});

test('payload schema rejects unknown fields, missing strings, wrong versions and excessive lengths', () => {
  const invalid = [null, [], {}, {...message(), version: 2}, {...message(), extra: 'x'}, message({...job, resume: 'private'}), message({...job, api_key: 'private'}), message({...job, jd: ''}), message({...job, target_role: '  '}), message({...job, salary: 15})];
  for (const [field, limit] of Object.entries({company:160,target_role:160,city:80,salary:100,education:80,jd:15000})) invalid.push(message({...job, [field]: 'x'.repeat(limit + 1)}));
  const missing = {...job}; delete missing.city; invalid.push(message(missing));
  for (const payload of invalid) assert.equal(Handoff.validateMessage(payload), null);
  assert.deepEqual(Handoff.validateMessage(message({...job, jd:'<script>untrusted text</script>'})), {...job, jd:'<script>untrusted text</script>'});
});

test('an invalid authentic payload ends the handshake without confirming receipt', () => {
  const f = fixture(); f.start(); f.send(message({...job, resume: 'private'})); f.send();
  assert.equal(f.errors.length, 1); assert.equal(f.received.length, 0); assert.equal(f.outgoing.length, 1);
  assert.equal(f.listeners.size, 0); assert.equal(f.win.opener, null);
});

test('a busy editor can refuse the job without an acknowledgement or late overwrite', () => {
  const f = fixture(); f.start(() => false); f.send();
  assert.equal(f.outgoing.length, 1); assert.equal(f.errors.length, 1); assert.equal(f.listeners.size, 0);
});

test('timeout and leaving the page remove every listener and detach the opener', () => {
  const f = fixture(); f.start(); [...f.timers.values()][0](); f.send();
  assert.equal(f.errors.length, 1); assert.equal(f.received.length, 0); assert.equal(f.listeners.size, 0); assert.equal(f.timers.size, 0); assert.equal(f.win.opener, null);
  const other = fixture(); other.start(); other.listeners.get('pagehide')();
  assert.equal(other.listeners.size, 0); assert.equal(other.timers.size, 0); assert.equal(other.win.opener, null);
});
