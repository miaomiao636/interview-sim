/* Per-job preparation. Results belong to immutable inputs, never to the page alone. */
const preparation = {job: null, dossier: null, versionId: null, epoch: 0, readSequence: 0, loading: false, busy: false, dirty: false, timer: null, taskRequests: new Set(), disclosureState: new Map(), disclosureScope: ''};
const PREP_TASKS = {resume: '简历诊断', recruitment: '岗位要求分析', materials: '整理练习素材'};
const PREP_CATEGORIES = {job_alignment:'岗位匹配', contribution:'个人贡献', evidence:'成果证据', structure:'结构与取舍', wording:'表达优化'};
const PREP_PRIORITIES = {high:'优先处理', medium:'建议处理', low:'细节优化'};

function bindPreparation() {
  $('view-preparation').innerHTML = `
    <header class="page-header"><div><p class="eyebrow">JOB PREPARATION</p><h1 id="prep-title" tabindex="-1">岗位准备</h1><p id="prep-subtitle" class="page-lead">材料先准备，实战时专注回答。</p></div><div class="prep-header-actions"><button id="prep-back" class="btn secondary" type="button">返回岗位</button><button id="prep-start" class="btn primary" type="button" disabled>开始面试</button></div></header>
    <p id="prep-status" class="inline-status prep-notice" role="status" aria-live="polite"></p>
    <div id="prep-loading" class="surface prep-empty" role="status">请选择一个岗位。</div>
    <div id="prep-content" class="hidden">
      <section class="surface prep-job-overview"><div class="section-heading"><h2>本次准备的材料</h2><button id="prep-refresh" class="btn secondary compact" type="button">刷新档案</button></div><p id="prep-stale" class="skip-note hidden"></p><details><summary>查看岗位描述与公司材料</summary><pre id="prep-jd" class="prep-source"></pre></details></section>
      <section class="prep-task-grid" aria-label="准备任务">${Object.entries(PREP_TASKS).map(([kind, label]) => `<article class="surface prep-task"><h2>${label}</h2><p>${{resume:'检查当前版本，逐项确认后再改写。', recruitment:'区分岗位要求、已有证据与待验证内容。', materials:'基于真实材料整理提纲，不自动写入简历。'}[kind]}</p><button class="btn secondary" type="button" data-prep-task="${kind}">${label}</button><div id="prep-task-${kind}" class="prep-task-state" aria-live="polite"></div></article>`).join('')}</section>
      <p class="field-help prep-data-note">仅点击分析时才调用已配置的分析模型，发送当前岗位、所选简历和已确认素材。取消会停止本地后续处理；已发送的远程请求仍可能产生用量。</p>
      <div class="prep-workspace">
        <section class="surface prep-editor"><div class="section-heading"><h2>简历版本</h2><span id="prep-dirty" class="prep-badge">已保存</span></div>
          <label for="prep-version">查看 / 使用的版本</label><select id="prep-version"></select><p id="prep-version-note" class="field-help"></p>
          <label for="prep-resume">简历正文</label><textarea id="prep-resume" rows="16" maxlength="50000"></textarea>
          <label for="prep-version-name">新版本名称</label><input id="prep-version-name" class="text-input" maxlength="120" placeholder="例如：突出项目贡献的版本">
          <button id="prep-save" class="btn primary wide" type="button">保存为新版本</button><p class="field-help">不会覆盖旧版本。保存或采纳后，新版本需重新诊断；旧分数不会沿用。</p>
        </section>
        <section class="surface prep-diagnosis"><h2>诊断与修改建议</h2><div id="prep-assessment"></div><div id="prep-advice"></div><div id="prep-suggestions"></div></section>
      </div>
      <details class="surface prep-section prep-panel" data-prep-disclosure="requirements"><summary><h2>岗位要求与材料证据</h2></summary><p class="field-help">表示材料覆盖情况，不是录取概率。面试尚未验证的能力不算不会。</p><div id="prep-requirements"></div></details>
      <details class="surface prep-section prep-panel" data-prep-disclosure="facts"><summary><h2>真实经历素材</h2></summary><p class="field-help">仅已确认素材供后续准备使用。确认表示你认可其真实性，不代表第三方核验。修改后会重新进入待确认。</p><div id="prep-session-tasks"></div><div id="prep-facts"></div></details>
      <details class="surface prep-section prep-panel" data-prep-disclosure="materials"><summary><h2>面前练习提纲</h2></summary><p class="field-help">这是准备草稿，请按真实经历核对；不会当作你的实际面试回答，也不会自动写入简历。</p><div id="prep-materials"></div></details>
      <details class="surface prep-section prep-panel" data-prep-disclosure="history"><summary><h2>历史准备成果</h2></summary><p class="field-help">保留旧版本和过期成果供比较，不作为当前材料的评价。</p><div id="prep-history"></div></details>
    </div>`;
  $('prep-back').addEventListener('click', () => switchView('config'));
  $('prep-refresh').addEventListener('click', () => { if (discardPreparationEdits()) refreshPreparation(true); });
  $('prep-resume').addEventListener('input', updatePreparationDirty);
  $('prep-version-name').addEventListener('input', updatePreparationDirty);
  $('prep-version').addEventListener('change', () => {
    const selected = $('prep-version').value;
    if (!discardPreparationEdits()) { $('prep-version').value = preparation.versionId; return; }
    preparation.versionId = selected;
    preparation.epoch++;
    renderPreparation(true);
    schedulePreparationPoll();
  });
  $('prep-save').addEventListener('click', savePreparationVersion);
  $('prep-start').addEventListener('click', () => {
    if (!discardPreparationEdits()) return;
    openInterviewSetup(preparation.job, preparation.versionId);
  });
  $('view-preparation').addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (button.dataset.prepTask) runPreparationTask(button.dataset.prepTask);
    if (button.dataset.taskAction) actOnPreparationTask(button.dataset.taskAction, button.dataset.taskId);
    if (button.dataset.acceptSuggestion) acceptPreparationSuggestion(button.dataset.acceptSuggestion);
    if (button.dataset.factDecision) decidePreparationFact(button.dataset.factId, button.dataset.factDecision);
    if (button.dataset.prepSettings !== undefined) switchView('settings');
    if (button.dataset.prepFold) {
      const group = $(button.dataset.prepFold);
      group?.querySelectorAll('details[data-prep-disclosure]').forEach(item => { item.open = button.dataset.expand === 'true'; });
    }
  });
  window.addEventListener('beforeunload', event => {
    if (preparation.dirty || preparation.busy) { event.preventDefault(); event.returnValue = ''; }
  });
}

