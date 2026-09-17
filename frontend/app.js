const API = "";

const state = {
  view: "config",
  sessionId: null,
  sessionEnded: false,
  blueprint: [],
  round: 0,
  lastQuestion: "",
  lastReport: null,
  isBusy: false,
  serviceReady: false,
  settings: null,
  inputMode: "text",
  timerInterval: null,
  timerSeconds: 0,
  isRecording: false,
  ttsAbortController: null,
  playbackContext: null,
  playbackSources: new Set(),
  voice: null,
  activeQuestionId: null,
  activeAttempt: 1,
  activeIsRetry: false,
  sessionEpoch: 0,
  reportEpoch: 0,
  reportJobIdentity: null,
  pendingOperation: null,
  nextQuestionJob: null,
  conversationController: null,
  presets: [],
  presetId: null,
  reportTimer: null,
  capabilities: {},
  reportPoll: null,
  reportSessionId: null,
  reportLearning: null,
};

const $ = (id) => document.getElementById(id);

function invalidateSessionContext(sessionId = state.sessionId) {
  state.sessionEpoch++;
  state.reportEpoch++;
  state.sessionId = sessionId;
  state.reportSessionId = null;
  state.reportJobIdentity = null;
  clearTimeout(state.reportLearning?.timer);
  clearTimeout(state.reportPoll);
  state.reportPoll = null;
  state.conversationController?.abort();
  state.conversationController = null;
  state.pendingOperation = null;
  state.nextQuestionJob = null;
  state.activeQuestionId = null;
  state.activeAttempt = 1;
  state.activeIsRetry = false;
  stopTimer();
  stopSpeaking();
  return sessionContext();
}
function sessionContext() { return {sessionId: state.sessionId, epoch: state.sessionEpoch, questionId: state.activeQuestionId, attempt: state.activeAttempt}; }
function isSessionContext(context) { return context.sessionId === state.sessionId && context.epoch === state.sessionEpoch; }
function applyActiveQuestion(active) {
  state.activeQuestionId = active?.question_id || null;
  state.activeAttempt = active?.attempt || 1;
  state.activeIsRetry = Boolean(active?.is_retry);
  state.lastQuestion = active?.question || '';
}
function operationFor(kind, payload) {
  const fingerprint = JSON.stringify({kind, session: state.sessionId, question: state.activeQuestionId, attempt: state.activeAttempt, payload});
  if (state.pendingOperation?.fingerprint !== fingerprint) state.pendingOperation = {fingerprint, id: crypto.randomUUID()};
  return state.pendingOperation.id;
}
function clearAnswerDrafts() {
  $('input-answer').value = '';
  $('voice-transcript').value = '';
  $('subtitle-text').textContent = '等待录音…';
  state.voice = null;
}
function renderNextQuestionRecovery() {
  const running = state.nextQuestionJob?.status === 'running';
  const canRecover = !state.activeQuestionId && !state.sessionEnded && ['running','failed','interrupted'].includes(state.nextQuestionJob?.status);
  $('next-question-recovery').classList.toggle('hidden', !canRecover);
  $('next-question-status').textContent = !canRecover ? '' : running ? '回答已保存，后台仍在生成下一题。稍后刷新即可读取结果，不会再次调用模型。' : `回答已保存。${state.nextQuestionJob.error || '下一题暂未生成，可重新生成。'}`;
  $('btn-recover-question').textContent = running ? '刷新当前问题' : '重新生成下一题';
  $('btn-recover-question').disabled = state.isBusy || (!running && !state.serviceReady);
}

document.addEventListener("DOMContentLoaded", () => {
  bindNavigation();
  bindConfig();
  bindJobs();
  bindPreparation();
  bindInterview();
  bindReport();
  bindHistory();
  bindSettings();
  updateCharacterCount("input-jd", "jd-count");
  updateCharacterCount("input-resume", "resume-count");
  Promise.allSettled([checkServiceHealth(), loadSettings()]).then(restorePendingReport);
  loadPresets();
});

async function checkServiceHealth() {
  const container = $("service-status");
  try {
    const health = await getJSON("/api/health");
    state.capabilities = health.capabilities || {};
    const configured = health.capabilities ? Boolean(health.capabilities.chat && health.capabilities.analysis) : Boolean(health.api_key_configured);
    applyServiceReadiness(configured);
    $("service-status-title").textContent = configured ? "模型配置已加载" : "需要配置 API Key";
    $("service-status-detail").textContent = configured
      ? "记录保存在本机；分析和语音使用各自配置的模型服务。"
      : "本地服务已运行；请前往“系统设置”完成模型连接。";
    container.classList.toggle("warning", !configured);
  } catch (_) {
    applyServiceReadiness(false);
    $("service-status-title").textContent = "本地服务未连接";
    $("service-status-detail").textContent = "请确认 FastAPI 服务已启动后刷新页面。";
    container.classList.add("warning");
  }
}

function applyServiceReadiness(ready) {
  state.serviceReady = ready;
  $("btn-start").disabled = !ready;
  $("btn-preview-voice").disabled = !ready || state.capabilities.tts === false;
  $("auto-speak").disabled = !ready || state.capabilities.tts === false;
  $("btn-replay").disabled = !ready || state.capabilities.tts === false;
  $("btn-send").disabled = !ready || state.isBusy;
  $("btn-record").disabled = !ready || state.isBusy;
  $("btn-end").disabled = !state.sessionId || state.isBusy;
  if (!ready) $("auto-speak").checked = false;
  syncVoiceUI();
}

function bindNavigation() {
  document.querySelectorAll(".nav-btn").forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
}

function bindConfig() {
  $("interview-config-form").addEventListener("submit", event => startInterview(event));
  $("btn-preview-voice").addEventListener("click", previewVoice);
  $("select-persona").addEventListener("change", syncVoiceWithPersona);

  [["input-jd", "jd-count"], ["input-resume", "resume-count"]].forEach(([inputId, countId]) => {
    $(inputId).addEventListener("input", () => updateCharacterCount(inputId, countId));
  });

  document.querySelectorAll('input[type="file"][data-target]').forEach((input) => {
    input.addEventListener("change", importDocument);
  });
}

