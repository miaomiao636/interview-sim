/* Saved job dossiers and per-interview choices. A run never edits the preset. */
const JOB_FIELDS = {company:'job-company',city:'job-city',target_role:'job-role',salary_min:'job-salary-min',salary_max:'job-salary-max',salary_months:'job-months',education:'job-education',responsibilities:'job-duties',requirements:'job-requirements',company_context:'job-company-context',resume:'job-resume',coaching_goal:'job-goal'};
let editingJobId = null;
let runJob = null;
let runVersions = [];
let runSetupRevision = 0;
let jobParseRevision = 0;

function bindJobs() {
  const container = document.createElement('div');
  const upload = (id, target, label) => `<label class="btn secondary compact" for="${id}">${label}</label><input id="${id}" class="sr-only" type="file" accept=".txt,.md,.docx,.pdf,.png,.jpg,.jpeg,.webp" data-target="${target}">`;
  container.innerHTML = `
  <dialog id="job-editor" class="workbench-dialog" aria-labelledby="job-editor-title">
    <header class="dialog-header"><h2 id="job-editor-title">创建岗位</h2><button class="dialog-close" type="button" data-close="job-editor" aria-label="关闭岗位编辑">×</button></header>
    <form id="job-editor-form">
      <div class="dialog-body">
        <section class="jd-recognition"><label for="job-source">粘贴完整 JD，或导入文件后识别岗位信息</label><textarea id="job-source" rows="5" maxlength="30000" placeholder="支持 JD 截图、Word、PDF 和文字；识别后可核对修改。"></textarea><div class="preset-actions">${upload('job-source-file','job-source','导入 JD 文件')}<button id="btn-parse-job" class="btn primary" type="button">AI 识别岗位</button></div><p id="job-parse-status" role="status" class="field-help"></p></section>
        <div class="form-grid two-columns">
          <div class="form-group"><label for="job-company">公司名称 *</label><input id="job-company" class="text-input" required maxlength="160" placeholder="例如：公司全称"></div>
          <div class="form-group"><label for="job-city">所在城市</label><input id="job-city" class="text-input" maxlength="80" placeholder="例如：无锡"></div>
        </div>
        <div class="form-group"><label for="job-role">岗位名称 *</label><input id="job-role" class="text-input" required maxlength="160" placeholder="例如：AI 应用工程师"></div>
        <fieldset class="salary-fields"><legend>薪资范围（选填）</legend><label>月薪下限 · K<input id="job-salary-min" type="number" min="0" max="1000" step="0.1" class="text-input" placeholder="起始"></label><label>月薪上限 · K<input id="job-salary-max" type="number" min="0" max="1000" step="0.1" class="text-input" placeholder="封顶"></label><label>薪数<input id="job-months" type="number" min="1" max="36" class="text-input" placeholder="如 13"></label></fieldset>
        <div class="form-group"><label for="job-education">学历要求</label><input id="job-education" class="text-input" list="education-options" maxlength="80"><datalist id="education-options"><option>不限</option><option>大专</option><option>本科</option><option>硕士</option><option>博士</option></datalist></div>
        <div class="form-group"><label for="job-duties">具体职责 *</label><textarea id="job-duties" rows="4" required maxlength="15000" placeholder="负责哪些业务、产品或项目"></textarea></div>
        <div class="form-group"><label for="job-requirements">任职要求</label><textarea id="job-requirements" rows="3" maxlength="15000"></textarea></div>
        <div class="form-group"><label for="job-company-context">公司背景（选填）</label><textarea id="job-company-context" rows="3" maxlength="6000" placeholder="业务、产品、客户；可从 JD 或官网摘录，帮助生成公司相关问题。"></textarea><p class="field-help">面试会结合公司名称和这些材料；不明确的信息会先向你了解。</p></div>
        <div class="form-group"><div class="section-heading"><label for="job-resume">此岗位使用的简历 *</label>${upload('job-resume-file','job-resume','导入简历')}</div><textarea id="job-resume" rows="5" required maxlength="50000" placeholder="不同岗位可以保存不同版本的真实简历"></textarea></div>
        <div class="form-group"><label for="job-goal">此岗位训练目标</label><input id="job-goal" class="text-input" maxlength="500" placeholder="例如：练习公司业务理解与项目深挖"></div>
        <p id="job-save-status" class="inline-status" role="status"></p>
      </div>
      <footer class="dialog-footer"><button class="btn secondary" type="button" data-close="job-editor">取消</button><button id="btn-save-preset" class="btn primary" type="submit">保存岗位</button></footer>
    </form>
  </dialog>
  <dialog id="interview-setup" class="workbench-dialog setup-dialog" aria-labelledby="interview-setup-title">
    <header class="dialog-header"><h2 id="interview-setup-title">模拟面试配置</h2><button type="button" class="dialog-close" data-close="interview-setup" aria-label="关闭面试配置">×</button></header>
    <form id="run-form"><div class="dialog-body">
      <div id="run-job-summary"></div>
      <div class="form-group run-version-field"><label for="run-resume-version">本次面试使用的简历版本</label><select id="run-resume-version" required disabled></select><p id="run-version-note" class="field-help">正在读取版本…</p></div>
      <fieldset class="choice-field"><legend>面试官角色</legend><div class="choice-grid">${[['HR','HR'],['上级','直属上级'],['CEO','CEO'],['CTO','CTO'],['业务负责人','业务负责人'],['技术负责人','技术负责人'],['英文面','英文面试官']].map(([v,label])=>choice('run-persona',v,label)).join('')}</div></fieldset>
      <fieldset class="choice-field"><legend>面试官性别 / 声音</legend><div class="choice-grid">${choice('run-gender','女性','女性')}${choice('run-gender','男性','男性')}</div><p class="field-help">使用对应音色，不影响提问标准和评分。自定义语音服务采用设置中的音色 ID。</p></fieldset>
      <fieldset class="choice-field"><legend>面试难度</legend><div class="choice-grid difficulty-grid">${choice('run-difficulty','初级','入门','结构与动机')}${choice('run-difficulty','标准','正常','专业基础与案例')}${choice('run-difficulty','进阶','进阶','深入追问与权衡')}${choice('run-difficulty','压力','压力面','挑战与连续质询')}</div></fieldset>
      <div class="manual-note"><strong>手动确认回答</strong><span>说完后结束录音，检查文字并发送，面试官再继续提问。</span></div>
      <p id="run-status" class="inline-status" role="status"></p>
    </div><footer class="dialog-footer"><button id="btn-run-interview" class="btn primary wide" type="submit">开始面试</button></footer></form>
  </dialog>`;
  document.body.append(container);
  container.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => $(button.dataset.close).close()));
  container.querySelectorAll('input[type=file]').forEach(input => input.addEventListener('change', importDocument));
  $('job-editor').addEventListener('close', () => { jobParseRevision++; });
  $('interview-setup').addEventListener('close', () => { runSetupRevision++; });
  $('interview-setup').addEventListener('cancel', event => { if (state.isBusy) event.preventDefault(); });
  $('run-resume-version').addEventListener('change', updateRunVersionNote);
  $('job-editor-form').addEventListener('submit', savePreset);
  $('btn-parse-job').addEventListener('click', parseJob);
  $('run-form').addEventListener('submit', startPresetInterview);
  $('btn-new-preset').addEventListener('click', () => openJobEditor());
  $('preset-list').addEventListener('click', event => {
    const button = event.target.closest('[data-preset-id]');
    if (!button || state.isBusy || state.isRecording || state.voice?.processing) return;
    const item = state.presets.find(job => job.id === button.dataset.presetId);
    if (button.dataset.action === 'edit') openJobEditor(item);
    else if (button.dataset.action === 'prepare') openPreparation(item);
    else openInterviewSetup(item);
  });
}

