/* Every callback belongs to one session and one durable report generation. */
function isReportContext(context) {
  return context.sessionId === state.reportSessionId && context.sessionId === state.sessionId && context.sessionEpoch === state.sessionEpoch && context.epoch === state.reportEpoch && (!context.identity || context.identity === state.reportJobIdentity);
}
function reportIdentity(job) { return `${job.id || 'legacy'}:${job.generation || 0}`; }

function renderReportLoading(context) {
  $('report-content').innerHTML = `<section class="surface report-progress" aria-live="polite"><p class="eyebrow">正在生成报告</p><h2 id="report-stage">准备分析</h2><progress id="report-stage-progress" max="1" value="0" aria-label="已完成分析阶段"></progress><p id="report-count">正在读取进度…</p><div class="report-time"><strong id="report-estimate">正在估计耗时…</strong><span id="report-elapsed">已等待 0 秒</span></div><p id="report-estimate-basis" class="field-help">按题量估计，实际取决于模型速度；预计时间不是倒计时。</p><p class="field-help">每个完成的部分都会保存。可以切换页面，重新打开时继续查看进度。</p></section>`;
  getJSON(`/api/review/estimate?session_id=${encodeURIComponent(context.sessionId)}`).then(estimate => {
    if (!isReportContext(context) || !$('report-estimate')) return;
    $('report-estimate').textContent = `预计约 ${formatWait(estimate.low_seconds)}–${formatWait(estimate.high_seconds)}`;
    $('report-estimate-basis').textContent = `${estimate.basis}；按完整报告计算，重试会复用已完成部分。`;
  }).catch(() => { if (isReportContext(context) && $('report-estimate')) $('report-estimate').textContent = '耗时暂无法估计，进度会持续更新'; });
}

function formatWait(seconds) {
  return seconds < 60 ? `${seconds} 秒` : `${Math.ceil(seconds / 60)} 分钟`;
}

async function followReport(sessionId, start) {
  if (state.view === 'preparation' && !discardPreparationEdits()) return;
  invalidateSessionContext(sessionId);
  const context = {sessionId, sessionEpoch: state.sessionEpoch, epoch: state.reportEpoch, identity: null};
  state.reportSessionId = sessionId;
  state.isBusy = true;
  setConversationBusy(true);
  enableView('report'); switchView('report');
  renderReportLoading(context);
  setReportExportReady(false);
  try {
    if (start) {
      const job = await postJSON('/api/review/jobs', {session_id:sessionId});
      if (!isReportContext(context)) return;
      context.identity = reportIdentity(job);
      state.reportJobIdentity = context.identity;
    }
    if (!isReportContext(context)) return;
    localStorage.setItem('interview-sim-pending-report', sessionId);
    state.isBusy = false;
    setConversationBusy(false);
    await pollReport(context);
  } catch (error) {
    if (!isReportContext(context)) return;
    reportStopped(context);
    renderReportError(error.message || '无法连接本地服务，请恢复服务后继续生成。', {}, context);
  }
}

