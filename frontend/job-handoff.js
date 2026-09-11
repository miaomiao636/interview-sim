/* One user-opened window, one job draft. No cross-origin HTTP or model calls. */
(function (root) {
  'use strict';
  const LIMITS = { company: 160, target_role: 160, city: 80, salary: 100, education: 80, jd: 15000 };

  function isLocalOrigin(value) {
    if (typeof value !== 'string' || !/^http:\/\/(127\.0\.0\.1|localhost):[1-9]\d{0,4}$/.test(value)) return false;
    try { return new URL(value).origin === value; } catch (_) { return false; }
  }

  function exactKeys(value, keys) {
    return value !== null && typeof value === 'object' && !Array.isArray(value)
      && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
  }

  function validateMessage(value) {
    if (!exactKeys(value, ['type', 'version', 'job']) || value.type !== 'interview-sim:job' || value.version !== 1) return null;
    if (!exactKeys(value.job, Object.keys(LIMITS))) return null;
    for (const [key, limit] of Object.entries(LIMITS)) {
      if (typeof value.job[key] !== 'string' || value.job[key].length > limit) return null;
    }
    if (!value.job.target_role.trim() || !value.job.jd.trim()) return null;
    return Object.fromEntries(Object.keys(LIMITS).map(key => [key, value.job[key]]));
  }

  function receive(win, onJob, onError) {
    const url = new URL(win.location.href);
    if (url.searchParams.get('import') !== 'job') return false;
    const origin = url.searchParams.get('source');
    const validParams = [...url.searchParams.keys()].length === 2
      && url.searchParams.getAll('import').length === 1 && url.searchParams.getAll('source').length === 1;
    // The URL carries routing metadata only; remove it before a refresh or copy.
    url.searchParams.delete('import'); url.searchParams.delete('source');
    win.history.replaceState(null, '', url.pathname + url.search + url.hash);
    const opener = win.opener;
    let timer = null, closed = false;
    function cleanup() {
      closed = true;
      win.removeEventListener('message', handleMessage);
      win.removeEventListener('pagehide', cleanup);
      if (timer !== null) win.clearTimeout(timer);
      win.opener = null;
    }
    function fail(message) { cleanup(); onError(message); }
    function handleMessage(event) {
      if (closed || event.origin !== origin || event.source !== opener) return;
      const job = validateMessage(event.data);
      if (!job) { fail('岗位资料格式不兼容，请回到来源工作台导出 JD Markdown 后手动导入。'); return; }
      if (onJob(job) === false) { fail('当前有正在进行的操作或未保存内容，未导入岗位。请结束当前操作后重新打开。'); return; }
      opener.postMessage({ type: 'interview-sim:received', version: 1 }, origin);
      cleanup();
    }
    if (!validParams || !isLocalOrigin(origin) || !isLocalOrigin(win.location.origin) || origin === win.location.origin || !opener) {
      fail('岗位导入入口无效。请从可信的本地工作台重新打开，或手动导入 JD 文件。');
      return true;
    }
    win.addEventListener('message', handleMessage);
    win.addEventListener('pagehide', cleanup);
    timer = win.setTimeout(() => fail('未收到岗位资料，请回到来源工作台重试，或导出 JD Markdown 后手动导入。'), 30000);
    opener.postMessage({ type: 'interview-sim:ready', version: 1 }, origin);
    return true;
  }

  const api = { isLocalOrigin, validateMessage, receive };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.InterviewJobHandoff = api;
})(typeof window !== 'undefined' ? window : globalThis);
