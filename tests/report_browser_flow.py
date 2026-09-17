"""P4 report semantics and explicitly confirmed learning loop, synthetic only."""
import asyncio
import json

from playwright.async_api import expect


def schema_two_report():
    names = ('relevance', 'evidence', 'professional_content', 'expression')

    def aggregate(total, count):
        return {'total': total, 'question_count': count, 'answered_count': count, 'unanswered_count': 0,
            'dimensions': {key: {'score': total / 10, 'max': 10, 'weight': 25} for key in names},
            'scope_note': '仅按实际问题统计，不含未问岗位要求'}

    feedback = []
    for question, attempt, score in [('q-1', 1, 7), ('q-2', 1, 9), ('q-1', 2, 6)]:
        answer = '我负责整理需求访谈记录。'
        feedback.append({'question_id': question, 'attempt': attempt, 'question': '请说明个人贡献。',
            'answer': answer, 'status': 'answered', 'dimensions': {key: {'score': score, 'comment': '依据原答'} for key in names},
            'score': score, 'max_score': 10, 'confidence': 'medium', 'evidence_quotes': [answer],
            'covered_points': ['个人行动'], 'missed_points': ['验证结果'], 'question_explanation': '考察可验证的个人贡献',
            'coaching_tip': '补充实际验证方式', 'improved_answer_outline': '说明访谈对象，再补充实际验证。', 'reason_analysis': ''})
    return {'schema_version': 2, 'rubric_version': 'interview-evidence-v1', 'report_id': 'qa-report-v2',
        'input_fingerprint': 'qa-synthetic-fingerprint', 'generated_at': '2026-09-15T12:00:00Z',
        'resume_quality': {'status': 'not_assessed', 'assessment': None, 'reason': '本场版本未诊断', 'resume_version_id': 'qa-version'},
        'interview_performance': {'first_attempt': aggregate(80, 2), 'latest_retry': aggregate(60, 1),
            'comparison': {'paired_question_count': 1, 'original_total': 70, 'latest_total': 60, 'delta': -10,
                'items': [{'question_id': 'q-1', 'first_attempt': 1, 'latest_attempt': 2, 'original_total': 70, 'latest_total': 60, 'delta': -10}]},
            'note': '重答不改变首次面试表现'},
        'requirement_coverage': {'status': 'available', 'requirements': [{'requirement': '需求分析', 'priority': 'must',
            'status': 'supported', 'source_quote': '负责需求分析', 'evidence': [{'source_id': 'resume', 'quote': '参与需求分析'}],
            'interview_status': 'not_observed', 'interview_evidence': [], 'note': '材料有记录，面试未验证'}], 'note': '不是录取概率'},
        'question_feedback': feedback, 'interview_tips': ['只说明真实贡献'], 'practice_plan': [{'question_id': 'q-1', 'focus': '验证结果', 'reason': '原答没有结果证据'}]}