function bindInterview() {
  $("btn-mode-text").addEventListener("click", () => setInputMode("text"));
  $("btn-mode-voice").addEventListener("click", () => setInputMode("voice"));
  $("btn-send").addEventListener("click", sendTextAnswer);
  $("btn-record").addEventListener("click", toggleRecording);
  $("btn-send-voice").addEventListener("click", async () => {
    const answer = $("voice-transcript").value.trim();
    if (!answer || state.voice?.recording || state.voice?.processing || state.voice?.stopping) return;
    if (state.voice?.failed && !window.confirm('仍有片段未成功识别。确认以当前文字发送，并放弃剩余音频吗？')) return;
    await submitAnswer(answer);
  });
  $("btn-retry-asr").addEventListener("click", () => state.voice?.retry());
  $("voice-transcript").addEventListener("input", syncVoiceUI);
  $("btn-skip").addEventListener("click", skipQuestion);
  $("btn-recover-question").addEventListener("click", recoverNextQuestion);
  $("btn-end").addEventListener("click", endInterview);
  $("btn-end-only").addEventListener("click", () => confirmEndInterview(false));
  $("btn-end-report").addEventListener("click", () => confirmEndInterview(true));
  $("btn-cancel-end").addEventListener("click", () => $("end-interview-dialog").close());
  $("end-interview-dialog").addEventListener("cancel", event => { if (state.isBusy) event.preventDefault(); });
  $("btn-replay").addEventListener("click", () => speakText(state.lastQuestion));
  $("btn-stop-audio").addEventListener("click", stopSpeaking);
  $("input-answer").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendTextAnswer();
    }
  });
}

function bindReport() {
  $("btn-export-md").addEventListener("click", () => exportReport("markdown"));
  $("btn-export-json").addEventListener("click", () => exportReport("json"));
  $("btn-print").addEventListener("click", () => window.print());
  $("report-content").addEventListener("click", (event) => {
    const button = event.target.closest("[data-retry-question]");
    if (button) retryQuestion(button.dataset.retryQuestion);
    const action = event.target.closest('[data-report-action]');
    if (action && !action.disabled) handleReportLearningAction(action.dataset.reportAction);
  });
}

function bindHistory() {
  $("btn-refresh-history").addEventListener("click", loadHistory);
  $("history-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-session-id]");
    if (button) viewSession(button.dataset.sessionId);
  });
}

function bindSettings() {
  $("settings-form").addEventListener("submit", saveSettings);
}

function switchView(name) {
  const targetButton = document.querySelector(`.nav-btn[data-view="${name}"]`);
  if (!targetButton || targetButton.disabled) return false;
  if (!beforePreparationNavigation(name)) return false;

  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach((button) => button.classList.remove("active"));
  $(`view-${name}`).classList.add("active");
  targetButton.classList.add("active");
  state.view = name;
  if (name === "history") loadHistory();
  if (name === "settings" && !state.settings) loadSettings();
  if (name === "preparation") onPreparationViewShown();
  if (name === 'report' && $('report-learning')) refreshReportLearning();
  window.scrollTo({ top: 0, behavior: "smooth" });
  return true;
}

function enableView(name, enabled = true) {
  const button = document.querySelector(`.nav-btn[data-view="${name}"]`);
  if (button) button.disabled = !enabled;
}

async function startInterview(event, options = {}) {
  event.preventDefault();
  if (state.isBusy || state.isRecording || state.voice?.processing) return;
  if (!state.serviceReady) {
    showInlineStatus("请先在“系统设置”中完成模型连接。", true);
    switchView("settings");
    return;
  }
  const jd = $("input-jd").value.trim();
  const resume = $("input-resume").value.trim();
  if (!jd || !resume) {
    showInlineStatus("请先填写岗位描述和个人简历。", true);
    return;
  }

  const context = invalidateSessionContext(null);
  setConfigBusy(true);
  state.isBusy = true;
  showInlineStatus("正在解析岗位要求，并建立专属面试蓝图…");
  try {
    const data = await postJSON("/api/plan", {
      jd,
      resume,
      target_role: $("input-target-role").value.trim(),
      coaching_goal: options.coaching_goal || state.settings?.profile?.coaching_goal || "",
      company: $('input-company').value.trim(),
      company_context: $('input-company-context').value.trim(),
      interviewer_gender: options.interviewer_gender || '',
      voice: $('select-voice').value,
      persona: $("select-persona").value,
      difficulty: $("select-difficulty").value,
      ...(options.preset_id && options.resume_version_id ? {preset_id: options.preset_id, resume_version_id: options.resume_version_id} : {}),
    });
    if (!isSessionContext(context)) return false;
    if (!data.active_question?.question_id || !data.active_question?.question) throw new Error("当前问题未生成，请补充材料后重试。");

    state.sessionId = data.session_id;
    state.sessionEnded = false;
    state.blueprint = data.blueprint;
    state.round = data.question_cursor || 1;
    applyActiveQuestion(data.active_question);
    state.lastReport = null;

    $("interview-persona").textContent = $("select-persona").value;
    $("interview-difficulty").textContent = $("select-difficulty").value;
    $("chat-area").replaceChildren();
    clearAnswerDrafts();
    addSystemMessage("面试开始。回答后请明确发送；录音停顿不会自动提交。");
    addChatMessage("interviewer", state.lastQuestion);
    updateInterviewMeta();
    renderNextQuestionRecovery();
    enableView("interview");
    enableView("report", false);
    startTimer(true);
    switchView("interview");
    setConversationBusy(false);
    if ($("auto-speak").checked) speakText(state.lastQuestion);
    return true;
  } catch (error) {
    if (state.sessionEpoch === context.epoch) showInlineStatus(error.message || "生成题纲失败，请稍后重试。", true);
    return false;
  } finally {
    if (state.sessionEpoch === context.epoch) {
      state.isBusy = false;
      setConfigBusy(false);
      setConversationBusy(false);
    }
  }
}

async function sendTextAnswer() {
  const answer = $("input-answer").value.trim();
  if (!answer) return;
  await submitAnswer(answer);
}

