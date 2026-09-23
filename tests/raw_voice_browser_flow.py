"""Real InterviewVoice acceptance with synthetic PCM and fixture ASR only."""
from playwright.async_api import expect


RAW_TRANSCRIPT = '我，我，嗯，也也参与接口对接。'

# Replace only browser audio hardware. InterviewVoice, Audio helpers, app handlers,
# streaming fetch and the fixture ASR endpoint all remain the production paths.
SYNTHETIC_AUDIO = """(() => {
  localStorage.setItem('interview-sim-auto-voice-cleanup', 'true');
  localStorage.setItem('raw-voice-test-unrelated', 'keep');
  window.syntheticMicrophoneStarts = 0;
  Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {configurable: true, value: async () => {
    window.syntheticMicrophoneStarts++;
    return {getTracks: () => [{stop() {}}]};
  }});
  window.AudioContext = class {
    sampleRate = 16000;
    audioWorklet = {addModule: async () => {}};
    async resume() {}
    async close() {}
    createMediaStreamSource() { return {connect() {}, disconnect() {}}; }
  };
  window.AudioWorkletNode = class {
    port = {postMessage: () => queueMicrotask(() => this.port.onmessage({data: {stopped: true}}))};
    connect() {}
    disconnect() {}
  };
})();
"""


async def supply_audio(page):
    await page.evaluate("""() => {
      if (!(state.voice instanceof InterviewVoice) || !state.voice.recording) throw new Error('Real recorder is not active');
      state.voice.node.port.onmessage({data: {samples: new Float32Array(4000).fill(0.1)}});
    }""")


async def run_raw_voice_flow(page, base_url, output_dir):
    cleanup_requests, answer_requests, asr_requests = [], [], []

    def track(request):
        if request.method != 'POST':
            return
        if request.url.endswith('/voice-cleanup'):
            cleanup_requests.append(request.post_data_json)
        elif request.url.endswith('/api/chat'):
            answer_requests.append(request.post_data_json)
        elif '/api/transcribe/' in request.url:
            asr_requests.append(request.url)

    page.on('request', track)
    page.on('dialog', lambda dialog: dialog.accept())
    await page.add_init_script(SYNTHETIC_AUDIO)
    await page.goto(base_url)
    await expect(page.locator('#service-status-title')).to_have_text('模型配置已加载')
    assert await page.evaluate("localStorage.getItem('interview-sim-auto-voice-cleanup')") is None
    assert await page.evaluate("localStorage.getItem('raw-voice-test-unrelated')") == 'keep'
    await expect(page.locator('#btn-cleanup-voice, #auto-cleanup-voice, #voice-cleanup-status')).to_have_count(0)
    await page.locator('#quick-prep > summary').click()
    await page.locator('#auto-speak').uncheck()
    await page.locator('#quick-prep > summary').click()
    await page.locator('.job-card').first.get_by_role('button', name='开始面试', exact=True).click()
    await page.locator('#btn-run-interview').click()
    await expect(page.locator('#view-interview')).to_be_visible()
    session_id = await page.evaluate('state.sessionId')
    await page.locator('#btn-mode-voice').click()

    # The real four-second timer requests ASR while recording. No draft injection.
    await page.locator('#btn-record').click()
    await expect(page.locator('#record-label')).to_have_text('结束录音')
    await supply_audio(page)
    await expect(page.locator('#voice-transcript')).to_have_value(RAW_TRANSCRIPT, timeout=8000)
    await expect(page.locator('#subtitle-text')).to_have_text(RAW_TRANSCRIPT)
    assert await page.evaluate('state.voice.recording') is True
    await expect(page.locator('#btn-send-voice')).to_be_disabled()
    assert not answer_requests and not cleanup_requests
    await page.locator('#btn-record').click()
    await expect(page.locator('#record-label')).to_have_text('开始录音')
    await expect(page.locator('#btn-send-voice')).to_be_enabled()
    await expect(page.locator('#voice-transcript')).to_have_value(RAW_TRANSCRIPT)
    assert len(asr_requests) == 1 and not answer_requests and not cleanup_requests

    for width in (1024, 1440):
        await page.set_viewport_size({'width': width, 'height': 1080})
        await page.locator('#voice-draft-group').scroll_into_view_if_needed()
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}px'
        assert await page.locator('#voice-draft-group').evaluate('(node) => node.scrollWidth <= node.clientWidth'), f'Composer overflow at {width}px'
        await page.screenshot(path=str(output_dir / f'raw-voice-{width}.png'), full_page=True)

    await page.locator('#btn-send-voice').click()
    await expect(page.locator('#interview-round')).to_have_text('第 2 题')
    assert len(answer_requests) == 1
    assert answer_requests[0]['asr_text'] == RAW_TRANSCRIPT
    assert 'voice_input' not in answer_requests[0]
    saved = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert saved['turns'][0]['answer'] == RAW_TRANSCRIPT
    assert not saved['turns'][0].get('voice_input')

    # This time stop before the four-second timer: the final pending ASR chunk
    # must still reach the composer, without triggering any post-processing.
    await page.locator('#btn-record').click()
    await expect(page.locator('#record-label')).to_have_text('结束录音')
    await supply_audio(page)
    await page.locator('#btn-record').click()
    await expect(page.locator('#voice-transcript')).to_have_value(RAW_TRANSCRIPT)
    await expect(page.locator('#btn-send-voice')).to_be_enabled()
    assert len(asr_requests) == 2 and len(answer_requests) == 1 and not cleanup_requests
    corrected = '我，我，嗯，也也参与接口联调。'
    await page.locator('#voice-transcript').fill(corrected)
    await page.evaluate('syncVoiceUI()')
    await expect(page.locator('#voice-transcript')).to_have_value(corrected)
    assert len(answer_requests) == 1
    await page.screenshot(path=str(output_dir / 'raw-voice-manual-correction.png'), full_page=True)
    await page.locator('#btn-send-voice').click()
    await expect(page.locator('#interview-round')).to_have_text('第 3 题')
    assert len(answer_requests) == 2
    assert answer_requests[1]['asr_text'] == corrected and 'voice_input' not in answer_requests[1]
    saved = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert [turn['answer'] for turn in saved['turns']] == [RAW_TRANSCRIPT, corrected]
    assert all(not turn.get('voice_input') for turn in saved['turns'])
    assert not cleanup_requests, 'Retired cleanup must never be called, including with an old enabled preference'
    assert await page.evaluate('syntheticMicrophoneStarts') == 2
