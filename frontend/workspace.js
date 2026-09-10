
async function skipQuestion() {
  if (state.isBusy || state.isRecording || state.voice?.processing || !state.activeQuestionId) return;
  if (($('input-answer').value.trim() || $('voice-transcript').value.trim()) && !window.confirm('跳过将放弃当前未发送草稿，并将本题记为未回答，继续吗？')) return;
  state.isBusy = true;
  setConversationBusy(true);
  stopSpeaking();
  try {
    const result = await postJSON(`/api/sessions/${state.sessionId}/skip`, { question_id: state.activeQuestionId, reason: $('skip-reason').value });
    addSystemMessage(`本题已标记为未回答${result.turn.skip_reason ? '：' + result.turn.skip_reason : ''}。报告会记录本题并提供训练建议。`);
    $('input-answer').value = '';
    $('voice-transcript').value = '';
    $('subtitle-text').textContent = '等待录音…';
    state.voice = null;
    $('skip-reason').value = '';
    state.activeQuestionId = result.active_question?.question_id || null;
    if (result.finished) {
      state.lastQuestion = '';
      addSystemMessage('本轮问题已结束，可以点击“结束并生成报告”。');
    } else {
      state.lastQuestion = result.active_question.question;
      state.round += 1;
      addChatMessage('interviewer', state.lastQuestion);
      updateInterviewMeta();
      renderBlueprint();
      updateCurrentFocus(state.round - 1);
      if ($('auto-speak').checked) speakText(state.lastQuestion);
    }
  } catch (error) { showToast(error.message, true); }
  finally { state.isBusy = false; setConversationBusy(false); }
}

const CONNECTION_LABELS = { chat: '对话', analysis: '分析', asr: '语音识别', tts: '语音合成' };
function renderConnections(settings) {
  $('connection-list').innerHTML = Object.entries(CONNECTION_LABELS).map(([role, label]) => {
    const item = settings.connections?.[role] || { inherit: true, protocol: ['asr','tts'].includes(role) ? 'mimo' : 'openai' };
    return `<section class="connection-card">
      <div class="connection-title"><h3>${label}连接</h3><label class="check-row" for="connection-${role}-inherit"><input id="connection-${role}-inherit" type="checkbox" ${item.inherit ? 'checked' : ''}>使用上方默认地址和 Key</label></div>
      <div class="form-grid two-columns" id="connection-${role}-details">
        <div class="form-group"><label for="connection-${role}-url">独立 API Base URL</label><input id="connection-${role}-url" class="text-input" type="url" value="${escAttr(item.inherit ? '' : item.base_url || '')}" placeholder="https://…/v1"></div>
        <div class="form-group"><label for="connection-${role}-key">独立 API Key</label><input id="connection-${role}-key" class="text-input" type="password" autocomplete="new-password" placeholder="${!item.inherit && item.has_api_key ? '已保存；不改地址时可留空保留' : '填写该服务的 Key'}"></div>
      </div>
      ${['asr','tts'].includes(role) ? `<div class="form-group"><label for="connection-${role}-protocol">语音接口格式</label><select id="connection-${role}-protocol"><option value="mimo" ${item.protocol === 'mimo' ? 'selected' : ''}>MiMo 语音接口</option><option value="openai" ${item.protocol === 'openai' ? 'selected' : ''}>OpenAI 标准音频接口</option></select></div>` : ''}
      ${role === 'tts' ? `<div class="form-grid two-columns"><div class="form-group"><label for="connection-tts-voice">服务商音色 ID（可选）</label><input id="connection-tts-voice" class="text-input" value="${escAttr(item.voice || '')}" placeholder="MiMo 留空用面试音色；标准接口默认 alloy"></div><div class="form-group"><label for="connection-tts-rate">PCM 采样率</label><select id="connection-tts-rate">${[16000,22050,24000,44100,48000].map(rate => `<option value="${rate}" ${rate === (item.sample_rate || 24000) ? 'selected' : ''}>${rate} Hz</option>`).join('')}</select><span class="field-help">必须与服务返回一致，MiMo / OpenAI 默认为 24000 Hz。</span></div></div>` : ''}
    </section>`;
  }).join('');
  for (const role of Object.keys(CONNECTION_LABELS)) {
    const check = $(`connection-${role}-inherit`);
    const update = () => {
      $(`connection-${role}-details`).classList.toggle('hidden', check.checked);
      $(`connection-${role}-url`).required = !check.checked;
      $(`connection-${role}-url`).disabled = check.checked;
      $(`connection-${role}-key`).disabled = check.checked;
    };
    check.addEventListener('change', update);
    update();
  }
}

function collectConnections() {
  const result = {};
  for (const role of Object.keys(CONNECTION_LABELS)) {
    result[role] = {
      inherit: $(`connection-${role}-inherit`).checked,
      base_url: $(`connection-${role}-url`).value.trim(),
      api_key: $(`connection-${role}-key`).value.trim(),
      protocol: $(`connection-${role}-protocol`)?.value || 'openai',
      voice: role === 'tts' ? $('connection-tts-voice').value.trim() : '',
      sample_rate: role === 'tts' ? Number($('connection-tts-rate').value) : 24000,
    };
  }
  return result;
}