async function submitAnswer(answer) {
  if (!state.sessionId || !state.serviceReady || state.isBusy || state.isRecording || !state.activeQuestionId || !answer.trim()) return;
  const context = {...sessionContext(), wasRetry: state.activeIsRetry};
  const operationId = operationFor('answer', answer);
  state.isBusy = true;
  setConversationBusy(true);
  stopSpeaking();
  const thinking = addSystemMessage('正在保存回答…');
  try {
    await streamQuestionRequest(context, '/api/chat', {session_id: context.sessionId, asr_text: answer, question_id: context.questionId, attempt: context.attempt, operation_id: operationId});
    if (!isSessionContext(context)) return;
    const session = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}`);
    if (!isSessionContext(context)) return;
    clearAnswerDrafts();
    state.pendingOperation = null;
    renderLiveSession(session, context.wasRetry);
    if (state.activeQuestionId && $('auto-speak').checked) speakText(state.lastQuestion);
  } catch (error) {
    if (!isSessionContext(context)) return;
    addSystemMessage(`本轮未完成：${error.message}`);
    try {
      const saved = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}`);
      if (!isSessionContext(context)) return;
      const accepted = (saved.turns || []).some(turn => turn.question_id === context.questionId && (turn.attempt || 1) === context.attempt && turn.answer === answer);
      if (accepted) {
        clearAnswerDrafts();
        state.pendingOperation = null;
      }
      renderLiveSession(saved, context.wasRetry && accepted);
      if (!accepted) showToast('本次内容尚未保存，草稿仍保留。请核对当前问题，重试同一内容不会重复保存。', true);
    } catch (_) { if (isSessionContext(context)) showToast('服务连接中断，草稿仍保留；恢复连接后可重试。', true); }
  } finally {
    thinking.remove();
    if (isSessionContext(context)) {
      state.isBusy = false;
      state.conversationController = null;
      setConversationBusy(false);
      if (state.activeQuestionId) $('input-answer').focus();
    }
  }
}

async function streamQuestionRequest(context, path, body) {
  const controller = new AbortController();
  state.conversationController = controller;
  const response = await fetch(`${API}${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: controller.signal});
  if (!response.ok) throw new Error(await responseError(response, '生成下一题失败'));
  if (!isSessionContext(context)) return;
  let message = null;
  await readTextStream(response, chunk => {
    if (!isSessionContext(context)) return false;
    if (!chunk) return;
    if (!message) message = addChatMessage('interviewer', '', true);
    message.querySelector('.message-body').textContent += chunk;
    scrollChat();
  });
}

async function recoverNextQuestion() {
  if (!state.sessionId || state.activeQuestionId || state.isBusy || state.sessionEnded) return;
  const readOnly = state.nextQuestionJob?.status === 'running';
  if (!readOnly && !['failed','interrupted'].includes(state.nextQuestionJob?.status)) return;
  const context = sessionContext();
  state.isBusy = true;
  setConversationBusy(true);
  try {
    if (!readOnly) {
      const operationId = operationFor('recover', state.nextQuestionJob?.generation);
      await streamQuestionRequest(context, `/api/sessions/${encodeURIComponent(context.sessionId)}/next-question`, {operation_id: operationId});
      if (!isSessionContext(context)) return;
    }
    const session = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}`);
    if (!isSessionContext(context)) return;
    state.pendingOperation = null;
    renderLiveSession(session);
    if (state.activeQuestionId) startTimer(false);
    if (!readOnly && state.activeQuestionId && $('auto-speak').checked) speakText(state.lastQuestion);
  } catch (error) {
    if (!isSessionContext(context)) return;
    try {
      const session = await getJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}`);
      if (isSessionContext(context)) renderLiveSession(session);
    } catch (_) { /* Retain the previous failed job and operation identity. */ }
    if (isSessionContext(context)) showToast(error.message || '下一题仍未生成，可稍后重试或结束面试。', true);
  } finally {
    if (isSessionContext(context)) { state.isBusy = false; state.conversationController = null; setConversationBusy(false); }
  }
}

function renderLiveSession(session, retryFinished = false) {
  state.sessionEnded = ['ended','completed'].includes(session.status);
  state.nextQuestionJob = session.next_question_job || null;
  state.round = session.question_cursor || session.turns?.length || 1;
  applyActiveQuestion(session.active_question);
  $('chat-area').replaceChildren();
  (session.transcript || []).forEach(item => addChatMessage(item.role, item.content));
  const last = (session.transcript || []).at(-1);
  if (session.active_question && !(last?.role === 'interviewer' && last.content === state.lastQuestion)) addChatMessage('interviewer', state.lastQuestion);
  if (!session.active_question) {
    if (retryFinished) addSystemMessage('本次重答已保存。不会继续普通题纲，可主动生成本次复盘报告。');
    else if (session.review_is_stale) addSystemMessage('本次重答已保存，旧报告仅对应之前的作答。可主动生成本次复盘报告，不会继续普通题纲。');
    else if (session.status === 'completed') addSystemMessage('面试已结束，报告已生成，可前往“证据报告”查看。');
    else if (session.status === 'ended') addSystemMessage('面试已结束，记录已保存，未生成报告。需要分析时，可点击“生成报告”。');
    else if (state.nextQuestionJob?.status === 'running') addSystemMessage('回答已保存，后台仍在生成下一题。稍后点击“刷新当前问题”读取结果。');
    else addSystemMessage('当前没有待回答的问题，已作答内容仍已保存。可以重新生成下一题，或选择仅保存记录或生成报告。');
    stopTimer();
  }
  updateInterviewMeta();
  renderNextQuestionRecovery();
  setConversationBusy(state.isBusy);
}

async function endInterview() {
  if (!state.sessionId || state.isBusy) return;
  if (state.isRecording) {
    await stopRecording();
    showToast("录音已停止，请先检查草稿。发送或清空后，再选择结束方式。");
    return;
  }
  if (state.voice?.processing || state.voice?.stopping) return showToast('转写尚未完成，请稍候。');
  if ($('voice-transcript').value.trim() || $('input-answer').value.trim()) return showToast('还有尚未发送的文字，请先发送，或清空草稿后结束面试。', true);
  if (state.sessionEnded) {
    if (!state.serviceReady) return showToast('请先在系统设置中配置分析模型。', true);
    if (window.confirm('现在使用已保存的面试记录生成报告？')) await followReport(state.sessionId, true);
    return;
  }
  $("end-interview-error").textContent = '';
  $("btn-end-report").disabled = !state.serviceReady;
  $("end-interview-dialog").showModal();
}

async function confirmEndInterview(generateReport) {
  if (!state.sessionId || state.isBusy) return;
  if (generateReport) {
    $("end-interview-dialog").close();
    stopTimer(); stopSpeaking();
    await followReport(state.sessionId, true);
    return;
  }
  const context = sessionContext();
  state.isBusy = true;
  setConversationBusy(true);
  for (const id of ['btn-end-only', 'btn-end-report', 'btn-cancel-end']) $(id).disabled = true;
  try {
    await postJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}/end`, {});
    if (!isSessionContext(context)) return;
    state.sessionEnded = true;
    state.activeQuestionId = null;
    stopTimer(); stopSpeaking();
    $("end-interview-dialog").close();
    $("chat-area").replaceChildren();
    addSystemMessage('面试已结束，记录已保存；未生成报告。可在训练记录中查看或补生成报告。');
    switchView('history');
    showToast('面试已结束，记录已保存，未调用模型生成报告。');
  } catch (error) {
    if (isSessionContext(context)) $("end-interview-error").textContent = error.message || '保存失败，请检查本地服务后重试。';
  } finally {
    if (isSessionContext(context)) {
      state.isBusy = false;
      for (const id of ['btn-end-only', 'btn-cancel-end']) $(id).disabled = false;
      $("btn-end-report").disabled = !state.serviceReady;
      setConversationBusy(false);
    }
  }
}