async function pollReport(context) {
  if (!isReportContext(context)) return;
  try {
    const job = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}/review-job`);
    if (!isReportContext(context)) return;
    const identity = reportIdentity(job);
    if (context.identity && context.identity !== identity) {
      reportStopped(context);
      renderReportError('报告任务已在其他页面变更。请从训练记录重新打开当前进度。', {}, context);
      return;
    }
    context.identity = identity;
    state.reportJobIdentity = identity;
    if (job.status === 'completed' && job.report) {
      state.lastReport = job.report;
      state.sessionEnded = true;
      reportStopped(context);
      if (localStorage.getItem('interview-sim-pending-report') === context.sessionId) localStorage.removeItem('interview-sim-pending-report');
      renderReport(job.report); setReportExportReady(true);
      if (state.view !== 'report') showToast('证据报告已生成，可以前往查看。');
      return;
    }
    if (['failed','idle','interrupted','cancelled'].includes(job.status)) {
      reportStopped(context);
      renderReportError(job.error || '上次报告未完成，点击继续生成。', job, context);
      return;
    }
    if ($('report-stage')) {
      $('report-stage').textContent = `${job.stage}${job.retrying ? ' · 正在修正输出格式' : ''}`;
      $('report-stage-progress').max = job.total;
      $('report-stage-progress').value = job.completed;
      $('report-count').textContent = `已保存 ${job.completed} / ${job.total} 个分析阶段`;
      $('report-elapsed').textContent = `本次已等待 ${job.elapsed_seconds} 秒`;
    }
    if (isReportContext(context)) state.reportPoll = setTimeout(() => pollReport(context), 2000);
  } catch (_) {
    if (!isReportContext(context)) return;
    if ($('report-count')) $('report-count').textContent = '暂时无法连接本地服务，正在重新连接；已完成部分已保存。';
    state.reportPoll = setTimeout(() => pollReport(context), 4000);
  }
}

function reportStopped(context) {
  if (!isReportContext(context)) return;
  clearTimeout(state.reportPoll);
  state.reportPoll = null;
  state.isBusy = false;
  setConversationBusy(false);
}

function setReportExportReady(ready) {
  for (const id of ['btn-export-md','btn-export-json','btn-print']) $(id).disabled = !ready;
}

function renderReportError(message, job={}, context) {
  if (context && !isReportContext(context)) return;
  const sessionId = context?.sessionId || state.reportSessionId || state.sessionId;
  $('report-content').innerHTML = `<section class="surface report-progress" role="status"><p class="eyebrow">报告尚未完成</p><h2>${esc(job.stage || '继续生成证据报告')}</h2><p>${esc(message)}</p><p class="field-help">${job.completed ? `已完成并保存 ${job.completed} / ${job.total} 个阶段。` : ''}原面试记录仍然保留。</p><button id="btn-retry-report" class="btn primary" type="button">继续生成报告</button><button id="btn-check-report-settings" class="btn secondary" type="button">检查分析模型设置</button></section>`;
  $('btn-retry-report').addEventListener('click', () => { if (!context || isReportContext(context)) followReport(sessionId, true); });
  $('btn-check-report-settings').addEventListener('click', () => switchView('settings'));
}

async function restorePendingReport() {
  const requested = new URLSearchParams(location.search).get('session');
  const sessionId = requested || localStorage.getItem('interview-sim-pending-report');
  if (!sessionId || !state.serviceReady) return;
  const initialEpoch = state.sessionEpoch;
  try {
    // Session state comes first: active_question always wins over an older report.
    const response = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (state.sessionEpoch !== initialEpoch) return;
    if (response.status === 404) {
      if (localStorage.getItem('interview-sim-pending-report') === sessionId) localStorage.removeItem('interview-sim-pending-report');
      return;
    }
    if (!response.ok) return;
    const session = await response.json();
    if (state.sessionEpoch !== initialEpoch) return;
    if (session.active_question) localStorage.removeItem('interview-sim-pending-report');
    await viewSession(sessionId);
  } catch (_) { /* The user can reopen it from training history. */ }
}

const REPORT_DIMENSIONS = {relevance: '问题相关性', evidence: '证据具体性', professional_content: '专业内容', expression: '表达结构'};
const RESUME_DIMENSIONS = {clarity: '内容清晰', structure: '结构组织', relevance: '岗位针对性', evidence: '贡献证据'};
function reportText(value) { return Array.isArray(value) ? value.join('；') : typeof value === 'string' ? value : ''; }
function reportNumber(value) { return typeof value === 'number' && Number.isFinite(value) ? String(Math.round(value * 100) / 100) : '未评估'; }
function reportValue(value, max = 100) { const number = reportNumber(value); return number === '未评估' ? number : `${number}/${max}`; }
function reportDelta(value) { return typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${reportNumber(value)}` : '未评估'; }
function renderAggregateDimensions(aggregate) {
  return `<dl class="report-four-dimensions">${Object.entries(REPORT_DIMENSIONS).map(([key, label]) => `<div><dt>${label}<small>25%</small></dt><dd>${reportValue(aggregate?.dimensions?.[key]?.score, 10)}</dd></div>`).join('')}</dl>`;
}
function renderEvidenceReport(data) {
  const performance = data.interview_performance || {}, first = performance.first_attempt || {}, retry = performance.latest_retry;
  const resume = data.resume_quality || {}, assessment = resume.status === 'assessed' ? resume.assessment : null;
  const comparison = performance.comparison || {}, coverage = data.requirement_coverage || {};
  $('report-content').innerHTML = `
    <section class="surface report-version-note"><strong>独立评分 · ${esc(data.rubric_version || 'interview-evidence-v1')}</strong><p>首次表现、学习后的重答和简历质量分别展示，不混合为“能力总分”。仅用于练习，不代表录取概率。</p><p>${esc(performance.note || '')}</p></section>
    <div class="report-score-grid">
      <section class="surface report-score-card"><p class="eyebrow">RESUME SNAPSHOT</p><h2>简历质量</h2><strong id="report-resume-total" class="report-total">${reportValue(assessment?.total)}</strong><p>${esc(assessment ? '仅评价本场所选简历的准备诊断快照，不从面试反推。' : resume.reason || '本场版本没有有效的准备诊断。')}</p><small>简历版本：${esc(resume.resume_version_id || '未关联')}</small>${assessment ? `<details><summary>查看简历诊断维度</summary><ul>${Object.entries(RESUME_DIMENSIONS).map(([key,label]) => `<li>${label}：${reportValue(assessment.dimensions?.[key]?.score,10)}<p>${esc(assessment.dimensions?.[key]?.comment || '')}</p></li>`).join('')}</ul>${renderTips(assessment.limitations || [])}</details>` : ''}</section>
      <section class="surface report-score-card primary-score"><p class="eyebrow">FIRST ATTEMPT</p><h2>首次面试表现</h2><strong id="report-first-total" class="report-total">${reportValue(first.total)}</strong><p>${Number(first.question_count) || 0} 题 · 已回答 ${Number(first.answered_count) || 0} · 未回答 ${Number(first.unanswered_count) || 0}</p>${renderAggregateDimensions(first)}<small>${esc(first.scope_note || '每个题号只取首次实际作答，重答不会改变首次分数。')}</small></section>
      <section class="surface report-score-card"><p class="eyebrow">LATEST RETRY</p><h2>最新重答表现</h2><strong id="report-retry-total" class="report-total">${reportValue(retry?.total)}</strong><p>${retry ? `${Number(retry.question_count) || 0} 道实际重答题，仅取各题最近一次重答。` : '尚无重答记录；未评估不等于零分。'}</p>${retry ? renderAggregateDimensions(retry) : ''}<small>看过反馈后的练习表现，不替代原面试评价。</small></section>
    </div>
    <section id="report-comparison" class="surface report-section"><h2>同题重答对比</h2><p>只比较同一批实际重答题的首次与最近一次，不拿单题重答和整场总分相减。</p>${comparison.paired_question_count ? `<div class="report-paired-total"><strong>${reportNumber(comparison.original_total)} → ${reportNumber(comparison.latest_total)}</strong><span>变化 ${reportDelta(comparison.delta)} 分 · 配对 ${Number(comparison.paired_question_count)} 题</span></div><div class="prep-table-scroll"><table class="prep-table"><thead><tr><th>题号</th><th>首次作答</th><th>最近重答</th><th>同题变化</th></tr></thead><tbody>${(comparison.items || []).map(item => `<tr><th scope="row">${esc(item.question_id)}</th><td>第 ${Number(item.first_attempt)} 次 · ${reportValue(item.original_total)}</td><td>第 ${Number(item.latest_attempt)} 次 · ${reportValue(item.latest_total)}</td><td>${reportDelta(item.delta)} 分</td></tr>`).join('')}</tbody></table></div>` : '<p class="prep-empty">尚无可配对的重答，不计算提升幅度。</p>'}<p class="field-help">${esc(performance.note || '首次分数保持原评估；不同模型或规则版本不直接解释为能力提升。')}</p></section>
    <section id="report-coverage" class="surface report-section"><h2>岗位要求覆盖</h2><p>${esc(coverage.note || '材料证据与面试观察分别记录，不是录取概率。')}</p>${coverage.status === 'available' ? renderReportCoverage(coverage.requirements || []) : '<p class="prep-empty">本场没有有效的岗位要求分析快照，未评估。</p>'}</section>
    <div class="report-grid"><section class="surface report-section"><div class="report-section-heading"><div><h2>逐题证据</h2><p>每次作答独立保留，引用来自对应原回答。已问且跳过的题为零分；没问的要求不进入分母。</p></div><span>${(data.question_feedback || []).length} 次作答</span></div><div class="feedback-list">${renderQuestionFeedback(data.question_feedback || [], true)}</div></section><aside class="side-stack"><section class="surface report-section"><h2>下一轮训练</h2>${renderPracticePlan(data.practice_plan || [])}</section><section class="surface report-section"><h2>面试提升建议</h2>${renderTips(data.interview_tips || [])}</section><section class="surface report-section"><h2>报告溯源</h2><p class="report-source-id">报告：${esc(data.report_id || '')}</p><p class="report-source-id">输入：${esc(data.input_fingerprint || '')}</p><p>${esc(data.generated_at || '')}</p></section></aside></div>${reportLearningShell()}`;
}
function renderReportCoverage(items) {
  const labels = {supported:'已有材料证据', needs_verification:'需要验证', missing:'缺少材料'};
  return `<div class="prep-table-scroll"><table class="prep-table"><thead><tr><th>岗位要求与来源</th><th>准备材料</th><th>面试观察</th></tr></thead><tbody>${items.map(item => `<tr><th scope="row">${esc(item.requirement)}<p>${esc(item.source_quote)}</p></th><td>${esc(labels[item.status] || '未评估')}${prepEvidence(item.evidence)}<p>${esc(item.note || '')}</p></td><td>${item.interview_status === 'observed' ? '有已验证的原答证据' : '未观察到 · 不作能力否定'}${(item.interview_evidence || []).map(evidence => `<blockquote>${esc(evidence.quote || '')}<small>${esc(evidence.question_id || '')} · 第 ${Number(evidence.attempt) || 1} 次作答</small></blockquote>`).join('')}</td></tr>`).join('')}</tbody></table></div>`;
}
function renderEvidenceReportMarkdown(data) {
  const performance = data.interview_performance || {}, first = performance.first_attempt || {}, retry = performance.latest_retry;
  const resume = data.resume_quality || {}, assessment = resume.status === 'assessed' ? resume.assessment : null;
  const comparison = performance.comparison || {}, coverage = data.requirement_coverage || {};
  const lines = ['# 面试训练证据报告', '', `评分规则：${data.rubric_version || 'interview-evidence-v1'}（schema 2）`, '首次表现、重答表现、简历质量分开展示；不代表录取概率。', '',
    `简历质量：${reportValue(assessment?.total)}`, `简历版本：${resume.resume_version_id || '未关联'}`, `诊断说明：${resume.reason || '仅使用本场材料诊断快照'}`,
    ...(assessment ? Object.entries(RESUME_DIMENSIONS).map(([key,label]) => `- 简历诊断·${label}：${reportValue(assessment.dimensions?.[key]?.score,10)}；${assessment.dimensions?.[key]?.comment || ''}`) : []),
    ...(assessment?.limitations || []).map(item => `诊断边界：${item}`), ...(resume.source_task_id ? [`诊断来源任务：${resume.source_task_id}`] : []), '',
    `首次面试表现：${reportValue(first.total)}`, `实际题数：${first.question_count || 0}；已回答：${first.answered_count || 0}；未回答：${first.unanswered_count || 0}`,
    ...Object.entries(REPORT_DIMENSIONS).map(([key,label]) => `- ${label}：${reportValue(first.dimensions?.[key]?.score,10)}，权重25%`), '',
    `最新重答表现：${reportValue(retry?.total)}`, `重答题数：${retry?.question_count || 0}；仅使用每题最近一次重答，不覆盖首次分数。`,
    ...Object.entries(REPORT_DIMENSIONS).map(([key,label]) => `- ${label}：${reportValue(retry?.dimensions?.[key]?.score,10)}，权重25%`), '', '## 同题重答对比'];
  if (comparison.paired_question_count) {
    lines.push(`同题配对：${reportNumber(comparison.original_total)} → ${reportNumber(comparison.latest_total)}；变化 ${reportDelta(comparison.delta)} 分；共 ${comparison.paired_question_count} 题。`);
    (comparison.items || []).forEach(item => lines.push(`- ${item.question_id}：第 ${item.first_attempt} 次 ${reportValue(item.original_total)} → 第 ${item.latest_attempt} 次 ${reportValue(item.latest_total)}，变化 ${reportDelta(item.delta)} 分`));
  } else lines.push('尚无可配对的重答，不计算提升幅度。');
  lines.push(performance.note || '重答不改变首次表现；只比较同一批重答题。', '', '## 岗位要求覆盖', coverage.note || '不是录取概率；未观察到不等于不会。');
  if (coverage.status !== 'available') lines.push('本场没有有效的岗位要求分析快照，未评估。');
  (coverage.requirements || []).forEach(item => lines.push(`### ${item.requirement}`, `JD 原文：${item.source_quote || ''}`, `材料状态：${({supported:'已有材料证据',needs_verification:'需要验证',missing:'缺少材料'})[item.status] || '未评估'}`, ...(item.evidence || []).map(e => `材料证据（${e.source_id}）：${e.quote}`), `面试观察：${item.interview_status === 'observed' ? '有已验证的原答证据' : '未观察到，不作能力否定'}`, ...(item.interview_evidence || []).map(e => `原答证据（${e.question_id} / 第 ${e.attempt} 次）：${e.quote}`), item.note || ''));
  lines.push('', '## 逐题证据');
  (data.question_feedback || []).forEach(item => {
    if (item.scoring_excluded) {
      lines.push('', `### ${item.question_id} · 系统重复题，未纳入评分`, `问题：${item.question || ''}`, item.exclusion_reason || '原始记录保留，不重复扣分。');
      return;
    }
    lines.push('', `### ${item.question_id} · 第 ${item.attempt || 1} 次作答 · ${reportValue(item.score,10)}`, `问题：${item.question || ''}`, `回答：${item.status === 'unanswered' ? '未回答（0 分）' : item.answer || ''}`, `原因说明：${item.reason_analysis || item.skip_reason || '未说明，不推测心理原因'}`, ...Object.entries(REPORT_DIMENSIONS).map(([key,label]) => `- ${label}：${reportValue(item.dimensions?.[key]?.score,10)}；${item.dimensions?.[key]?.comment || ''}`), `证据：${(item.evidence_quotes || []).join('；') || '无可验证引用'}`, `已覆盖：${(item.covered_points || []).join('；') || '无'}`, `仍缺少：${(item.missed_points || []).join('；') || '无'}`, `问题讲解：${item.question_explanation || ''}`, `下一次改进：${item.coaching_tip || ''}`, `作答提纲：${reportText(item.improved_answer_outline)}`);
    if (item.voice_input) lines.push(`原始转写：${item.voice_input.raw_transcript || ''}`, `整理稿：${item.voice_input.cleaned_transcript || '未使用'}`, 'ASR文字并非录音真值；不据此评估真实口吃、语速或现场流畅度。');
  });
  lines.push('', '## 下一轮训练', ...(data.practice_plan || []).map(item => `- ${item.question_id}：${item.focus}；${item.reason}`), '', '## 面试提升建议', ...(data.interview_tips || []).map(item => `- ${item}`), '', `报告：${data.report_id || ''}`, `输入指纹：${data.input_fingerprint || ''}`, `生成时间：${data.generated_at || ''}`);
  return lines.join('\n');
}