function prepContext() { return {epoch: preparation.epoch, jobId: preparation.job?.id, versionId: preparation.versionId}; }
function isPrepContext(context) { return context.epoch === preparation.epoch && context.jobId === preparation.job?.id && context.versionId === preparation.versionId; }
function selectedPreparationVersion() { return preparation.dossier?.versions.find(version => version.id === preparation.versionId); }
function defaultPreparationVersion(dossier) {
  return (dossier.stale && dossier.versions.find(version => version.source?.kind === 'preset' && version.source.version_signature === dossier.preset_signature)?.id) || dossier.current_version_id;
}
function prepMessage(text, error = false) {
  $('prep-status').textContent = text;
  $('prep-status').classList.toggle('error', error);
}
function stopPreparationPoll() { clearTimeout(preparation.timer); preparation.timer = null; }
function discardPreparationEdits() {
  if (preparation.busy) { showToast('正在保存操作，请稍候再切换。', true); return false; }
  if (preparation.dirty && !window.confirm('简历或素材还有未保存的编辑。确定放弃草稿吗？')) return false;
  if (preparation.dirty) { preparation.dirty = false; renderPreparation(true); }
  return true;
}
function beforePreparationNavigation(name) {
  if (state.view !== 'preparation' || name === 'preparation') return true;
  if (!discardPreparationEdits()) return false;
  preparation.epoch++;
  preparation.loading = false;
  stopPreparationPoll();
  return true;
}
function onPreparationViewShown() {
  if (preparation.job && !preparation.loading) refreshPreparation(false);
}

async function openPreparation(job, versionId = null) {
  if (!job || state.isBusy || state.isRecording || state.voice?.processing || !discardPreparationEdits()) return;
  stopPreparationPoll();
  Object.assign(preparation, {job, dossier: null, versionId, epoch: preparation.epoch + 1, loading: true, dirty: false});
  enableView('preparation');
  switchView('preparation');
  $('prep-title').textContent = job.target_role + ' · 岗位准备';
  $('prep-subtitle').textContent = [job.company, job.city].filter(Boolean).join(' · ') || '按需准备，也可以直接开始面试。';
  $('prep-content').classList.add('hidden');
  $('prep-loading').classList.remove('hidden');
  $('prep-loading').textContent = '正在读取岗位准备档案…';
  $('prep-start').disabled = true;
  prepMessage('');
  await refreshPreparation(true);
  if (state.view === 'preparation') $('prep-title').focus();
}