async function retryQuestion(questionId) {
  if (!state.sessionId || state.isBusy) return;
  const context = invalidateSessionContext(state.sessionId);
  state.isBusy = true;
  try {
    const active = await postJSON(`/api/sessions/${encodeURIComponent(context.sessionId)}/retry`, {
      question_id: questionId,
    });
    if (!isSessionContext(context)) return;
    state.sessionEnded = false;
    applyActiveQuestion(active);
    state.lastReport = null;
    enableView('report', false);
    setReportExportReady(false);
    clearAnswerDrafts();
    localStorage.removeItem('interview-sim-pending-report');
    $('chat-area').replaceChildren();
    addSystemMessage(`单题重答 · 第 ${active.attempt} 次作答。提交或跳过后结束本次练习。`);
    addChatMessage("interviewer", active.question);
    updateInterviewMeta();
    renderNextQuestionRecovery();
    enableView("interview");
    startTimer(false);
    switchView("interview");
    if ($("auto-speak").checked) speakText(active.question);
    $("input-answer").focus();
  } catch (error) {
    if (isSessionContext(context)) showToast(error.message || "无法开始专项重答", true);
  } finally {
    if (isSessionContext(context)) { state.isBusy = false; setConversationBusy(false); }
  }
}

function renderReport(data) {
  if (data.schema_version === 2) renderEvidenceReport(data);
  else renderLegacyReport(data);
  initializeReportLearning(data);
}

function renderLegacyReport(data) {
  const score = data.score || {};
  const dimensions = score.dimensions || {};
  const feedback = data.question_feedback || [];
  const skillMap = data.skill_map || [];

  const dimensionsHtml = Object.entries(dimensions).map(([name, info]) => {
    const max = Number(info.max) || 1;
    const value = Number(info.score) || 0;
    const percent = Math.max(0, Math.min(100, Math.round((value / max) * 100)));
    return `<div class="metric-row">
      <span class="metric-name">${esc(name)}</span>
      <div class="metric-bar" aria-label="${escAttr(name)} ${percent}%"><div class="metric-fill" style="width:${percent}%"></div></div>
      <span class="metric-value">${value} / ${max}</span>
      <span class="metric-comment">${esc(info.comment || "")}</span>
    </div>`;
  }).join("");

  $("report-content").innerHTML = `
    <section class="surface report-version-note"><strong>旧版五维报告</strong><p>保留生成时的原评分含义与权重，不转换为新版四维表现，也不与新版分数直接比较。</p></section>
    <div class="report-summary">
      <section class="surface score-panel">
        <span class="score-label">TRAINING SCORE</span>
        <div class="score-number">${Number(score.total) || 0}<small>/100</small></div>
        <p>${esc(score.conclusion || "暂无竞争力结论")}</p>
        <span class="calibration-note">${esc(score.calibration_note || "分数仅用于训练对比，不代表真实招聘结果。")}</span>
      </section>
      <section class="surface dimension-panel">
        <h2>五维能力概览</h2>
        <div class="dimension-list">${dimensionsHtml || emptyInline("暂无维度数据")}</div>
      </section>
    </div>
    <div class="report-grid">
      <div>
        <section class="surface report-section">
          <div class="report-section-heading"><div><h2>逐题证据</h2><p>引用必须能在你的原回答中逐字找到</p></div><span>${feedback.length} 次作答</span></div>
          <div class="feedback-list">${renderQuestionFeedback(feedback)}</div>
        </section>
        <section class="surface report-section">
          <div class="report-section-heading"><div><h2>岗位定制简历</h2><p>仅重排和改写已有事实</p></div></div>
          ${renderResume(data.tailored_resume, data.tailored_resume_warnings)}
        </section>
      </div>
      <aside class="side-stack">
        <section class="surface report-section">
          <h2>能力地图</h2>
          <div class="skill-list">${renderSkillMap(skillMap)}</div>
        </section>
        <section class="surface report-section">
          <h2>下一轮训练</h2>
          <div class="practice-list">${renderPracticePlan(data.practice_plan || [])}</div>
        </section>
        <section class="surface report-section">
          <h2>面试提升建议</h2>
          <div class="tips-list">${renderTips(data.interview_tips || [])}</div>
        </section>
        <section class="surface report-section">
          <h2>简历润色清单</h2>
          <div class="polish-list">${renderPolish(data.polish_list || [])}</div>
        </section>
      </aside>
    </div>${reportLearningShell()}`;
}