async def assert_report_rendering(page, base_url, output_dir):
    await page.goto(base_url)
    await expect(page.locator('#service-status-title')).to_have_text('模型配置已加载')
    report = schema_two_report()
    await page.evaluate('(data) => { state.lastReport = data; renderReport(data); enableView("report"); switchView("report"); setReportExportReady(true); }', report)
    await expect(page.locator('#report-first-total')).to_have_text('80/100')
    await expect(page.locator('#report-retry-total')).to_have_text('60/100')
    await expect(page.locator('#report-resume-total')).to_have_text('未评估')
    await expect(page.locator('#report-comparison')).to_contain_text('70 → 60')
    await expect(page.locator('#report-comparison')).to_contain_text('-10')
    await expect(page.locator('#report-coverage')).to_contain_text('未观察到')
    await expect(page.locator('#report-coverage')).not_to_contain_text('0 分')
    assert (await page.locator('#report-coverage blockquote').first.bounding_box())['width'] > 120
    markdown = await page.evaluate('(data) => renderReportMarkdown(data)', report)
    assert '首次面试表现：80/100' in markdown and '最新重答表现：60/100' in markdown
    assert '同题配对：70 → 60' in markdown and '简历质量：未评估' in markdown
    assert '旧版五维报告' not in markdown and '岗位定制简历' not in markdown
    await expect(page.locator('#report-learning-status')).to_contain_text('未关联岗位')
    await expect(page.locator('#btn-extract-materials')).to_be_disabled()
    for width in (1440, 1024):
        await page.set_viewport_size({'width': width, 'height': 1000})
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert (await page.locator('.feedback-card').first.bounding_box())['width'] > 450
        await page.screenshot(path=str(output_dir / f'report-v2-{width}.png'), full_page=True)
    await page.emulate_media(media='print')
    await expect(page.locator('#report-first-total')).to_be_visible()
    await expect(page.locator('#report-comparison')).to_be_visible()
    await page.emulate_media(media='screen')
    async with page.expect_download() as export:
        await page.locator('#btn-export-json').click()
    exported = await (await export.value).path()
    from pathlib import Path
    raw = json.loads(Path(exported).read_text(encoding='utf-8'))
    assert raw['schema_version'] == 2 and 'score' not in raw
    assert raw['interview_performance']['first_attempt']['total'] == 80
    legacy = {'score': {'total': 35, 'dimensions': {}, 'conclusion': '历史评价'}, 'question_feedback': []}
    await page.evaluate('(data) => renderReport(data)', legacy)
    await expect(page.locator('#report-content')).to_contain_text('旧版五维报告')
    await expect(page.locator('#report-first-total')).to_have_count(0)
    old_markdown = await page.evaluate('(data) => renderReportMarkdown(data)', legacy)
    assert '旧版五维报告' in old_markdown and '总分：35/100' in old_markdown
    diagnosed = schema_two_report()
    diagnosed['resume_quality'].update(status='assessed', assessment={'total': 70, 'dimensions': {
        key: {'score': 7, 'comment': '简历诊断独立说明'} for key in ('clarity', 'structure', 'relevance', 'evidence')}, 'limitations': ['不评价视觉排版']})
    diagnosed_md = await page.evaluate('(data) => renderReportMarkdown(data)', diagnosed)
    assert '简历诊断·内容清晰：7/10' in diagnosed_md and '不评价视觉排版' in diagnosed_md
    history = [{'id': 'qa-new', 'job_title': '新评分历史', 'created_at': '2026-09-15T12:00:00Z', 'status': 'completed', 'score': 30, 'score_kind': 'interview_first_attempt'},
        {'id': 'qa-old', 'job_title': '旧评分历史', 'created_at': '2026-09-15T11:00:00Z', 'status': 'completed', 'score': 35, 'score_kind': 'legacy'}]
    await page.route(base_url + '/api/sessions', lambda route: route.fulfill(content_type='application/json', body=json.dumps(history)))
    await page.locator('[data-view=history]').click()
    await expect(page.locator('[data-session-id=qa-new]')).to_contain_text('首次面试')
    await expect(page.locator('[data-session-id=qa-old]')).to_contain_text('旧版五维')
    await page.unroute(base_url + '/api/sessions')