function choice(name, value, label, detail='') {
  return `<label class="choice"><input type="radio" name="${name}" value="${value}" required><span><strong>${label}</strong>${detail ? `<small>${detail}</small>` : ''}</span></label>`;
}

function salaryLabel(item) {
  if (item.salary_min == null && item.salary_max == null) return '薪资未填写';
  return `${item.salary_min ?? '—'}–${item.salary_max ?? '—'}K${item.salary_months ? ` · ${item.salary_months}薪` : ''}`;
}

async function loadPresets() {
  try {
    state.presets = await getJSON('/api/presets');
    $('preset-list').innerHTML = state.presets.length ? state.presets.map(item => `<article class="preset-card job-card"><div class="job-card-heading"><div class="company-mark" aria-hidden="true">${esc((item.company || item.target_role).slice(0,1))}</div><div><h3>${esc(item.target_role)}</h3><p>${esc([item.company,item.city].filter(Boolean).join(' · ') || item.name)}</p></div><button class="job-edit-link" type="button" data-preset-id="${escAttr(item.id)}" data-action="edit">编辑岗位</button></div><div class="job-card-meta"><span>${esc(item.education || '学历未填写')}</span><span>${esc(salaryLabel(item))}</span></div><p class="job-card-goal">${esc(item.coaching_goal || '从你的经历出发，练习这个岗位的真实面试')}</p><div class="preset-actions job-primary-actions"><button class="btn secondary" type="button" data-preset-id="${escAttr(item.id)}" data-action="prepare">准备这个岗位</button><button class="btn primary" type="button" data-preset-id="${escAttr(item.id)}" data-action="start">开始面试</button></div></article>`).join('') : '<div class="jobs-empty"><strong>先创建一个目标岗位</strong><p>导入 JD、保存简历，之后可以按需准备，也可直接开始面试。</p></div>';
  } catch (_) { $('preset-list').textContent = '岗位暂时无法读取，请检查本地服务。'; }
}