function renderQuestionFeedback(items, versioned = false) {
  if (!items.length) return emptyInline("当前历史报告没有逐题证据。完成一次新面试即可生成。 ");
  const previousScores = new Map();
  return items.map((item) => {
    const score = Number(item.score) || 0;
    const previous = previousScores.get(item.question_id);
    previousScores.set(item.question_id, score);
    const delta = versioned || previous === undefined ? "" : ` · ${score >= previous ? "+" : ""}${score - previous} 分`;
    const evidence = (item.evidence_quotes || []).map((quote) => `<q>${esc(quote)}</q>`).join("、");
    return `<article class="feedback-card">
      <div class="feedback-topline">
        <span class="question-code">${esc(item.question_id || "QUESTION")}</span>
        <span class="attempt-pill">ATTEMPT ${Number(item.attempt) || 1}${esc(delta)}</span>
        <span class="confidence">${confidenceLabel(item.confidence)}</span>
        <span class="turn-score">${score}<small>/10</small></span>
      </div>
      <h3 class="feedback-question">${esc(item.question || "未记录问题")}</h3>
      <blockquote class="answer-quote">${esc(item.status === 'unanswered' ? '未回答 · 本题 0 分' : item.answer || "未记录回答")}</blockquote>
      ${item.status === 'unanswered' ? `<p class="skip-note">${esc(item.reason_analysis || '未说明跳过原因，不推断心理或能力问题。')}</p>` : ''}
      <div class="evidence-grid">
        <div class="evidence-box covered"><strong>已覆盖</strong>${renderTags(item.covered_points)}</div>
        <div class="evidence-box missed"><strong>仍缺少</strong>${renderTags(item.missed_points)}</div>
      </div>
      <p class="feedback-evidence"><strong>可验证证据：</strong>${evidence || "没有找到可逐字验证的引用，评分置信度已降低。"}</p>
      ${versioned ? `<div class="turn-dimensions">${Object.entries(REPORT_DIMENSIONS).map(([key, label]) => `<div><strong>${esc(label)} · ${reportValue(item.dimensions?.[key]?.score, 10)}</strong><p>${esc(item.dimensions?.[key]?.comment || '')}</p></div>`).join('')}</div><div class="report-explanation"><p><strong>这题考察什么：</strong>${esc(item.question_explanation || '暂无说明')}</p><p><strong>下次作答提纲：</strong>${esc(reportText(item.improved_answer_outline) || '请补充真实行动与结果，不编造经历。')}</p></div>` : ''}
      <div class="coaching-row">
        <p><strong>下一次只改这一点：</strong>${esc(item.coaching_tip || "补充具体行动和结果。")}</p>
        <button class="btn secondary" type="button" data-retry-question="${escAttr(item.question_id || "")}">重答这题</button>
      </div>
    </article>`;
  }).join("");
}

function renderTags(values) {
  const items = Array.isArray(values) ? values : [];
  return items.length
    ? `<div class="tag-list">${items.map((value) => `<span>${esc(value)}</span>`).join("")}</div>`
    : '<div class="tag-list"><span>暂无明确证据</span></div>';
}

function renderSkillMap(items) {
  if (!items.length) return emptyInline("暂无能力地图");
  return items.map((item) => {
    const score = Math.max(0, Math.min(100, Number(item.score) || 0));
    return `<div class="skill-item"><strong>${esc(item.skill)}</strong><span>${score}</span>
      <div class="metric-bar"><div class="metric-fill" style="width:${score}%"></div></div>
      <p>${esc(item.evidence || "")}</p></div>`;
  }).join("");
}

function renderPracticePlan(items) {
  if (!items.length) return emptyInline("暂无专项训练计划");
  return items.map((item, index) => `<div class="practice-item"><strong>${index + 1}. ${esc(item.focus || "专项重答")}</strong>${esc(item.reason || "")}</div>`).join("");
}

function renderTips(items) {
  if (!items.length) return emptyInline("暂无建议");
  return items.map((item) => `<div class="tip-item">${esc(item)}</div>`).join("");
}

function renderPolish(items) {
  if (!items.length) return emptyInline("暂无润色建议");
  return items.map((item) => {
    const warning = item._validated === false
      ? `<span class="claim-warning">包含原简历未出现的数字：${esc((item._unverified_claims || []).join("、"))}。使用前请核实。</span>`
      : "";
    return `<div class="polish-item"><strong>${esc(item.original || item.issue || "待优化内容")}</strong>
      ${esc(item.suggestion || "")}<br><br>${esc(item.example || "")}${warning}</div>`;
  }).join("");
}

function renderResume(text, warnings) {
  if (!text) return emptyInline("当前报告未生成岗位定制简历");
  const warning = warnings?.length
    ? `<span class="claim-warning">以下数字未在原简历中找到：${esc(warnings.join("、"))}。导出前请核实。</span>`
    : "";
  return `<pre class="resume-preview">${esc(text)}</pre>${warning}`;
}


async function loadHistory() {
  const container = $("history-list");
  container.innerHTML = '<div class="surface empty-state tall loading-block"><strong>正在读取本地记录</strong></div>';
  try {
    const sessions = await getJSON("/api/sessions");
    if (!sessions.length) {
      container.innerHTML = '<div class="surface empty-state tall"><strong>还没有训练记录</strong><span>完成第一次模拟后，报告会保存在这里。</span></div>';
      return;
    }
    container.innerHTML = sessions.map((session) => `<button class="history-card" type="button" data-session-id="${escAttr(session.id)}">
      <time datetime="${escAttr(session.created_at)}">${formatDate(session.created_at)}</time>
      <strong>${esc(session.job_title || "未命名岗位")}</strong>
      <span>${session.status === 'ended' ? '已结束 · 未生成报告' : session.status === 'completed' ? '报告已生成' : session.status === 'interview_finished' ? '待生成报告' : session.status === 'reviewing' ? '报告生成中' : session.status === 'review_failed' ? '报告待重试' : '面试记录'}</span>
      <div class="history-meta"><span>${esc(session.persona)} · ${esc(session.difficulty)} · ${Number(session.turn_count) || 0} 次作答</span><span class="history-score"><small>${session.score_kind === 'interview_first_attempt' ? '首次面试' : session.score == null ? '未评估' : '旧版五维'}</small>${session.score == null ? '—' : reportNumber(session.score)}</span></div>
    </button>`).join("");
  } catch (error) {
    container.innerHTML = `<div class="surface empty-state tall"><strong>记录加载失败</strong><span>${esc(error.message)}</span></div>`;
  }
}