async def assert_material_generations(page, base_url, job_id, task):
    """A slow old GET cannot overwrite a cancel followed by explicit retry."""
    task_url = base_url + '/api/preparation/' + job_id + '/tasks/' + task['id']
    started, release_old = asyncio.Event(), asyncio.Event()
    generation = task['generation']
    reads = 0

    async def read(route):
        nonlocal reads
        reads += 1
        if reads == 1:
            started.set()
            await release_old.wait()
            value = {**task, 'status': 'completed', 'stage': '迟到旧素材结果'}
        else:
            value = {**task, 'generation': generation, 'status': 'running', 'stage': '新的提取代次', 'result': None}
        await route.fulfill(content_type='application/json', body=json.dumps({'task': value, 'revision': 1}))

    async def cancel(route):
        await route.fulfill(content_type='application/json', body=json.dumps({'task': {**task, 'generation': generation,
            'status': 'cancelled', 'stage': '已取消提取', 'result': None}, 'revision': 1}))

    async def retry(route):
        nonlocal generation
        generation += 1
        await route.fulfill(content_type='application/json', body=json.dumps({'task': {**task, 'generation': generation,
            'status': 'running', 'stage': '新的提取代次', 'result': None}, 'revision': 1}))

    await page.route(task_url, read)
    await page.route(task_url + '/cancel', cancel)
    await page.route(task_url + '/retry', retry)
    try:
        await page.evaluate('(task) => { state.reportLearning.task = {...task, status:"running", result:null}; renderReportLearning(state.reportLearning); scheduleReportLearning(state.reportLearning); }', task)
        await asyncio.wait_for(started.wait(), 5)
        await page.locator('[data-report-action=cancel]').click()
        await expect(page.locator('#report-material-task')).to_contain_text('已取消')
        await page.locator('[data-report-action=retry]').click()
        await expect(page.locator('#report-material-task')).to_contain_text('新的提取代次')
        release_old.set()
        await page.evaluate('() => new Promise(resolve => setTimeout(resolve, 100))')
        await expect(page.locator('#report-material-task')).not_to_contain_text('迟到旧素材结果')
        assert await page.evaluate('state.reportLearning.task.generation') == generation
        await page.locator('[data-report-action=cancel]').click()
        await expect(page.locator('#report-material-task')).to_contain_text('已取消')
    finally:
        release_old.set()
        await page.unroute(task_url, read)
        await page.unroute(task_url + '/cancel', cancel)
        await page.unroute(task_url + '/retry', retry)
    await page.evaluate('refreshReportLearning()')
    await expect(page.locator('#report-material-task')).to_contain_text('提取已完成')