async function refreshPreparation(resetEditor = false) {
  if (!preparation.job) return;
  const context = prepContext();
  const readSequence = ++preparation.readSequence;
  try {
    const dossier = await getJSON(`/api/preparation/${encodeURIComponent(context.jobId)}`);
    if (!isPrepContext(context) || readSequence !== preparation.readSequence) return;
    preparation.dossier = dossier;
    if (!dossier.versions.some(version => version.id === preparation.versionId)) preparation.versionId = defaultPreparationVersion(dossier);
    $('prep-loading').classList.add('hidden');
    $('prep-content').classList.remove('hidden');
    renderPreparation(resetEditor);
    schedulePreparationPoll();
    return true;
  } catch (error) {
    if (!isPrepContext(context) || readSequence !== preparation.readSequence) return;
    $('prep-loading').textContent = '读取失败。返回岗位后可重新打开。';
    prepMessage(error.message, true);
    return false;
  } finally { if (context.epoch === preparation.epoch) preparation.loading = false; }
}

function updatePreparationDirty() {
  const resumeDirty = $('prep-resume').value !== (selectedPreparationVersion()?.resume || '') || Boolean($('prep-version-name').value.trim());
  const factsDirty = [...document.querySelectorAll('[data-fact-text]')].some(input => input.value !== input.dataset.original);
  preparation.dirty = resumeDirty || factsDirty;
  $('prep-dirty').textContent = preparation.dirty ? '有未保存编辑' : '已保存';
}

function capturePreparationDisclosures() {
  const scope = preparation.disclosureScope;
  if (!scope) return null;
  let focusKey = null;
  $('view-preparation').querySelectorAll('details[data-prep-disclosure]').forEach(item => {
    preparation.disclosureState.set(`${scope}:${item.dataset.prepDisclosure}`, item.open);
    if (document.activeElement === item.querySelector(':scope > summary')) focusKey = item.dataset.prepDisclosure;
  });
  return {scope, focusKey};
}
function restorePreparationDisclosures(previous) {
  const scope = `${preparation.job?.id}:${preparation.versionId}`;
  preparation.disclosureScope = scope;
  $('view-preparation').querySelectorAll('details[data-prep-disclosure]').forEach(item => {
    item.open = preparation.disclosureState.get(`${scope}:${item.dataset.prepDisclosure}`) === true;
    if (previous?.scope === scope && previous.focusKey === item.dataset.prepDisclosure) item.querySelector(':scope > summary')?.focus({preventScroll:true});
  });
}

