"""Desktop browser acceptance flow against ui_fixture_server (synthetic data only)."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright, expect
from tests.preparation_browser_flow import run_preparation_flow
from tests.live_browser_flow import run_live_flow
from tests.report_browser_flow import run_report_flow
from tests.raw_voice_browser_flow import run_raw_voice_flow


async def wait_for_rendered_report(page, answer_count):
    # Only the completed v2 renderer creates these nodes; neither the initial
    # placeholder nor an in-progress stage labelled "逐题证据" qualifies.
    await expect(page.locator('#view-report')).to_be_visible(timeout=30000)
    await expect(page.locator('#report-content .report-version-note')).to_contain_text('interview-evidence-v2', timeout=30000)
    await expect(page.locator('#report-content .feedback-card')).to_have_count(answer_count, timeout=30000)
    for button in ('btn-export-md', 'btn-export-json', 'btn-print'):
        await expect(page.locator('#' + button)).to_be_enabled()


async def main():
    output = Path(tempfile.mkdtemp(prefix='interview-ui-check-'))
    async with async_playwright() as p:
        options = {'headless': True}
        if os.environ.get('BROWSER_EXECUTABLE'):
            options['executable_path'] = os.environ['BROWSER_EXECUTABLE']
        browser = await p.chromium.launch(**options)
        page = await browser.new_page(viewport={'width':1440,'height':1080})
        errors = []
        report_requests = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('request', lambda request: report_requests.append(request.url) if request.method == 'POST' and '/api/review' in request.url else None)
        page.on('dialog', lambda dialog: dialog.accept())
        base_url = 'http://127.0.0.1:' + os.environ.get('INTERVIEW_SIM_TEST_PORT', '8830')
        await page.goto(base_url)
        await expect(page.locator('#service-status-title')).to_have_text('模型配置已加载')
        await page.locator('#quick-prep > summary').click()
        await page.locator('#auto-speak').uncheck()
        await page.locator('#quick-prep > summary').click()
        await page.locator('#btn-new-preset').click()
        await page.locator('#job-source').fill('示例内容科技公司，位于无锡，招聘 AI 应用工程师，月薪7-16K。负责开发AI内容工具。')
        await page.locator('#btn-parse-job').click()
        await expect(page.locator('#job-company')).to_have_value('示例内容科技公司')
        await page.locator('#job-resume').fill('参与内容工具项目，负责需求访谈和 Python 工具实现。')
        await page.locator('#job-editor').evaluate('(dialog) => { dialog.scrollTop = 0; }')
        await page.screenshot(path=str(output/'job-editor.png'))
        await page.locator('#btn-save-preset').click()
        await expect(page.locator('#job-editor')).not_to_be_visible()
        card = page.locator('.job-card').filter(has_text='示例内容科技公司').first
        await card.get_by_role('button', name='开始面试', exact=True).click()
        await page.locator('.choice').filter(has=page.locator('input[name=run-persona][value=CEO]')).click()
        await page.locator('.choice').filter(has=page.locator('input[name=run-gender][value=男性]')).click()
        await page.locator('.choice').filter(has=page.locator('input[name=run-difficulty][value=压力]')).click()
        await page.screenshot(path=str(output/'interview-setup.png'))
        await page.locator('#btn-run-interview').click()
        await expect(page.locator('#interview-setup')).not_to_be_visible()
        await expect(page.locator('#interview-persona')).to_have_text('CEO')
        await expect(page.locator('#interview-difficulty')).to_have_text('压力')
        await page.locator('#input-answer').fill('我负责需求访谈，并将结论转成工具迭代计划。')
        await page.locator('#btn-send').click()
        await expect(page.locator('#interview-round')).to_have_text('第 2 题')
        await page.locator('#input-answer').fill('尚未发送的草稿')
        await page.locator('#btn-end').click()
        await expect(page.locator('#end-interview-dialog')).not_to_be_visible()
        await expect(page.locator('#input-answer')).to_have_value('尚未发送的草稿')
        await page.locator('#input-answer').fill('')
        await page.locator('#btn-skip').click()
        await expect(page.locator('#interview-round')).to_have_text('第 3 题')
        await page.locator('#btn-skip').click()
        # Adaptive q-2 did not consume blueprint item 2. Both remaining
        # blueprint items must still be asked, rather than treating q-N as coverage.
        await expect(page.locator('#interview-round')).to_have_text('第 4 题')
        await page.locator('#btn-end').click()
        await expect(page.locator('#end-interview-dialog')).to_be_visible()
        await page.locator('#btn-cancel-end').click()
        await expect(page.locator('#end-interview-dialog')).not_to_be_visible()
        await page.locator('#btn-end').click()
        await page.screenshot(path=str(output/'end-interview-options.png'))
        await page.route('**/api/sessions/*/end', lambda route: route.fulfill(status=503, content_type='application/json', body='{"detail":"测试保存失败"}'))
        await page.locator('#btn-end-only').click()
        await expect(page.locator('#end-interview-error')).to_contain_text('测试保存失败')
        await expect(page.locator('#end-interview-dialog')).to_be_visible()
        await page.unroute('**/api/sessions/*/end')
        await page.locator('#btn-end-only').click()
        await expect(page.locator('#view-history')).to_be_visible()
        await expect(page.locator('.history-card').first).to_contain_text('已结束 · 未生成报告')
        assert not report_requests, report_requests
        sessions = await (await page.request.get(base_url+'/api/sessions')).json()
        saved_id = sessions[0]['id']
        saved = await (await page.request.get(base_url+'/api/sessions/'+saved_id)).json()
        assert saved['status'] == 'ended' and saved['review'] is None
        assert 'report_job' not in saved and len(saved['turns']) == 4
        assert [turn['blueprint_id'] for turn in saved['turns']] == [1, 1, 2, 3]
        await page.reload()
        await page.locator('[data-view=history]').click()
        await page.locator(f'[data-session-id="{saved_id}"]').click()
        await expect(page.locator('#chat-area')).to_contain_text('面试已结束，记录已保存，未生成报告')
        await expect(page.locator('#input-answer')).to_be_disabled()
        await expect(page.locator('#btn-end')).to_have_text('生成报告')
        assert not report_requests, report_requests
        await page.locator('#btn-end').click()
        await expect(page.locator('#report-stage')).to_be_visible()
        await expect(page.locator('#report-estimate')).to_contain_text('预计约')
        await page.screenshot(path=str(output/'report-progress.png'))
        restore_requested, release_restore = asyncio.Event(), asyncio.Event()

        async def hold_session_restore(route):
            restore_requested.set()
            await release_restore.wait()
            await route.continue_()

        await page.route(base_url + '/api/sessions/' + saved_id, hold_session_restore, times=1)
        await page.reload()
        try:
            await asyncio.wait_for(restore_requested.wait(), timeout=5)
            for button in ('btn-export-md', 'btn-export-json', 'btn-print'):
                await expect(page.locator('#' + button)).to_be_disabled()
        finally:
            release_restore.set()
        await wait_for_rendered_report(page, 4)
        await expect(page.locator('.answer-quote').filter(has_text='未回答')).to_have_count(3)
        sessions = await (await page.request.get(base_url+'/api/sessions')).json()
        session = await (await page.request.get(base_url+'/api/sessions/'+sessions[0]['id'])).json()
        assert session['config']['company'] == '示例内容科技公司'
        assert session['config']['interviewer_gender'] == '男性'
        assert session['config']['voice'] == '白桦'
        assert session['report_job']['status'] == 'completed'
        assert len(session['review']['question_feedback']) == 4
        await page.goto(base_url+'/?session='+sessions[0]['id'])
        await wait_for_rendered_report(page, 4)
        # A new interview resets the ended state, and the original report path remains usable.
        await page.locator('[data-view=config]').click()
        await page.locator('.job-card').first.get_by_role('button', name='开始面试', exact=True).click()
        await page.locator('#btn-run-interview').click()
        await expect(page.locator('#interview-setup')).not_to_be_visible()
        await expect(page.locator('#btn-end')).to_have_text('结束面试')
        await page.locator('#btn-end').click()
        await page.locator('#btn-end-report').click()
        await wait_for_rendered_report(page, 1)
        preparation_page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        preparation_page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            await run_preparation_flow(preparation_page, base_url, output)
        finally:
            await preparation_page.close()
        live_page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        live_page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            await run_live_flow(live_page, base_url, output)
        finally:
            await live_page.close()
        report_page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        report_page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            await run_report_flow(report_page, base_url, output)
        finally:
            await report_page.close()
        voice_page = await browser.new_page(viewport={'width': 1440, 'height': 1080})
        voice_page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            await run_raw_voice_flow(voice_page, base_url, output)
        finally:
            await voice_page.close()
        assert not errors, errors
        print(json.dumps({'browser_flow':'passed','screenshots':str(output),'console_errors':errors}, ensure_ascii=False))
        await browser.close()


asyncio.run(main())