async def run_report_flow(page, base_url, output_dir):
    await assert_report_rendering(page, base_url, output_dir)
    page.on('dialog', lambda dialog: dialog.accept())
    extractions = []
    page.on('request', lambda request: extractions.append(request.url) if request.method == 'POST' and request.url.endswith('/session-materials') else None)
    response = await page.request.post(base_url + '/api/presets', data={
        'name': 'QA 复盘闭环岗位', 'target_role': 'QA 复盘闭环岗位', 'company': '虚构闭环公司',
        'jd': '岗位需要开展需求访谈并验证产品方案。', 'resume': '参与了一个测试项目，负责需求访谈。'})
    assert response.ok
    job = await response.json()
    await page.goto(base_url)
    card = page.locator('.job-card').filter(has_text='QA 复盘闭环岗位').first
    await card.get_by_role('button', name='准备这个岗位', exact=True).click()
    await expect(page.locator('#prep-resume')).not_to_have_value('')
    first_version = await page.locator('#prep-version').input_value()
    await page.locator('[data-prep-task=resume]').click()
    await expect(page.locator('#prep-assessment')).to_contain_text('简历质量', timeout=15000)
    await page.locator('[data-prep-task=recruitment]').click()
    await expect(page.locator('#prep-requirements')).to_contain_text('需要验证', timeout=15000)
    await page.locator('#prep-start').click()
    await expect(page.locator('#btn-run-interview')).to_be_enabled()
    await page.locator('#btn-run-interview').focus()
    await page.locator('#btn-run-interview').press('Enter')
    await expect(page.locator('#view-interview')).to_be_visible()
    for index, answer in enumerate(['我独立整理了需求访谈原始记录，并将每条请求标注来源。', '我根据访谈记录整理了验证清单，并请同伴复核。'], start=2):
        await page.locator('#input-answer').fill(answer)
        await page.locator('#btn-send').click()
        await expect(page.locator('#interview-round')).to_have_text(f'第 {index} 题')
    await page.locator('#btn-skip').click()
    await expect(page.locator('#interview-round')).to_have_text('第 4 题')
    await page.locator('#btn-end').click()
    await page.locator('#btn-end-report').click()
    await expect(page.locator('#btn-export-json')).to_be_enabled(timeout=30000)
    await expect(page.locator('#report-resume-total')).to_have_text('70/100')
    await expect(page.locator('#report-retry-total')).to_have_text('未评估')
    await expect(page.locator('#report-coverage')).to_contain_text('未观察到')
    assert not extractions, 'Generating a report must not automatically extract experience'
    session_id = await page.evaluate('state.sessionId')
    dossier_url = base_url + '/api/preparation/' + job['id']
    dossier = await (await page.request.get(dossier_url)).json()
    assert not dossier['facts']
    await expect(page.locator('#btn-extract-materials')).to_be_enabled()
    await page.locator('#btn-extract-materials').click()
    await expect(page.locator('#report-material-task')).to_contain_text('提取已完成', timeout=15000)
    dossier = await (await page.request.get(dossier_url)).json()
    assert len(dossier['facts']) == 2 and all(f['status'] == 'pending' for f in dossier['facts'])
    assert all(f['source_version_id'] == first_version and f['source']['session_id'] == session_id for f in dossier['facts'])
    assert all(f['text'] in f['source_quote'] for f in dossier['facts'])
    await assert_material_generations(page, base_url, job['id'], next(task for task in dossier['tasks'] if task['kind'] == 'session_materials'))
    revision = dossier['revision']
    await page.locator('#btn-extract-materials').click()
    await expect(page.locator('#btn-extract-materials')).to_be_enabled()
    repeated = await (await page.request.get(dossier_url)).json()
    assert len(repeated['facts']) == 2 and repeated['revision'] == revision
    await page.locator('#btn-return-preparation').click()
    await expect(page.locator('#prep-version')).to_have_value(first_version)
    await expect(page.locator('#prep-facts .prep-fact')).to_have_count(2)
    await page.locator('[data-prep-disclosure="facts"] > summary').click()
    first = page.locator('#prep-facts .prep-fact').first
    revised = '我独立整理了需求访谈原始记录。'
    await first.locator('textarea').fill(revised)
    await first.locator('[data-fact-decision=confirm]').click()
    await expect(page.locator('#prep-status')).to_contain_text('修订已进入待确认')
    revised_card = page.locator('#prep-facts .prep-fact').filter(has=page.locator('textarea[data-original="' + revised + '"]'))
    await expect(revised_card.locator('h3')).to_have_text('待确认')
    await revised_card.locator('[data-fact-decision=confirm]').click()
    await expect(revised_card.locator('h3')).to_have_text('已确认')
    pending = page.locator('#prep-facts .prep-fact').filter(has=page.get_by_role('heading', name='待确认', exact=True))
    await pending.locator('[data-fact-decision=reject]').click()
    await expect(page.locator('#prep-status')).to_contain_text('已拒绝')
    await page.screenshot(path=str(output_dir / 'report-confirmed-materials.png'), full_page=True)
    original_resume = await page.locator('#prep-resume').input_value()
    await page.locator('#prep-resume').fill(original_resume + '\n' + revised)
    await page.locator('#prep-version-name').fill('复盘后本人核对的版本')
    await page.locator('#prep-save').click()
    await expect(page.locator('#prep-version')).not_to_have_value(first_version)
    new_version = await page.locator('#prep-version').input_value()
    await expect(page.locator('#prep-assessment')).to_contain_text('未评估')
    await page.locator('#prep-start').click()
    await page.locator('#btn-run-interview').click()
    await expect(page.locator('#view-interview')).to_be_visible()
    new_session = await page.evaluate('state.sessionId')
    assert new_session != session_id
    saved = await (await page.request.get(base_url + '/api/sessions/' + new_session)).json()
    assert saved['config']['resume_version_id'] == new_version
    assert revised in saved['config']['resume']
    assert saved['config']['preset_id'] == job['id']
    await expect(page.locator('#current-focus, #focus-detail, #blueprint-list')).to_have_count(0)