function renderPreparation(resetEditor = false) {
  const dossier = preparation.dossier, version = selectedPreparationVersion();
  if (!dossier || !version) return;
  const disclosures = capturePreparationDisclosures();
  $('prep-jd').textContent = preparation.job.jd;
  $('prep-stale').classList.toggle('hidden', !dossier.stale);
  $('prep-stale').textContent = '岗位材料已变更。默认展示当前岗位快照，请先保存为新版本再诊断；旧成果仅供参考。也可选择历史简历直接面试。';
  $('prep-version').innerHTML = dossier.versions.map(item => `<option value="${escAttr(item.id)}">${esc(item.name || '简历版本')}${item.id === dossier.current_version_id ? ' · 当前保存版本' : ''}</option>`).join('');
  $('prep-version').value = preparation.versionId;
  $('prep-version-note').textContent = `${version.source?.kind === 'preset' ? '岗位原始材料快照' : '独立保存的简历版本'} · ${version.created_at ? formatDate(version.created_at) : '未记录时间'}`;
  if (resetEditor || !preparation.dirty) {
    $('prep-resume').value = version.resume;
    $('prep-version-name').value = '';
  }
  $('prep-start').disabled = preparation.busy;
  $('prep-save').disabled = preparation.busy;
  $('prep-version').disabled = preparation.busy;
  $('prep-resume').disabled = preparation.busy;
  $('prep-version-name').disabled = preparation.busy;
  renderPreparationTasks();
  renderSessionMaterialTasks();
  renderPreparationAssessment();
  renderPreparationAdvice();
  renderPreparationSuggestions();
  renderPreparationRequirements();
  if (!preparation.dirty || resetEditor) renderPreparationFacts();
  renderPreparationMaterials();
  renderPreparationHistory();
  restorePreparationDisclosures(disclosures);
  updatePreparationDirty();
}
function renderSessionMaterialTasks() {
  const tasks = prepTasks('session_materials', false);
  $('prep-session-tasks').innerHTML = tasks.map(task => `<article class="prep-session-task"><strong>面后素材提取 · ${({running:'进行中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'})[task.status] || esc(task.status)}</strong><p class="field-help">会话 ${esc(task.session_id || '')} · 来源简历 ${esc(task.version_id)}${task.stale ? ' · 输入已过期' : ''}</p><p>${esc(task.stage || '')}</p>${task.error ? `<p class="error">${esc(task.error)}</p>` : ''}${task.result ? `<p>${esc(task.result.notice || '')} · 新增 ${Number(task.result.created_count) || 0} 条待确认素材</p>` : ''}${task.status === 'running' ? `<button class="btn secondary compact" type="button" data-task-action="cancel" data-task-id="${escAttr(task.id)}">取消提取</button>` : ['failed','cancelled','interrupted'].includes(task.status) && !task.stale ? `<button class="btn secondary compact" type="button" data-task-action="retry" data-task-id="${escAttr(task.id)}">重试提取</button>` : ''}</article>`).join('');
}

function prepTasks(kind, exactVersion = true) {
  return (preparation.dossier?.tasks || []).filter(task => (!kind || task.kind === kind) && (!exactVersion || task.version_id === preparation.versionId));
}
function prepResult(kind) {
  if (preparation.dossier?.stale) return null;
  return prepTasks(kind).filter(task => task.status === 'completed' && !task.stale && task.result).sort((a,b) => (b.updated_at || '').localeCompare(a.updated_at || ''))[0]?.result || null;
}
function renderPreparationTasks() {
  for (const kind of Object.keys(PREP_TASKS)) {
    const tasks = prepTasks(kind), task = tasks.filter(item => item.status === 'running')[0] || tasks.at(-1);
    const button = document.querySelector(`[data-prep-task="${kind}"]`);
    button.disabled = preparation.busy || preparation.taskRequests.has(kind) || task?.status === 'running' || Boolean(preparation.dossier.stale);
    const slot = $(`prep-task-${kind}`);
    if (!task) { slot.textContent = '未执行 · 可独立选择，不必按顺序'; continue; }
    const labels = {running:'进行中', completed:'已完成', failed:'失败', cancelled:'已取消', interrupted:'已中断'};
    const started = Date.parse(task.started_at), elapsed = Number.isFinite(started) ? Math.max(0, Math.floor((Date.now() - started) / 1000)) : 0;
    slot.innerHTML = `<strong>${esc(labels[task.status] || task.status)}${task.stale ? ' · 输入已过期' : ''}</strong><p>${esc(task.stage || '')}${task.status === 'running' ? ` · 已等待 ${elapsed} 秒` : ''}</p>${task.error ? `<p class="error">${esc(task.error)}</p>` : ''}<div class="prep-inline-actions">${task.status === 'running' ? `<button class="btn secondary compact" type="button" data-task-action="cancel" data-task-id="${escAttr(task.id)}">取消任务</button>` : ['failed','cancelled','interrupted'].includes(task.status) && !task.stale ? `<button class="btn secondary compact" type="button" data-task-action="retry" data-task-id="${escAttr(task.id)}">重试任务</button>` : ''}</div>`;
  }
}
function prepMissing(items) { return items?.length ? `<details class="prep-missing"><summary>待补充 / 待核对 · ${items.length} 项</summary><ul>${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul></details>` : ''; }
function renderPreparationAssessment() {
  const result = prepResult('resume');
  if (!result) { $('prep-assessment').innerHTML = '<p class="prep-empty">此版本未评估。点击简历诊断，不会沿用其他版本的分数。</p>'; return; }
  const assessment = result.assessment;
  const labels = {clarity:'内容清晰', structure:'结构组织', relevance:'岗位针对性', evidence:'贡献证据'};
  $('prep-assessment').innerHTML = `<div class="prep-score"><span>简历质量 · 当前所选版本</span><strong>${esc(assessment.total)}<small> / 100</small></strong></div><details class="prep-assessment-details" data-prep-disclosure="assessment"><summary>查看四维诊断与评分依据</summary><ul class="prep-dimensions">${Object.entries(labels).map(([key, label]) => `<li><strong>${label} · ${esc(assessment.dimensions[key].score)} / 10</strong><p>${esc(assessment.dimensions[key].comment)}</p></li>`).join('')}</ul><p class="field-help">四个维度各占 25%。仅评价文本内容，不评价视觉排版，不是 ATS 通过率。</p></details>${prepMissing([...(assessment.limitations || []), ...(result.missing_information || [])])}`;
}