function openJobEditor(item=null) {
  if (state.isBusy || state.isRecording) return;
  editingJobId = item?.id || null;
  $('job-editor-title').textContent = item ? '编辑岗位' : '创建岗位';
  $('job-editor-form').reset();
  $('job-source').value = item?.source_jd || item?.jd || '';
  for (const [key,id] of Object.entries(JOB_FIELDS)) $(id).value = item?.[key] ?? (key === 'education' ? '不限' : '');
  if (item && !item.responsibilities) $('job-duties').value = item.jd;
  $('job-save-status').textContent = '';
  $('job-parse-status').textContent = '';
  $('job-editor').showModal();
}

async function parseJob() {
  const source = $('job-source').value.trim();
  if (source.length < 10) { $('job-parse-status').textContent = '请先粘贴 JD 或导入文件。'; return; }
  const revision = ++jobParseRevision;
  $('btn-parse-job').disabled = true;
  $('job-parse-status').textContent = '正在识别公司、岗位和职责，通常需要几十秒…';
  try {
    const data = await postJSON('/api/presets/parse', {jd:source});
    if (revision !== jobParseRevision || source !== $('job-source').value.trim()) return;
    for (const [key,id] of Object.entries(JOB_FIELDS)) if (key in data) $(id).value = data[key] ?? '';
    $('job-parse-status').textContent = '已填入 JD 中的信息，请核对并补充简历后保存。';
  } catch (error) { if (revision === jobParseRevision) $('job-parse-status').textContent = error.message; }
  finally { $('btn-parse-job').disabled = false; }
}

async function savePreset(event) {
  event.preventDefault();
  const body = Object.fromEntries(Object.entries(JOB_FIELDS).map(([key,id]) => [key,$(id).value.trim()]));
  for (const key of ['salary_min','salary_max','salary_months']) body[key] = body[key] ? Number(body[key]) : null;
  body.name = `${body.company} · ${body.target_role}`.slice(0,120);
  body.source_jd = $('job-source').value.trim();
  body.jd = [`公司：${body.company}`, `岗位：${body.target_role}`,body.city && `城市：${body.city}`,`薪资：${salaryLabel(body)}`,`学历：${body.education}`,`职责：\n${body.responsibilities}`,`要求：\n${body.requirements}`,`公司背景：\n${body.company_context}`].filter(Boolean).join('\n');
  // Structured edited fields are authoritative; retain original text separately.
  const original = state.presets.find(item => item.id === editingJobId);
  body.persona = original?.persona || 'HR'; body.difficulty = original?.difficulty || '标准';
  $('btn-save-preset').disabled = true;
  try {
    await (editingJobId ? putJSON(`/api/presets/${editingJobId}`,body) : postJSON('/api/presets',body));
    await loadPresets(); $('job-editor').close(); showToast('岗位与简历已保存，选择岗位后可配置本次面试。');
  } catch (error) { $('job-save-status').textContent = error.message; }
  finally { $('btn-save-preset').disabled = false; }
}