async function viewSession(sessionId) {
  if (state.isRecording || state.voice?.processing) return showToast('请先结束当前录音。');
  if (($('input-answer').value.trim() || $('voice-transcript').value.trim()) && !window.confirm('打开训练记录会放弃当前未发送的草稿，继续吗？')) return;
  const context = invalidateSessionContext(sessionId);
  state.isBusy = true;
  setConversationBusy(true);
  try {
    const session = await getJSON(`/api/sessions/${encodeURIComponent(sessionId)}`);
    if (!isSessionContext(context)) return;
    state.sessionEnded = session.status === 'ended';
    applyActiveQuestion(session.active_question);
    state.blueprint = session.blueprint || [];
    state.round = session.question_cursor || session.turns?.length || 0;
    state.lastReport = session.review_is_stale ? null : session.review || null;
    if (session.config?.voice) $('select-voice').value = session.config.voice;
    if (session.config?.persona) $('select-persona').value = session.config.persona;
    if (session.config?.difficulty) $('select-difficulty').value = session.config.difficulty;
    $("interview-persona").textContent = session.config?.persona || "面试官";
    $("interview-difficulty").textContent = session.config?.difficulty || "标准";
    const hasInterviewHistory = Boolean(session.active_question || session.turns?.length);
    enableView("interview", hasInterviewHistory);
    enableView("report", Boolean(session.review) && !session.review_is_stale && !session.active_question?.is_retry);
    clearAnswerDrafts();
    // The report and live navigation must always refer to the same saved session.
    renderLiveSession(session);

    // An active retry (or any active question) always wins over an old report.
    if (session.active_question) {
      localStorage.removeItem('interview-sim-pending-report');
      startTimer(true);
      switchView('interview');
      return;
    }

    if (!state.sessionEnded && !session.review_is_stale && (session.report_job?.status === 'running' || session.report_job?.status === 'failed' || session.status === 'reviewing' || session.status === 'review_failed')) {
      await followReport(session.id, false);
      return;
    }

    if (session.review && session.status === 'completed') {
      renderReport(session.review);
      setReportExportReady(true);
      switchView("report");
      return;
    }
    if (hasInterviewHistory) {
      switchView("interview");
    }
  } catch (error) {
    if (isSessionContext(context)) showToast(error.message || "记录读取失败", true);
  } finally {
    if (isSessionContext(context)) { state.isBusy = false; setConversationBusy(false); }
  }
}

function updateInterviewMeta() {
  $("interview-round").textContent = state.activeIsRetry ? `单题重答 · 第 ${state.activeAttempt} 次` : `第 ${state.round} 题`;
}

function setInputMode(mode) {
  state.inputMode = mode;
  const isText = mode === "text";
  $("btn-mode-text").classList.toggle("active", isText);
  $("btn-mode-voice").classList.toggle("active", !isText);
  $("btn-mode-text").setAttribute("aria-pressed", String(isText));
  $("btn-mode-voice").setAttribute("aria-pressed", String(!isText));
  $("text-input-group").classList.toggle("hidden", !isText);
  $("voice-input-group").classList.toggle("hidden", isText);
  $("voice-draft-group").classList.toggle("hidden", isText);
  $("subtitle-area").classList.toggle("hidden", isText);
  if (isText) $("input-answer").focus();
}

async function toggleRecording() {
  if (state.isRecording) stopRecording();
  else await startRecording();
}

async function startRecording() {
  if (!state.serviceReady || state.isBusy || state.isRecording || state.voice?.processing) return;
  if (($("voice-transcript").value.trim() || state.voice?.queue.length) && !window.confirm("重新录音会替换当前语音草稿和未识别片段，继续吗？")) return;
  stopSpeaking();
  const voice = new InterviewVoice({
    onText(text) {
      $("subtitle-text").textContent = text || "正在聆听，文字将在识别后出现…";
      $("voice-transcript").value = text;
      syncVoiceUI();
    },
    onStatus(text) { $("record-status").textContent = text; },
    onState: syncVoiceUI,
  });
  state.voice = voice;
  $("btn-record").disabled = true;
  try {
    await voice.start($("select-persona").value === "英文面" ? "en" : "zh");
  } catch (error) {
    $("record-status").textContent = `无法开始录音：${error.message}`;
  } finally { syncVoiceUI(); }
}

async function stopRecording() {
  await state.voice?.stop();
  syncVoiceUI();
}

function syncVoiceUI() {
  const voice = state.voice;
  state.isRecording = Boolean(voice?.recording);
  const pending = Boolean(voice?.processing || voice?.stopping);
  $("btn-record").classList.toggle("recording", state.isRecording);
  $("btn-record").setAttribute("aria-pressed", String(state.isRecording));
  $("record-label").textContent = state.isRecording ? "结束录音" : "开始录音";
  $("btn-record").disabled = state.isBusy || (!state.isRecording && pending) || !state.activeQuestionId || state.capabilities.asr === false;
  $("voice-transcript").readOnly = state.isRecording || pending;
  $("btn-send-voice").disabled = state.isBusy || state.isRecording || pending || !$("voice-transcript").value.trim() || !state.activeQuestionId;
  $("btn-retry-asr").classList.toggle("hidden", !voice?.failed);
  $("btn-retry-asr").disabled = state.isRecording || pending || state.isBusy;
  $("btn-skip").disabled = state.isBusy || state.isRecording || pending || !state.activeQuestionId;
  $("btn-send").disabled = state.isBusy || state.isRecording || pending || !state.activeQuestionId || !state.serviceReady;
  $("btn-mode-text").disabled = state.isRecording || pending || state.isBusy;
  $("btn-mode-voice").disabled = state.isBusy;
}

function encodeWav(samples, sampleRate) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset, value) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  write(36, "data");
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => {
    const value = Math.max(-1, Math.min(1, sample));
    view.setInt16(44 + index * 2, value < 0 ? value * 32768 : value * 32767, true);
  });
  return buffer;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunkSize = 32768;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