function prepFoldControls(id) {
  return `<div class="prep-fold-actions"><button class="btn text compact" type="button" data-prep-fold="${id}" data-expand="true">展开全部</button><button class="btn text compact" type="button" data-prep-fold="${id}" data-expand="false">收起全部</button></div>`;
}
function prepAdviceSummary(item, fallback) {
  return `<span class="prep-badge prep-priority-${escAttr(item.priority || 'medium')}">${esc(PREP_PRIORITIES[item.priority] || fallback)}</span><strong>${esc(item.title || item.reason?.slice(0, 80) || fallback)}</strong><small>${esc(PREP_CATEGORIES[item.category] || '表达优化')}</small>`;
}
function renderPreparationAdvice() {
  const result = prepResult('resume'), items = result?.advice || [];
  const priority = {high:0, medium:1, low:2};
  const sorted = items.map((item,index) => ({...item, key:index})).sort((a,b) => (priority[a.priority] ?? 1) - (priority[b.priority] ?? 1));
  $('prep-advice').innerHTML = `<div class="prep-result-heading"><h3>内容提升建议${items.length ? ` · ${items.length}` : ''}</h3>${items.length ? prepFoldControls('prep-advice') : ''}</div><p class="field-help">先看该补什么、该删什么和如何突出贡献；建议不会自动写入简历。</p>${items.length ? sorted.map(item => `<details class="prep-advice-item" data-prep-disclosure="advice-${item.key}"><summary>${prepAdviceSummary(item, '内容建议')}</summary><div class="prep-disclosure-body"><dl><dt>为什么要改</dt><dd>${esc(item.problem)}</dd><dt>具体怎么做</dt><dd>${esc(item.action)}</dd></dl>${item.questions?.length ? `<h4>需要你补充</h4><ul>${item.questions.map(question => `<li>${esc(question)}</li>`).join('')}</ul>` : ''}${prepEvidence(item.evidence)}</div></details>`).join('') : `<p class="prep-empty">${result ? '这是旧版诊断，尚无内容提升建议。重新运行简历诊断可生成新版建议。' : '运行简历诊断，获取岗位匹配、贡献、成果证据与结构取舍建议。旧诊断保留在历史准备成果中。'}</p>`}`;
}

function renderPreparationSuggestions() {
  const versions = preparation.dossier.versions, ancestry = new Set();
  let version = selectedPreparationVersion();
  while (version && !ancestry.has(version.id)) { ancestry.add(version.id); version = versions.find(item => item.id === version.parent_id); }
  const suggestions = (preparation.dossier.suggestions || []).filter(item => ancestry.has(item.source_version_id) && (item.status === 'accepted' || !preparation.dossier.tasks?.find(task => task.id === item.source_task_id)?.stale));
  $('prep-suggestions').innerHTML = suggestions.length ? `<div class="prep-result-heading"><h3>可核对的改写 · ${suggestions.length}</h3>${prepFoldControls('prep-suggestions')}</div>` + suggestions.map(item => `<details class="prep-suggestion" data-prep-disclosure="suggestion-${escAttr(item.id)}"><summary>${prepAdviceSummary(item, '改写建议')}<span class="field-help">${item.status === 'accepted' ? '已采纳' : item.applicable === false ? '需核对原文 / 材料' : '待核对'}</span></summary><div class="prep-disclosure-body"><dl><dt>原文</dt><dd>${esc(item.target)}</dd><dt>建议改写</dt><dd>${esc(item.replacement)}</dd><dt>理由</dt><dd>${esc(item.reason)}</dd></dl>${item.anchor_adjusted ? '<p class="field-help">已按唯一对应的原文恢复换行和空格，未模糊匹配文字。</p>' : ''}${item.warning ? `<p class="skip-note">${esc(item.warning)}</p>` : ''}<button class="btn secondary compact" type="button" data-accept-suggestion="${escAttr(item.id)}" ${item.status === 'accepted' || item.applicable === false || preparation.busy || preparation.dossier.stale || preparation.versionId !== preparation.dossier.current_version_id ? 'disabled' : ''}>${item.status === 'accepted' ? '已采纳' : item.applicable === false ? '需补充信息，暂不可采纳' : '核对真实性并采纳'}</button></div></details>`).join('') : '<p class="field-help">诊断后会显示有原文对照的改写建议；若没有必要改写，可先按内容建议补充材料。每次采纳生成新版本。</p>';
  if (suggestions.length && preparation.versionId !== preparation.dossier.current_version_id) $('prep-suggestions').insertAdjacentHTML('beforeend', '<p class="field-help">当前查看历史版本。请先保存为新的当前版本，再采纳建议，避免修改其他版本分支。</p>');
}

