/* Poll a durable local job; no browser request is held for several minutes. */
function renderReportLoading() {
  $('report-content').innerHTML = `<section class="surface report-progress" aria-live="polite"><p class="eyebrow">正在生成报告</p><h2 id="report-stage">准备分析</h2><progress id="report-stage-progress" max="1" value="0" aria-label="已完成分析阶段"></progress><p id="report-count">正在读取进度…</p><div class="report-time"><strong id="report-estimate">正在估计耗时…</strong><span id="report-elapsed">已等待 0 秒</span></div><p id="report-estimate-basis" class="field-help">按题量估计，实际取决于模型速度；预计时间不是倒计时。</p><p class="field-help">每个完成的部分都会保存。可以切换页面，重新打开时继续查看进度。</p></section>`;
  getJSON(`/api/review/estimate?session_id=${encodeURIComponent(state.reportSessionId)}`).then(estimate => {
    if (!$('report-estimate')) return;
    $('report-estimate').textContent = `预计约 ${formatWait(estimate.low_seconds)}–${formatWait(estimate.high_seconds)}`;
    $('report-estimate-basis').textContent = `${estimate.basis}；按完整报告计算，重试会复用已完成部分。`;
  }).catch(() => { if ($('report-estimate')) $('report-estimate').textContent = '耗时暂无法估计，进度会持续更新'; });
}

function formatWait(seconds) {
  return seconds < 60 ? `${seconds} 秒` : `${Math.ceil(seconds / 60)} 分钟`;
}

async function followReport(sessionId, start) {
  clearTimeout(state.reportPoll);
  state.reportSessionId = sessionId;
  state.sessionId = sessionId;
  state.activeQuestionId = null;
  state.isBusy = true;
  setConversationBusy(true);
  enableView('report'); switchView('report');
  renderReportLoading();
  setReportExportReady(false);
  try {
    if (start) await postJSON('/api/review/jobs', {session_id:sessionId});
    localStorage.setItem('interview-sim-pending-report', sessionId);
    await pollReport(sessionId);
  } catch (error) {
    reportStopped();
    renderReportError(error.message || '无法连接本地服务，请恢复服务后继续生成。');
  }
}

async function pollReport(sessionId) {
  if (sessionId !== state.reportSessionId) return;
  try {
    const job = await getJSON(`/api/sessions/${encodeURIComponent(sessionId)}/review-job`);
    if (sessionId !== state.reportSessionId) return;
    if (job.status === 'completed' && job.report) {
      state.lastReport = job.report;
      reportStopped();
      localStorage.removeItem('interview-sim-pending-report');
      renderReport(job.report); setReportExportReady(true);
      if (state.view !== 'report') showToast('证据报告已生成，可以前往查看。');
      return;
    }
    if (job.status === 'failed' || job.status === 'idle') {
      reportStopped();
      renderReportError(job.error || '上次报告未完成，点击继续生成。', job);
      return;
    }
    if ($('report-stage')) {
      $('report-stage').textContent = `${job.stage}${job.retrying ? ' · 正在修正输出格式' : ''}`;
      $('report-stage-progress').max = job.total;
      $('report-stage-progress').value = job.completed;
      $('report-count').textContent = `已保存 ${job.completed} / ${job.total} 个分析阶段`;
      $('report-elapsed').textContent = `本次已等待 ${job.elapsed_seconds} 秒`;
    }
    state.reportPoll = setTimeout(() => pollReport(sessionId), 2000);
  } catch (_) {
    if ($('report-count')) $('report-count').textContent = '暂时无法连接本地服务，正在重新连接；已完成部分已保存。';
    state.reportPoll = setTimeout(() => pollReport(sessionId), 4000);
  }
}

function reportStopped() {
  clearTimeout(state.reportPoll);
  state.reportPoll = null;
  state.isBusy = false;
  setConversationBusy(false);
}

function setReportExportReady(ready) {
  for (const id of ['btn-export-md','btn-export-json','btn-print']) $(id).disabled = !ready;
}

function renderReportError(message, job={}) {
  $('report-content').innerHTML = `<section class="surface report-progress" role="status"><p class="eyebrow">报告尚未完成</p><h2>${esc(job.stage || '继续生成证据报告')}</h2><p>${esc(message)}</p><p class="field-help">${job.completed ? `已完成并保存 ${job.completed} / ${job.total} 个阶段。` : ''}原面试记录仍然保留。</p><button id="btn-retry-report" class="btn primary" type="button">继续生成报告</button><button id="btn-check-report-settings" class="btn secondary" type="button">检查分析模型设置</button></section>`;
  $('btn-retry-report').addEventListener('click', () => followReport(state.reportSessionId || state.sessionId, true));
  $('btn-check-report-settings').addEventListener('click', () => switchView('settings'));
}

async function restorePendingReport() {
  const requested = new URLSearchParams(location.search).get('session');
  if (requested && state.serviceReady) { await viewSession(requested); return; }
  const sessionId = localStorage.getItem('interview-sim-pending-report');
  if (!sessionId || !state.serviceReady) return;
  try {
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/review-job`);
    if (response.status === 404) { localStorage.removeItem('interview-sim-pending-report'); return; }
    if (response.ok) await followReport(sessionId, false);
  } catch (_) { /* The user can reopen it from training history. */ }
}
