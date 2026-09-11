"""Desktop browser acceptance flow against ui_fixture_server (synthetic data only)."""
import asyncio
import json
import os
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright, expect
from browser_handoff import check_job_handoff


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
        await check_job_handoff(browser, base_url, output)
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
        await card.get_by_role('button', name='选择岗位 · 配置面试').click()
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
        assert 'report_job' not in saved and len(saved['turns']) == 3
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
        await page.reload()
        await expect(page.locator('#report-content')).to_contain_text('逐题证据', timeout=30000)
        await expect(page.locator('.answer-quote').filter(has_text='未回答')).to_have_count(2)
        await expect(page.locator('#btn-export-json')).to_be_enabled()
        sessions = await (await page.request.get(base_url+'/api/sessions')).json()
        session = await (await page.request.get(base_url+'/api/sessions/'+sessions[0]['id'])).json()
        assert session['config']['company'] == '示例内容科技公司'
        assert session['config']['interviewer_gender'] == '男性'
        assert session['config']['voice'] == '白桦'
        assert session['report_job']['status'] == 'completed'
        assert len(session['review']['question_feedback']) == 3
        await page.goto(base_url+'/?session='+sessions[0]['id'])
        await expect(page.locator('#report-content')).to_contain_text('逐题证据')
        # A new interview resets the ended state, and the original report path remains usable.
        await page.locator('[data-view=config]').click()
        await page.locator('.job-card').first.get_by_role('button', name='选择岗位 · 配置面试').click()
        await page.locator('#btn-run-interview').click()
        await expect(page.locator('#interview-setup')).not_to_be_visible()
        await expect(page.locator('#btn-end')).to_have_text('结束面试')
        await page.locator('#btn-end').click()
        await page.locator('#btn-end-report').click()
        await expect(page.locator('#report-content')).to_contain_text('逐题证据', timeout=30000)
        assert not errors, errors
        print(json.dumps({'browser_flow':'passed','screenshots':str(output),'console_errors':errors}, ensure_ascii=False))
        await browser.close()


asyncio.run(main())