function reportLearningShell() {
  return `<section id="report-learning" class="surface report-section report-learning"><p class="eyebrow">NEXT PREPARATION</p><h2>把真实回答带回下一次准备</h2><p>只从本场保存的原回答提取逐字片段，不使用示例答案或改写提纲。提取结果先待确认，不会自动更新简历。</p><p id="report-learning-status" role="status" aria-live="polite">正在检查岗位关联…</p><div class="prep-inline-actions"><button id="btn-extract-materials" class="btn primary" type="button" data-report-action="extract" disabled>补充到经历素材</button><button id="btn-return-preparation" class="btn secondary" type="button" data-report-action="prepare" disabled>回到岗位准备</button><button class="btn secondary" type="button" data-report-action="refresh">刷新提取状态</button></div><div id="report-material-task" aria-live="polite"></div></section>`;
}
function isLearningContext(context) { return context && state.reportLearning === context && isSessionContext(context); }
function initializeReportLearning(data) {
  clearTimeout(state.reportLearning?.timer);
  const context = {...sessionContext(), reportId: data.report_id || 'legacy', sequence: 0, loading: false, busy: false, task: null, job: null, versionId: null, eligible: false, message: '', timer: null};
  state.reportLearning = context;
  if (!context.sessionId) { context.message = '未关联岗位，不能自动导入经历素材。'; renderReportLearning(context); return; }
  refreshReportLearning();
}
async function refreshReportLearning() {
  const context = state.reportLearning;
  if (!isLearningContext(context) || !context.sessionId || context.loading || context.busy) return;
  const sequence = ++context.sequence;
  context.loading = true;
  clearTimeout(context.timer);
  try {
    const session = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}`);
    if (!isLearningContext(context) || sequence !== context.sequence) return;
    const jobId = session.config?.preset_id;
    context.versionId = session.config?.resume_version_id;
    if (!jobId || !context.versionId) { context.message = '本场未关联岗位或简历版本，不能自动导入经历素材；不会按职位名称猜测归属。'; context.job = null; return; }
    const jobs = await getJSON('/api/presets');
    if (!isLearningContext(context) || sequence !== context.sequence) return;
    context.job = jobs.find(job => job.id === jobId) || null;
    if (!context.job) { context.message = '原岗位已删除，不能自动导入素材。历史报告仍可查看与导出。'; return; }
    const dossier = await getJSON(`/api/preparation/${encodeURIComponent(jobId)}`);
    if (!isLearningContext(context) || sequence !== context.sequence) return;
    context.eligible = ['ended','completed','interview_finished','review_failed'].includes(session.status) && !session.active_question && session.report_job?.status !== 'running';
    context.task = (dossier.tasks || []).filter(task => task.kind === 'session_materials' && task.session_id === context.sessionId && task.version_id === context.versionId).sort((a,b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0] || null;
    context.message = context.eligible ? `来源：${context.job.target_role} · 本场简历版本 ${context.versionId}。提取后仍需逐条核对。` : '本场尚有活动问题或报告任务，结束后再提取原回答。';
  } catch (error) {
    if (isLearningContext(context) && sequence === context.sequence) { context.eligible = false; context.message = `读取关联状态失败：${error.message}。可点击刷新，不会自动重复分析。`; }
  } finally {
    if (isLearningContext(context) && sequence === context.sequence) { context.loading = false; renderReportLearning(context); scheduleReportLearning(context); }
  }
}
function renderReportLearning(context) {
  if (!isLearningContext(context) || !$('report-learning-status')) return;
  const task = context.task;
  $('report-learning-status').textContent = context.message;
  $('btn-extract-materials').disabled = context.busy || context.loading || !context.eligible || !context.job || task?.status === 'running' || ['failed','cancelled','interrupted'].includes(task?.status) && !task.stale;
  $('btn-return-preparation').disabled = !context.job || context.busy || context.loading;
  $('btn-extract-materials').textContent = task?.status === 'completed' && !task.stale ? '读取已提取素材' : '补充到经历素材';
  if (!task) { $('report-material-task').replaceChildren(); return; }
  const label = {running:'正在提取',completed:'提取已完成',failed:'提取失败',cancelled:'已取消',interrupted:'已中断'}[task.status] || task.status;
  $('report-material-task').innerHTML = `<article class="report-material-result"><h3>${esc(label)}${task.stale ? ' · 输入已过期' : ''}</h3><p>${esc(task.stage || '')}</p>${task.error ? `<p class="error">${esc(task.error)}</p>` : ''}${task.result ? `<p>${esc(task.result.notice || '')} · 新增 ${Number(task.result.created_count) || 0} 条待确认素材。</p><p>请回到岗位准备逐条确认、编辑或拒绝。只有明确确认的素材才供后续任务使用。</p>${(task.result.facts || []).map(fact => `<details><summary>${esc(fact.text)}</summary><blockquote>${esc(fact.source_quote || '')}</blockquote><small>${esc(fact.source?.question_id || '')} · 第 ${Number(fact.source?.attempt) || 1} 次 · 来源简历 ${esc(fact.source_version_id || '')}</small></details>`).join('')}` : ''}<div class="prep-inline-actions">${task.status === 'running' ? '<button class="btn secondary compact" type="button" data-report-action="cancel">取消提取</button>' : ['failed','cancelled','interrupted'].includes(task.status) && !task.stale ? '<button class="btn secondary compact" type="button" data-report-action="retry">重试提取</button>' : ''}</div></article>`;
  $('report-material-task').querySelectorAll('button').forEach(button => { button.disabled = context.busy || context.loading; });
}
function scheduleReportLearning(context) {
  clearTimeout(context.timer);
  if (!isLearningContext(context) || context.busy || state.view !== 'report' || context.task?.status !== 'running') return;
  const expected = `${context.task.id}:${context.task.generation}`;
  context.timer = setTimeout(async () => {
    if (!isLearningContext(context) || state.view !== 'report' || context.busy) return;
    const sequence = ++context.sequence;
    try {
      const response = await getJSON(`/api/preparation/${encodeURIComponent(context.job.id)}/tasks/${encodeURIComponent(context.task.id)}`);
      if (!isLearningContext(context) || sequence !== context.sequence) return;
      const task = response.task;
      if (`${task.id}:${task.generation}` !== expected) { context.message = '提取任务已在其他页面变更，请刷新当前状态。'; renderReportLearning(context); return; }
      context.task = task;
      renderReportLearning(context);
      scheduleReportLearning(context);
    } catch (error) {
      if (isLearningContext(context) && sequence === context.sequence) { context.message = `读取提取进度失败：${error.message}。点击刷新可继续查看，不会自动重复提取。`; renderReportLearning(context); }
    }
  }, 1200);
}
async function handleReportLearningAction(action) {
  const context = state.reportLearning;
  if (!isLearningContext(context) || context.busy || context.loading) return;
  if (action === 'refresh') return refreshReportLearning();
  if (!context.job) return;
  if (action === 'prepare') return openPreparation(context.job, context.versionId);
  if (!['extract','retry','cancel'].includes(action) || action !== 'cancel' && !context.eligible) return;
  if (action === 'extract' && !window.confirm('将本场原始回答发送到已配置的分析模型，提取为待确认素材？不会使用示例答案，也不会自动修改简历。相同来源已完成的任务会复用。')) return;
  const sequence = ++context.sequence;
  context.busy = true;
  clearTimeout(context.timer);
  renderReportLearning(context);
  try {
    let response;
    const path = `/api/preparation/${encodeURIComponent(context.job.id)}`;
    if (action === 'extract') {
      const dossier = await getJSON(path);
      if (!isLearningContext(context) || sequence !== context.sequence) return;
      response = await postJSON(`${path}/session-materials`, {session_id: context.sessionId, revision: dossier.revision});
    } else response = await postJSON(`${path}/tasks/${encodeURIComponent(context.task.id)}/${action}`, {});
    if (!isLearningContext(context) || sequence !== context.sequence) return;
    context.task = response.task;
    context.message = action === 'cancel' ? '已取消后续提取；已发出的远程请求仍可能产生用量。' : '提取任务已就绪。只产生待确认素材，尚未修改简历。';
  } catch (error) { if (isLearningContext(context) && sequence === context.sequence) context.message = `${error.message}。原回答与简历未被覆盖，可刷新核对后重试。`; }
  finally { if (isLearningContext(context) && sequence === context.sequence) { context.busy = false; renderReportLearning(context); scheduleReportLearning(context); } }
}