function prepEvidence(items) { return (items || []).map(item => `<blockquote>${esc(item.quote)}<small>来源：${esc(item.source_id === 'resume' ? '所选简历' : item.source_id === 'jd' ? '岗位要求（不代表个人经历）' : item.source_id)}</small></blockquote>`).join(''); }
function renderPreparationRequirements() {
  const result = prepResult('recruitment');
  if (!result) { $('prep-requirements').innerHTML = '<p class="prep-empty">尚无此版本的有效岗位要求分析。</p>'; return; }
  const status = {supported:'已有材料证据', needs_verification:'需要验证', missing:'缺少材料'};
  $('prep-requirements').innerHTML = `<div class="prep-table-scroll"><table class="prep-table"><thead><tr><th>岗位要求</th><th>覆盖状态</th><th>材料证据与说明</th></tr></thead><tbody>${result.requirements.map(item => `<tr><td><strong>${esc(item.requirement)}</strong><small>${item.priority === 'must' ? '必备' : '加分'}</small><p>JD 原文：${esc(item.source_quote)}</p></td><td>${esc(status[item.status] || item.status)}</td><td>${prepEvidence(item.evidence)}<p>${esc(item.note)}</p></td></tr>`).join('')}</tbody></table></div>${prepMissing(result.missing_information)}`;
}
function renderPreparationFacts() {
  const facts = preparation.dossier.facts || [];
  $('prep-facts').innerHTML = facts.length ? facts.map(fact => `<article class="prep-fact"><div class="section-heading"><h3>${({pending:'待确认', confirmed:'已确认', rejected:'已拒绝', superseded:'已由修订替代'})[fact.status] || esc(fact.status)}</h3><span class="field-help">${esc(fact.source?.kind || '材料来源')}</span></div><label for="fact-${escAttr(fact.id)}">素材内容</label><textarea id="fact-${escAttr(fact.id)}" rows="3" maxlength="12000" data-fact-text="${escAttr(fact.id)}" data-original="${escAttr(fact.text)}" ${['rejected','superseded'].includes(fact.status) ? 'disabled' : ''}>${esc(fact.text)}</textarea><details><summary>查看原始引用</summary><blockquote>${esc(fact.source_quote || fact.source?.quote || '未提供引用')}</blockquote><p class="field-help">${esc([fact.source?.session_id && `会话 ${fact.source.session_id}`,fact.source?.question_id && `题目 ${fact.source.question_id}`,fact.source?.attempt && `第 ${fact.source.attempt} 次作答`,fact.source_version_id && `简历版本 ${fact.source_version_id}`].filter(Boolean).join(' · '))}</p></details>${['pending','confirmed'].includes(fact.status) ? `<div class="prep-inline-actions"><button class="btn secondary compact" type="button" data-fact-id="${escAttr(fact.id)}" data-fact-decision="confirm">${fact.status === 'confirmed' ? '保存修订为待确认' : '确认 / 保存修改'}</button><button class="btn secondary compact" type="button" data-fact-id="${escAttr(fact.id)}" data-fact-decision="reject">拒绝此素材</button></div>` : ''}</article>`).join('') : '<p class="prep-empty">尚无经历素材。面试结束后可提取原回答，再在这里逐项确认。</p>';
  $('prep-facts').querySelectorAll('[data-fact-text]').forEach(input => input.addEventListener('input', updatePreparationDirty));
}
function renderPreparationMaterials() {
  const result = prepResult('materials');
  if (!result) { $('prep-materials').innerHTML = '<p class="prep-empty">可点击“整理练习素材”，生成带来源的自我介绍和经历提纲。</p>'; return; }
  $('prep-materials').innerHTML = `<h3>自我介绍草稿</h3><p class="prep-preserve">${esc(result.self_introduction)}</p><div class="prep-material-list">${result.materials.map(item => `<article><h3>${esc(item.title)}</h3><dl>${[['situation','背景'],['task','目标'],['action','个人行动'],['result','结果']].map(([key,label]) => `<dt>${label}</dt><dd>${esc(item[key] || '待你补充真实信息')}</dd>`).join('')}</dl>${prepEvidence(item.evidence)}</article>`).join('')}</div>${prepMissing(result.missing_information)}`;
}
function renderPreparationHistory() {
  const tasks = prepTasks(null, false).filter(task => task.result && (task.stale || task.version_id !== preparation.versionId));
  $('prep-history').innerHTML = tasks.length ? tasks.map(task => `<details class="prep-history-item"><summary>${esc(PREP_TASKS[task.kind] || task.kind)} · ${esc(preparation.dossier.versions.find(version => version.id === task.version_id)?.name || '旧版本')} · ${task.stale ? '输入已过期' : '其他版本'}</summary><pre class="prep-source">${esc(JSON.stringify(task.result, null, 2))}</pre></details>`).join('') : '<p class="field-help">暂时没有历史准备成果。</p>';
}