async function speakText(text) {
  if (!state.serviceReady || !text?.trim() || state.isRecording) return;
  stopSpeaking();
  const controller = new AbortController();
  state.ttsAbortController = controller;
  const context = new AudioContext();
  state.playbackContext = context;
  try {
    // Resume inside the click gesture, before waiting for the network.
    await context.resume();
    const response = await fetch(`${API}/api/tts/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, voice: $("select-voice").value }),
      signal: controller.signal,
    });
    if (!response.ok || !response.body) throw new Error("语音服务暂不可用");
    const sampleRate = Number(response.headers.get("X-Sample-Rate")) || 24000;
    const player = new InterviewAudio.PcmPlayer(context, sampleRate, state.playbackSources);
    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (controller.signal.aborted) break;
      if (done) { player.push(new Uint8Array(0), true); break; }
      player.push(value);
    }
    const remaining = Math.max(0, (player.nextTime - context.currentTime) * 1000 + 100);
    setTimeout(() => {
      if (state.playbackContext === context) {
        context.close().catch(() => {});
        state.playbackContext = null;
      }
    }, remaining);
  } catch (error) {
    if (error.name !== "AbortError") showToast("语音朗读暂不可用，请检查语音模型连接；文字内容不受影响。", true);
    if (state.playbackContext === context) stopSpeaking();
    else context.close().catch(() => {});
  }
}

function stopSpeaking() {
  state.ttsAbortController?.abort();
  state.ttsAbortController = null;
  state.playbackSources.forEach((source) => {
    try { source.stop(); } catch (_) { /* already ended */ }
  });
  state.playbackSources.clear();
  state.playbackContext?.close().catch(() => {});
  state.playbackContext = null;
}

async function previewVoice() {
  if (!state.serviceReady) return;
  const button = $("btn-preview-voice");
  button.disabled = true;
  button.textContent = "播放中";
  await speakText("你好，我是今天的面试官。准备好以后，我们就开始。 ");
  window.setTimeout(() => {
    button.disabled = !state.serviceReady;
    button.textContent = "试听";
  }, 900);
}

function syncVoiceWithPersona() {
  const voiceMap = { 技术负责人: "白桦", HR: "茉莉", 业务总监: "苏打", 压力面: "苏打", 英文面: "白桦" };
  $("select-voice").value = voiceMap[$("select-persona").value] || "白桦";
}

function startTimer(reset) {
  if (reset) state.timerSeconds = 0;
  if (state.timerInterval) return;
  updateTimer();
  state.timerInterval = window.setInterval(() => {
    state.timerSeconds += 1;
    updateTimer();
  }, 1000);
}

function stopTimer() {
  window.clearInterval(state.timerInterval);
  state.timerInterval = null;
}

function updateTimer() {
  const minutes = Math.floor(state.timerSeconds / 60);
  const seconds = state.timerSeconds % 60;
  $("timer-display").textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  $("timer-display").dateTime = `PT${state.timerSeconds}S`;
}

function renderReportMarkdown(data) {
  if (data.schema_version === 2) return renderEvidenceReportMarkdown(data);
  const lines = [
    "# 面试训练证据报告",
    "",
    "旧版五维报告：保留原评分含义与权重，不与新版四维分数直接比较。",
    `总分：${data.score?.total ?? 0}/100`,
    `结论：${data.score?.conclusion || ""}`,
    "",
    "## 逐题证据",
  ];
  (data.question_feedback || []).forEach((item) => {
    lines.push(
      "",
      `### ${item.question_id} · 第 ${item.attempt || 1} 次作答 · ${item.score || 0}/10`,
      `问题：${item.question || ""}`,
      `回答：${item.status === 'unanswered' ? '未回答（0 分）' : item.answer || ""}`,
      ...(item.status === 'unanswered' ? [`原因说明：${item.reason_analysis || '未说明'}`] : []),
      `证据：${(item.evidence_quotes || []).join("；") || "无可验证引用"}`,
      `已覆盖：${(item.covered_points || []).join("；") || "无"}`,
      `仍缺少：${(item.missed_points || []).join("；") || "无"}`,
      `下一次改进：${item.coaching_tip || ""}`,
    );
  });
  lines.push("", "## 岗位定制简历", "", data.tailored_resume || "暂无");
  return lines.join("\n");
}

function exportReport(format) {
  if (!state.lastReport) {
    showToast("当前没有可导出的报告。", true);
    return;
  }
  if (format === "markdown") {
    downloadFile("interview-evidence-report.md", renderReportMarkdown(state.lastReport), "text/markdown;charset=utf-8");
  } else {
    downloadFile("interview-evidence-report.json", JSON.stringify(state.lastReport, null, 2), "application/json;charset=utf-8");
  }
}

