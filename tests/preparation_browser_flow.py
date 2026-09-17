"""Job preparation acceptance with synthetic data and the shared fake-model server."""
import asyncio

from playwright.async_api import expect


async def run_preparation_flow(page, base_url, output_dir):
    await page.goto(base_url)
    await expect(page.locator('#service-status-title')).to_have_text('模型配置已加载')
    card = page.locator('.job-card').filter(has_text='QA 测试岗位').first
    if await card.count() == 0:
        card = page.locator('.job-card').filter(has_text='示例公司').first
    await expect(card.get_by_role('button', name='准备这个岗位', exact=True)).to_be_visible()
    # Leave during the first load; the late response must not strand the page.
    gate = asyncio.Event()
    requested = asyncio.Event()
    async def delayed_dossier(route):
        response = await route.fetch()
        requested.set()
        await gate.wait()
        await route.fulfill(response=response)
    await page.route('**/api/preparation/*', delayed_dossier, times=1)
    await card.get_by_role('button', name='准备这个岗位', exact=True).click()
    await requested.wait()
    await page.locator('[data-view=settings]').click()
    gate.set()
    await page.locator('[data-view=preparation]').click()
    await expect(page.locator('#view-preparation')).to_be_visible()
    await expect(page.locator('#prep-resume')).not_to_have_value('')
    original = await page.locator('#prep-resume').input_value()
    initial_version = await page.locator('#prep-version').input_value()
    await expect(page.locator('#prep-assessment')).to_contain_text('未评估')

    # A dirty editor must not be silently overwritten by navigation.
    await page.locator('#prep-resume').fill(original + '\n补充的本地草稿')
    async def reject_dialog(dialog):
        await dialog.dismiss()
    page.on('dialog', reject_dialog)
    await page.locator('[data-view=settings]').click()
    await expect(page.locator('#view-preparation')).to_be_visible()
    await expect(page.locator('#prep-resume')).to_have_value(original + '\n补充的本地草稿')
    page.remove_listener('dialog', reject_dialog)
    await page.locator('#prep-resume').fill(original)

    await page.locator('[data-prep-task=resume]').click()
    await expect(page.locator('#prep-assessment')).to_contain_text('简历质量', timeout=15000)
    await expect(page.locator('[data-accept-suggestion]')).to_have_count(2)
    # Real disclosure behavior: collapsed initially, keyboard operable, and
    # polling/refresh must not reset the reader's current expansion or focus.
    suggestion = page.locator('#prep-suggestions details.prep-suggestion').first
    advice = page.locator('#prep-advice details.prep-advice-item').first
    await expect(suggestion).not_to_have_attribute('open', '')
    await expect(suggestion.locator('dl')).not_to_be_visible()
    await expect(advice.locator('.prep-disclosure-body')).not_to_be_visible()
    await page.evaluate('window.scrollTo(0, 0)')
    await page.screenshot(path=str(output_dir / 'resume-advice-collapsed-1440.png'), full_page=True)
    await page.set_viewport_size({'width': 1024, 'height': 900})
    assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    await page.screenshot(path=str(output_dir / 'resume-advice-collapsed-1024.png'), full_page=True)
    await page.set_viewport_size({'width': 1440, 'height': 1080})
    await advice.locator('summary').focus()
    await page.keyboard.press('Enter')
    await expect(advice.locator('.prep-disclosure-body')).to_be_visible()
    await expect(advice).to_contain_text('真实交付物')
    await page.evaluate('renderPreparation(false)')
    await expect(advice.locator('summary')).to_be_focused()
    await expect(advice.locator('.prep-disclosure-body')).to_be_visible()
    await page.screenshot(path=str(output_dir / 'resume-advice-expanded-1440.png'), full_page=True)
    await expect(page.locator('[data-prep-disclosure="requirements"]')).not_to_have_attribute('open', '')
    await page.locator('#prep-refresh').click()
    await expect(advice.locator('.prep-disclosure-body')).to_be_visible()
    await page.locator('[data-prep-fold="prep-suggestions"][data-expand="true"]').click()
    await expect(page.locator('#prep-suggestions details[open]')).to_have_count(2)
    await page.locator('[data-prep-fold="prep-suggestions"][data-expand="false"]').click()
    await expect(page.locator('#prep-suggestions details[open]')).to_have_count(0)
    await page.locator('[data-prep-task=recruitment]').click()
    await expect(page.locator('#prep-requirements')).to_contain_text('需要验证', timeout=15000)
    await page.locator('[data-prep-task=materials]').click()
    await expect(page.locator('#prep-materials')).to_contain_text('项目经历提纲', timeout=15000)
    async def accept_dialog(dialog):
        await dialog.accept()
    page.on('dialog', accept_dialog)
    await suggestion.locator('summary').click()
    await page.locator('[data-accept-suggestion]').first.click()
    await expect(page.locator('#prep-version')).not_to_have_value(initial_version)
    first_adopted_version = await page.locator('#prep-version').input_value()
    second = page.locator('details.prep-suggestion').filter(has=page.locator('[data-accept-suggestion]:not([disabled])')).first
    await second.locator('summary').click()
    await page.locator('[data-accept-suggestion]:not([disabled])').first.click()
    await expect(page.locator('#prep-version')).not_to_have_value(first_adopted_version)
    await expect(page.locator('[data-accept-suggestion][disabled]')).to_have_count(2)
    adopted_text = await page.locator('#prep-resume').input_value()
    assert adopted_text != original
    adopted_version = await page.locator('#prep-version').input_value()
    await expect(page.locator('#prep-assessment')).to_contain_text('未评估')

    await page.locator('#prep-version').select_option(initial_version)
    await expect(page.locator('#prep-resume')).to_have_value(original)
    await expect(page.locator('#prep-assessment')).to_contain_text('简历质量')
    await page.locator('#prep-version').select_option(adopted_version)
    await expect(page.locator('#prep-resume')).to_have_value(adopted_text)

    # Failed saves keep the draft; an explicit retry creates an immutable child.
    draft = adopted_text + '\n这是一条经本人核对的补充说明。'
    await page.locator('#prep-resume').fill(draft)
    await page.locator('#prep-version-name').fill('核对后的面试版本')
    await page.route('**/api/preparation/*/versions', lambda route: route.fulfill(status=409, content_type='application/json', body='{"detail":"测试版本冲突"}'))
    await page.locator('#prep-save').click()
    await expect(page.locator('#prep-status')).to_contain_text('测试版本冲突')
    await expect(page.locator('#prep-resume')).to_have_value(draft)
    await page.unroute('**/api/preparation/*/versions')
    async def fail_reload_once(route):
        await route.fulfill(status=503, content_type='application/json', body='{"detail":"测试刷新暂不可用"}')
    await page.route('**/api/preparation/*', fail_reload_once, times=1)
    await page.locator('#prep-save').click()
    await expect(page.locator('#prep-status')).to_contain_text('保存成功，但暂时无法刷新')
    await page.locator('#prep-refresh').click()
    await expect(page.locator('#prep-version')).not_to_have_value(adopted_version)
    adopted_version = await page.locator('#prep-version').input_value()
    adopted_text = draft
    await expect(page.locator('#prep-version-name')).to_have_value('')
    await page.evaluate('window.scrollTo(0, 0)')
    await page.screenshot(path=str(output_dir / 'preparation-1440.png'), full_page=True)
    await page.set_viewport_size({'width': 1024, 'height': 900})
    assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    await page.screenshot(path=str(output_dir / 'preparation-1024.png'), full_page=True)
    await page.set_viewport_size({'width': 1440, 'height': 1080})

    # Starting never requires preparation; the chosen immutable revision is sent.
    await page.locator('#prep-start').click()
    await expect(page.locator('#run-resume-version')).to_have_value(adopted_version)
    await page.locator('#btn-run-interview').click()
    await expect(page.locator('#interview-setup')).not_to_be_visible()
    sessions = await (await page.request.get(base_url + '/api/sessions')).json()
    session = await (await page.request.get(base_url + '/api/sessions/' + sessions[0]['id'])).json()
    assert session['config']['resume_version_id'] == adopted_version
    assert session['config']['resume'] == adopted_text
    await page.locator('#btn-end').click()
    await page.locator('#btn-end-only').click()
    await expect(page.locator('#view-history')).to_be_visible()
    page.remove_listener('dialog', accept_dialog)
