"""P3 live state boundaries through the synthetic server, without model billing."""
import asyncio
import json
import time
import uuid

from playwright.async_api import expect


async def assert_pure_live(page):
    await expect(page.locator('#view-interview')).to_be_visible()
    await expect(page.locator('#current-focus, #focus-detail, #blueprint-list, .interview-rail')).to_have_count(0)
    assert '回答建议' not in await page.locator('#view-interview').inner_text()


async def assert_history_dom(page, base_url, session_id):
    source = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    other = {**source, 'id': 'qa-other-history', 'status': 'completed', 'active_question': None,
        'transcript': [{'role': 'interviewer', 'content': '历史乙问题'},
            {'role': 'candidate', 'content': '历史乙回答，仅属于乙会话'}]}
    target = base_url + '/api/sessions/' + other['id']

    async def historical_response(route):
        await route.fulfill(content_type='application/json', body=json.dumps(other))

    await page.route(target, historical_response)
    try:
        await page.evaluate('(id) => viewSession(id)', other['id'])
        await page.locator('.nav-btn[data-view="interview"]').click()
        await expect(page.locator('#chat-area')).to_contain_text('历史乙回答，仅属于乙会话')
        await expect(page.locator('#chat-area')).not_to_contain_text('测试触发下一题失败')
        await expect(page.locator('#chat-area')).to_contain_text('报告已生成')
        assert await page.evaluate('state.sessionEnded')
    finally:
        await page.unroute(target, historical_response)
    await page.evaluate('(id) => viewSession(id)', session_id)


async def assert_report_epochs(page, base_url, session_id):
    """Late estimate success and polling failure cannot mutate a newer generation."""
    first_estimate = asyncio.Event()
    first_poll = asyncio.Event()
    release_old = asyncio.Event()
    counts = {'estimate': 0, 'poll': 0}
    estimate_url = base_url + '/api/review/estimate?session_id=' + session_id
    poll_url = base_url + '/api/sessions/' + session_id + '/review-job'

    async def estimate_response(route):
        counts['estimate'] += 1
        old = counts['estimate'] == 1
        if old:
            first_estimate.set()
            await release_old.wait()
        await route.fulfill(content_type='application/json', body=json.dumps({
            'low_seconds': 1 if old else 120, 'high_seconds': 2 if old else 180,
            'basis': '旧估算不得覆盖' if old else '当前报告估算'}))

    async def poll_response(route):
        counts['poll'] += 1
        if counts['poll'] == 1:
            first_poll.set()
            await release_old.wait()
            await route.fulfill(status=503, content_type='application/json', body='{"detail":"旧请求失败"}')
            return
        await route.fulfill(content_type='application/json', body=json.dumps({
            'id': 'qa-current-report', 'generation': 2, 'status': 'running',
            'stage': '当前报告阶段', 'completed': 1, 'total': 3, 'elapsed_seconds': 2}))

    await page.route(estimate_url, estimate_response)
    await page.route(poll_url, poll_response)
    try:
        await page.evaluate('(id) => { void followReport(id, false); }', session_id)
        await asyncio.wait_for(asyncio.gather(first_estimate.wait(), first_poll.wait()), 5)
        await page.evaluate('(id) => followReport(id, false)', session_id)
        await expect(page.locator('#report-stage')).to_have_text('当前报告阶段')
        await expect(page.locator('#report-estimate-basis')).to_contain_text('当前报告估算')
        await page.evaluate("() => { $('input-answer').value = '新上下文草稿'; state.isBusy = true; window.qaCurrentPoll = state.reportPoll; }")
        release_old.set()
        await page.wait_for_function("() => $('report-stage').textContent === '当前报告阶段' && $('report-estimate-basis').textContent.includes('当前报告估算')")
        # Drain all route responses and promise callbacks, not the polling timer.
        await page.evaluate('() => new Promise(resolve => setTimeout(resolve, 100))')
        assert await page.evaluate('state.reportPoll === window.qaCurrentPoll && state.isBusy')
        await expect(page.locator('#input-answer')).to_have_value('新上下文草稿')
        await expect(page.locator('#report-count')).to_have_text('已保存 1 / 3 个分析阶段')
    finally:
        release_old.set()
        await page.evaluate("() => { invalidateSessionContext(); state.isBusy = false; clearAnswerDrafts(); }")
        await page.unroute(estimate_url, estimate_response)
        await page.unroute(poll_url, poll_response)