async function openInterviewSetup(item, preferredVersionId = null) {
  if (!item) return;
  const revision = ++runSetupRevision;
  runJob = item;
  runVersions = [];
  $('run-job-summary').innerHTML = `<article class="run-job-card"><h3>${esc(item.target_role)}</h3><p>${esc([item.company,item.city].filter(Boolean).join(' · '))}</p><div class="job-card-meta"><span>${esc(item.education || '不限')}</span><span>${esc(salaryLabel(item))}</span></div><details><summary>完整岗位详情</summary><pre>${esc(item.jd)}</pre></details></article>`;
  for (const [name,value] of [['run-persona',item.persona === '业务总监' ? '业务负责人' : item.persona],['run-difficulty',item.difficulty],['run-gender','女性']]) {
    const inputs = [...document.querySelectorAll(`input[name="${name}"]`)];
    (inputs.find(input => input.value === value) || inputs[0]).checked = true;
  }
  $('run-status').textContent = '';
  $('run-resume-version').innerHTML = '<option value="">正在读取版本…</option>';
  $('run-resume-version').disabled = true;
  $('btn-run-interview').disabled = true;
  $('run-version-note').textContent = '无需先做准备；可以直接使用已有简历开始面试。';
  $('interview-setup').showModal();
  try {
    const dossier = await getJSON(`/api/preparation/${encodeURIComponent(item.id)}`);
    if (revision !== runSetupRevision || runJob.id !== item.id || !$('interview-setup').open) return;
    runVersions = dossier.versions;
    $('run-resume-version').innerHTML = runVersions.map(version => `<option value="${escAttr(version.id)}">${esc(version.name || '简历版本')}</option>`).join('');
    $('run-resume-version').value = runVersions.some(version => version.id === preferredVersionId) ? preferredVersionId : defaultPreparationVersion(dossier);
    $('run-resume-version').disabled = false;
    $('btn-run-interview').disabled = false;
    updateRunVersionNote();
  } catch (error) {
    if (revision === runSetupRevision) $('run-status').textContent = `简历版本读取失败：${error.message}。关闭后重新打开可重试。`;
  }
}

function updateRunVersionNote() {
  const version = runVersions.find(item => item.id === $('run-resume-version').value);
  $('run-version-note').textContent = version ? `使用这个版本的原文（${version.resume.length} 字）和当前岗位 JD。没有匹配的简历诊断时显示未评估，不影响面试。` : '请选择简历版本。';
}

async function startPresetInterview(event) {
  event.preventDefault();
  if (!runJob || state.isBusy) return;
  const version = runVersions.find(item => item.id === $('run-resume-version').value);
  if (!version) { $('run-status').textContent = '请等待版本读取完成并选择一个简历版本。'; return; }
  const runRevision = runSetupRevision;
  const selected = name => document.querySelector(`input[name="${name}"]:checked`).value;
  $('input-jd').value = runJob.jd; $('input-resume').value = version.resume;
  $('input-target-role').value = runJob.target_role;
  $('input-company').value = runJob.company || '';
  $('input-company-context').value = runJob.company_context || '';
  $('select-persona').value = selected('run-persona');
  $('select-difficulty').value = selected('run-difficulty');
  $('select-voice').value = selected('run-gender') === '女性' ? '茉莉' : '白桦';
  $('btn-run-interview').disabled = true;
  $('run-status').textContent = '正在结合公司、岗位和你的经历准备面试…';
  const ok = await startInterview(event, {coaching_goal:runJob.coaching_goal,interviewer_gender:selected('run-gender'),preset_id:runJob.id,resume_version_id:version.id});
  if (runRevision !== runSetupRevision) return;
  $('btn-run-interview').disabled = false;
  if (ok) $('interview-setup').close();
  else $('run-status').textContent = $('config-status').textContent;
}