async function savePreparationVersion() {
  if (!preparation.dossier || preparation.busy) return;
  if ([...document.querySelectorAll('[data-fact-text]')].some(input => input.value !== input.dataset.original)) { prepMessage('请先保存或放弃素材草稿，再保存简历版本。', true); return; }
  const resume = $('prep-resume').value.trim();
  if (!resume) { prepMessage('请先填写简历正文。', true); return; }
  const name = $('prep-version-name').value.trim() || `简历版本 ${preparation.dossier.versions.length + 1}`;
  await mutatePreparation(`/versions`, {revision: preparation.dossier.revision, parent_id: preparation.versionId, name, resume}, '已保存为新版本，原版本保持不变。', true);
}
async function acceptPreparationSuggestion(id) {
  if (preparation.dirty) { prepMessage('请先保存或放弃编辑，再采纳建议。', true); return; }
  const item = preparation.dossier.suggestions.find(suggestion => suggestion.id === id);
  if (!item || item.applicable === false || item.status === 'accepted') return;
  if (!window.confirm('请核对原文和建议：你确认改写中的职责、贡献、技能和数字均真实，没有虚构或夸大吗？确认后将保存为新版本。')) return;
  await mutatePreparation(`/suggestions/${encodeURIComponent(id)}/accept`, {revision: preparation.dossier.revision, truth_confirmed: true}, '已采纳并生成新版本。其他未冲突的建议可继续核对采纳。', true);
}
async function decidePreparationFact(id, decision) {
  const fact = preparation.dossier.facts.find(item => item.id === id);
  if (!fact) return;
  const otherEdits = $('prep-resume').value !== selectedPreparationVersion().resume || $('prep-version-name').value.trim() || [...document.querySelectorAll('[data-fact-text]')].some(input => input.dataset.factText !== id && input.value !== input.dataset.original);
  if (otherEdits) { prepMessage('请先保存或放弃其他简历 / 素材草稿，再操作这条素材。', true); return; }
  const text = $(`fact-${id}`).value.trim();
  if (decision === 'confirm' && !text) { prepMessage('素材内容不能为空。', true); return; }
  const changed = text !== fact.text;
  if (!window.confirm(decision === 'reject' ? '拒绝这条素材？它将不再用于准备任务。' : changed ? '将修改保存为新的待确认素材？保存后请再次核对并明确确认。' : '确认这段内容来自你的真实经历，并允许用于后续岗位准备吗？')) return;
  await mutatePreparation(`/facts/${encodeURIComponent(id)}/decision`, {revision: preparation.dossier.revision, decision, text: decision === 'reject' ? '' : text}, changed && decision === 'confirm' ? '修订已进入待确认，请核对新条目后再次确认。' : decision === 'confirm' ? '素材已确认。后续分析将使用确认后的内容。' : '已拒绝该素材。', false);
}
async function mutatePreparation(path, body, message, selectNewVersion) {
  if (preparation.busy) return;
  const context = prepContext();
  preparation.busy = true;
  stopPreparationPoll();
  renderPreparation(false);
  try {
    const response = await postJSON(`/api/preparation/${encodeURIComponent(context.jobId)}${path}`, body);
    if (!isPrepContext(context)) return;
    preparation.dirty = false;
    if (selectNewVersion && response.version) preparation.versionId = response.version.id;
    preparation.epoch++;
    const refreshed = await refreshPreparation(true);
    prepMessage(refreshed ? message : '保存成功，但暂时无法刷新档案。请点击“刷新档案”，不要重复保存。', !refreshed);
  } catch (error) {
    if (isPrepContext(context)) prepMessage(`${error.message}。如提示版本冲突，请先保留草稿再刷新档案。`, true);
  } finally {
    preparation.busy = false;
    if (context.jobId === preparation.job?.id) { renderPreparation(false); schedulePreparationPoll(); }
  }
}