function downloadFile(name, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = name;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function importDocument(event) {
  const input = event.currentTarget;
  const file = input.files?.[0];
  if (!file) return;
  if (file.size > 12 * 1024 * 1024) {
    showToast("单个文件请控制在 12 MB 以内。", true);
    input.value = "";
    return;
  }
  try {
    showToast(`正在本地解析 ${file.name}…`);
    const form = new FormData();
    form.append("file", file);
    const response = await fetch(`${API}/api/documents/extract`, { method: "POST", body: form });
    if (!response.ok) throw new Error(await responseError(response, "文件解析失败"));
    const parsed = await response.json();
    const target = $(input.dataset.target);
    const maxLength = Number(target.maxLength) || parsed.text.length;
    target.value = parsed.text.slice(0, maxLength);
    target.dispatchEvent(new Event("input"));
    const clipped = parsed.text.length > maxLength ? `，已按输入上限保留 ${maxLength.toLocaleString()} 字` : "";
    showToast(`已导入 ${parsed.filename} · ${Number(parsed.char_count).toLocaleString()} 字${clipped}`);
  } catch (error) {
    showToast(`文件读取失败：${error.message}`, true);
  } finally {
    input.value = "";
  }
}

async function loadSettings() {
  try {
    const settings = await getJSON("/api/settings");
    state.settings = settings;
    renderConnections(settings);
    $("setting-display-name").value = settings.profile?.display_name || "";
    $("setting-target-role").value = settings.profile?.target_role || "";
    $("setting-coaching-goal").value = settings.profile?.coaching_goal || "";
    $("setting-base-url").value = settings.ai?.base_url || "https://api.xiaomimimo.com/v1";
    $("setting-api-key").value = "";
    $("setting-llm-model").value = settings.ai?.llm_model || "mimo-v2.5";
    $("setting-llm-model-pro").value = settings.ai?.llm_model_pro || "mimo-v2.5-pro";
    $("setting-asr-model").value = settings.ai?.asr_model || "mimo-v2.5-asr";
    $("setting-tts-model").value = settings.ai?.tts_model || "mimo-v2.5-tts";
    $("setting-persona").value = settings.defaults?.persona || "技术负责人";
    $("setting-difficulty").value = settings.defaults?.difficulty || "标准";
    $("setting-voice").value = settings.defaults?.voice || "白桦";
    if (!$("input-target-role").value) $("input-target-role").value = settings.profile?.target_role || "";
    $("select-persona").value = settings.defaults?.persona || "技术负责人";
    $("select-difficulty").value = settings.defaults?.difficulty || "标准";
    $("select-voice").value = settings.defaults?.voice || "白桦";
    renderSettingsState(settings);
    if (settings.is_first_run) switchView("settings");
  } catch (error) {
    $("settings-state").textContent = "读取失败";
    showToast(error.message || "无法读取本地配置", true);
  }
}

function renderSettingsState(settings) {
  const hasKey = Boolean(settings.ai?.has_api_key);
  $("settings-state").textContent = settings.is_first_run ? "待完成" : "配置已就绪";
  $("first-run-banner").classList.toggle("hidden", !settings.is_first_run);
  $("settings-key-state").textContent = hasKey ? "已配置" : "未配置";
  $("settings-key-state").classList.toggle("configured", hasKey);
  $("setting-api-key").placeholder = hasKey ? "已安全保存；留空则保留现有 Key" : "请输入 API Key";
}

async function saveSettings(event) {
  event.preventDefault();
  if (state.isRecording || state.voice?.processing || state.isBusy) return showToast('请先结束当前录音或等待正在进行的操作完成，再切换模型。', true);
  const button = $("btn-save-settings");
  button.disabled = true;
  button.textContent = "正在保存…";
  $("settings-status").textContent = "正在安全写入本机配置…";
  $("settings-status").classList.remove("error");
  try {
    const settings = await putJSON("/api/settings", {
      connections: collectConnections(),
      profile: {
        display_name: $("setting-display-name").value.trim(),
        target_role: $("setting-target-role").value.trim(),
        coaching_goal: $("setting-coaching-goal").value.trim(),
      },
      ai: {
        base_url: $("setting-base-url").value.trim(),
        api_key: $("setting-api-key").value.trim(),
        llm_model: $("setting-llm-model").value.trim(),
        llm_model_pro: $("setting-llm-model-pro").value.trim(),
        asr_model: $("setting-asr-model").value.trim(),
        tts_model: $("setting-tts-model").value.trim(),
      },
      defaults: {
        persona: $("setting-persona").value,
        difficulty: $("setting-difficulty").value,
        voice: $("setting-voice").value,
      },
    });
    state.settings = settings;
    renderConnections(settings);
    $("setting-api-key").value = "";
    $("input-target-role").value = settings.profile?.target_role || "";
    $("select-persona").value = settings.defaults?.persona || "技术负责人";
    $("select-difficulty").value = settings.defaults?.difficulty || "标准";
    $("select-voice").value = settings.defaults?.voice || "白桦";
    renderSettingsState(settings);
    await checkServiceHealth();
    $("settings-status").textContent = settings.is_first_run
      ? "已保存，请补充目标岗位和 API Key。"
      : "配置已保存，现在可以开始准备面试。";
    if (!settings.is_first_run) switchView("config");
  } catch (error) {
    $("settings-status").textContent = error.message || "配置保存失败";
    $("settings-status").classList.add("error");
  } finally {
    button.disabled = false;
    button.textContent = "保存并进入工作台";
  }
}

function updateCharacterCount(inputId, countId) {
  $(countId).textContent = `${$(inputId).value.trim().length.toLocaleString()} 字`;
}

function setConfigBusy(busy) {
  const button = $("btn-start");
  button.disabled = busy || !state.serviceReady;
  $("start-label").textContent = busy ? "正在建立面试蓝图" : "分析材料并开始";
}

function setConversationBusy(busy) {
  const answerDisabled = busy || !state.activeQuestionId;
  $("btn-send").disabled = answerDisabled || !state.serviceReady;
  $("input-answer").disabled = answerDisabled;
  $("btn-record").disabled = answerDisabled || !state.serviceReady;
  $("btn-mode-text").disabled = answerDisabled;
  $("btn-mode-voice").disabled = answerDisabled;
  $("btn-end").disabled = state.isBusy || !state.sessionId;
  $("btn-end").textContent = state.sessionEnded ? '生成报告' : '结束面试';
  syncVoiceUI();
  renderNextQuestionRecovery();
}

function showInlineStatus(message, isError = false) {
  const status = $("config-status");
  status.textContent = message;
  status.classList.toggle("error", isError);
}

let toastTimer = null;
function showToast(message, isError = false) {
  const toast = $("toast");
  window.clearTimeout(toastTimer);
  toast.textContent = message;
  toast.className = `toast show${isError ? " error" : ""}`;
  toastTimer = window.setTimeout(() => { toast.className = "toast"; }, 4200);
}

function addChatMessage(role, content, streaming = false) {
  const normalizedRole = role === "candidate" ? "candidate" : "interviewer";
  const article = document.createElement("article");
  article.className = `chat-message ${normalizedRole}${streaming ? " streaming" : ""}`;
  const speaker = document.createElement("span");
  speaker.className = "speaker";
  speaker.textContent = normalizedRole === "candidate" ? "你" : "面试官";
  const body = document.createElement("div");
  body.className = `message-body${streaming ? " stream-caret" : ""}`;
  body.textContent = content;
  article.append(speaker, body);
  $("chat-area").appendChild(article);
  scrollChat();
  return article;
}

function addSystemMessage(text) {
  const message = document.createElement("div");
  message.className = "system-message";
  message.textContent = text;
  $("chat-area").appendChild(message);
  scrollChat();
  return message;
}

function scrollChat() {
  $("chat-area").scrollTop = $("chat-area").scrollHeight;
}

async function postJSON(path, body) {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await responseError(response, "请求失败"));
  return response.json();
}

async function putJSON(path, body) {
  const response = await fetch(`${API}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(await responseError(response, "请求失败"));
  return response.json();
}

async function getJSON(path) {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(await responseError(response, "请求失败"));
  return response.json();
}

async function responseError(response, fallback) {
  try {
    const payload = await response.json();
    if (typeof payload.detail === "string") return payload.detail;
    if (Array.isArray(payload.detail)) return payload.detail.map(item => item.msg || '配置格式不正确').join('；');
    if (payload.detail?.message) return payload.detail.message;
    return payload.error?.message || fallback;
  } catch (_) {
    return fallback;
  }
}

async function readTextStream(response, onChunk) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    if (text && onChunk(text) === false) { await reader.cancel(); return; }
  }
  const tail = decoder.decode();
  if (tail) onChunk(tail);
}

function confidenceLabel(value) {
  return ({ high: "高置信", medium: "中置信", low: "低置信" })[value] || "中置信";
}

function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "未知时间" : date.toLocaleString("zh-CN", { hour12: false });
}

function emptyInline(message) {
  return `<div class="empty-state"><span>${esc(message)}</span></div>`;
}

function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escAttr(value) {
  return esc(value).replaceAll("`", "&#096;");
}