async def run_live_flow(page, base_url, output_dir):
    requests = []
    speech_requests = []
    page.on('request', lambda request: requests.append(request.post_data_json) if request.method == 'POST' and request.url.endswith('/api/chat') else None)
    page.on('request', lambda request: speech_requests.append(request.url) if request.method == 'POST' and request.url.endswith('/api/tts/stream') else None)
    page.on('dialog', lambda dialog: dialog.accept())
    await page.goto(base_url)
    await expect(page.locator('#service-status-title')).to_have_text('模型配置已加载')
    await page.locator('.job-card').first.get_by_role('button', name='开始面试', exact=True).click()
    await page.locator('#btn-run-interview').click()
    await assert_pure_live(page)
    await page.screenshot(path=str(output_dir / 'live-practice.png'))
    await page.locator('#input-answer').fill('测试延迟下一题：我负责需求访谈。' + uuid.uuid4().hex)
    async with page.expect_response(lambda response: response.url.endswith('/api/chat')):
        await page.locator('#btn-send').click()
    session_id = await page.evaluate('state.sessionId')
    await page.goto(base_url + '/?session=' + session_id)
    await assert_pure_live(page)
    await expect(page.locator('#btn-recover-question')).to_have_text('刷新当前问题')
    deadline = time.monotonic() + 10
    while True:
        waiting = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
        if waiting['active_question']:
            break
        assert time.monotonic() < deadline, waiting.get('next_question_job')
        await asyncio.sleep(0.1)
    speech_before_refresh = len(speech_requests)
    await page.locator('#btn-recover-question').click()
    await expect(page.locator('#input-answer')).to_be_enabled()
    await page.evaluate('() => new Promise(resolve => setTimeout(resolve, 100))')
    assert len(requests) == 1, 'Refreshing the current question must not POST another answer'
    assert len(speech_requests) == speech_before_refresh, 'Read-only recovery must not invoke a speech model'
    await page.locator('#input-answer').fill('测试触发下一题失败：我负责访谈并记录验证结果。' + uuid.uuid4().hex)
    await page.locator('#btn-send').click()
    await expect(page.locator('#btn-recover-question')).to_be_visible(timeout=15000)
    session_id = await page.evaluate('state.sessionId')
    session = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert len(session['turns']) == 2 and session['active_question'] is None
    assert requests[0]['question_id'] and requests[0]['attempt'] == 1 and requests[0]['operation_id']
    await page.locator('#btn-recover-question').click()
    await expect(page.locator('#btn-recover-question')).not_to_be_visible()
    await expect(page.locator('#input-answer')).to_be_enabled()
    recovered = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert len(recovered['turns']) == 2
    await assert_pure_live(page)
    await page.locator('#btn-end').click()
    await page.locator('#btn-end-report').click()
    await expect(page.locator('[data-retry-question]').first).to_be_visible(timeout=30000)
    await assert_history_dom(page, base_url, session_id)
    question_id = await page.locator('[data-retry-question]').first.get_attribute('data-retry-question')
    await page.locator('[data-retry-question]').first.click()
    await assert_pure_live(page)
    # An old pending report pointer must not steal the active retry on reload.
    await page.evaluate('(id) => localStorage.setItem("interview-sim-pending-report", id)', session_id)
    await page.reload()
    await assert_pure_live(page)
    await expect(page.locator('#interview-round')).to_contain_text('重答')
    await page.screenshot(path=str(output_dir / 'live-retry-restored.png'))
    await page.locator('#input-answer').fill('这次重答仍然只说明我实际完成的访谈工作。')
    await page.locator('#btn-send').click()
    await expect(page.locator('#input-answer')).to_be_disabled()
    await expect(page.locator('#chat-area')).to_contain_text('本次重答已保存')
    saved = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert saved['active_question'] is None and saved['status'] == 'interview_finished'
    assert len([turn for turn in saved['turns'] if turn['question_id'] == question_id]) == 2
    await page.screenshot(path=str(output_dir / 'live-retry-finished.png'))
    # A second explicit retry can be skipped, with no ordinary next question.
    response = await page.request.post(base_url + '/api/sessions/' + session_id + '/retry', data={'question_id': question_id})
    assert response.ok
    await page.goto(base_url + '/?session=' + session_id)
    await assert_pure_live(page)
    await page.locator('#btn-skip').click()
    await expect(page.locator('#input-answer')).to_be_disabled()
    await expect(page.locator('#chat-area')).to_contain_text('本次重答已保存')
    skipped = await (await page.request.get(base_url + '/api/sessions/' + session_id)).json()
    assert skipped['active_question'] is None and skipped['status'] == 'interview_finished'
    assert skipped['turns'][-1]['status'] == 'unanswered'
    await assert_report_epochs(page, base_url, session_id)