async function runPreparationTask(kind) {
  if (preparation.busy || !preparation.dossier || preparation.taskRequests.has(kind)) return;
  if (preparation.dirty) { prepMessage('请先保存或放弃草稿，再分析所选的已保存版本。', true); return; }
  if (preparation.dossier.stale) { prepMessage('岗位已更新，请先将当前岗位快照保存为新版本。', true); return; }
  if (state.capabilities.analysis === false || !state.serviceReady && !state.capabilities.analysis) {
    prepMessage('本地资料仍可保存。请在系统设置中配置分析模型后运行分析。', true);
    $(`prep-task-${kind}`).innerHTML = '<button class="btn secondary compact" data-prep-settings type="button">前往系统设置</button>';
    return;
  }
  const context = prepContext();
  preparation.taskRequests.add(kind);
  const button = document.querySelector(`[data-prep-task="${kind}"]`);
  button.disabled = true;
  try {
    await postJSON(`/api/preparation/${encodeURIComponent(context.jobId)}/tasks`, {kind, version_id: context.versionId, revision: preparation.dossier.revision});
    if (!isPrepContext(context)) return;
    prepMessage('任务已提交，可留在本页核对其他材料；关闭页面不会自动重试任务。');
    await refreshPreparation(false);
  } catch (error) { if (isPrepContext(context)) prepMessage(error.message, true); }
  finally { preparation.taskRequests.delete(kind); if (isPrepContext(context)) renderPreparationTasks(); }
}
async function actOnPreparationTask(action, id) {
  if (!['cancel','retry'].includes(action) || preparation.busy) return;
  if (action === 'retry' && preparation.dirty) { prepMessage('请先保存或放弃草稿再重试。', true); return; }
  const context = prepContext();
  try {
    await postJSON(`/api/preparation/${encodeURIComponent(context.jobId)}/tasks/${encodeURIComponent(id)}/${action}`, {});
    if (!isPrepContext(context)) return;
    prepMessage(action === 'cancel' ? '任务已取消。已经发送的远程请求仍可能产生用量。' : '已按原输入重试任务。');
    await refreshPreparation(false);
  } catch (error) { if (isPrepContext(context)) prepMessage(error.message, true); }
}
function schedulePreparationPoll() {
  stopPreparationPoll();
  if (state.view !== 'preparation' || preparation.busy || !prepTasks(null, false).some(task => task.status === 'running')) return;
  const context = prepContext();
  preparation.timer = setTimeout(async () => {
    if (!isPrepContext(context) || state.view !== 'preparation') return;
    try {
      await Promise.all(prepTasks(null, false).filter(task => task.status === 'running').map(task => getJSON(`/api/preparation/${encodeURIComponent(context.jobId)}/tasks/${encodeURIComponent(task.id)}`)));
      if (isPrepContext(context)) await refreshPreparation(false);
    } catch (error) {
      if (isPrepContext(context)) { prepMessage(`暂时无法读取任务进度：${error.message}。可稍后点击刷新档案，不会自动重复分析。`, true); stopPreparationPoll(); }
    }
  }, 1200);
}
