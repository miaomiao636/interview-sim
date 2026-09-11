"""Real two-origin browser handoff using synthetic jobs, never real accounts."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import quote
from playwright.async_api import expect


class SenderPage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'<!doctype html><html lang="en"><title>Synthetic job sender</title><button id="open">Open interview</button></html>'
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


async def check_job_handoff(browser, base_url, output):
    server = ThreadingHTTPServer(('127.0.0.1', 0), SenderPage)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    source = f'http://127.0.0.1:{server.server_port}'
    job = {'company': '联通测试示例公司', 'target_role': '开发工程师', 'city': '示例城市', 'salary': '10-15K', 'education': '本科', 'jd': '<script>window.__injected = true</script>\n负责开发工具并验证效果。'}
    target = base_url + '/?import=job&source=' + quote(source, safe='')
    context = await browser.new_context(viewport={'width': 1440, 'height': 1080})
    errors, mutations, requests = [], [], []
    context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
    context.on('request', lambda req: mutations.append(req.url) if req.method in ('POST', 'PUT', 'DELETE') else requests.append(req.url))
    try:
        sender = await context.new_page()
        await sender.goto(source)
        await context.add_init_script("if (location.port !== '" + str(server.server_port) + "') localStorage.setItem('interview-sim-pending-report', 'unrelated-synthetic-report');")
        await sender.evaluate("""({target, origin, job}) => {
          window.acks = [];
          document.querySelector('#open').onclick = () => { window.receiver = window.open(target, '_blank'); };
          addEventListener('message', event => {
            if (event.origin !== origin || event.source !== window.receiver) return;
            window.acks.push(event.data.type);
            if (event.data.type === 'interview-sim:ready') window.receiver.postMessage({type:'interview-sim:job',version:1,job}, origin);
          });
        }""", {'target': target, 'origin': base_url, 'job': job})
        async with sender.expect_popup() as popup_info:
            await sender.locator('#open').click()
        receiver = await popup_info.value
        await expect(receiver.locator('#job-editor')).to_be_visible()
        await expect(receiver.locator('#job-editor-title')).to_have_text('导入岗位 · 未保存草稿')
        await expect(receiver.locator('#job-company')).to_have_value(job['company'])
        await expect(receiver.locator('#job-duties')).to_have_value(job['jd'])
        await expect(receiver.locator('#job-source')).to_have_value(job['jd'])
        await expect(receiver.locator('#job-resume')).to_have_value('')
        await expect(receiver.locator('#job-company-context')).to_have_value('薪资原文（来源岗位）：10-15K')
        await expect(receiver.locator('#job-parse-status')).to_contain_text('尚未保存，也未调用 AI')
        await sender.wait_for_function("window.acks.includes('interview-sim:received')")
        assert await receiver.evaluate('location.search') == ''
        assert await receiver.evaluate('window.opener === null')
        assert not await receiver.evaluate('Boolean(window.__injected)')
        assert not mutations, mutations
        assert not any('unrelated-synthetic-report' in url for url in requests), requests
        await receiver.screenshot(path=str(output / 'job-handoff-draft.png'))
        await receiver.locator('#btn-save-preset').click()
        await expect(receiver.locator('#job-editor')).to_be_visible()
        assert not mutations, mutations
        await receiver.locator('#job-resume').fill('这是虚构测试简历，负责工具开发与效果验证。')
        await receiver.locator('#btn-save-preset').click()
        await expect(receiver.locator('#job-editor')).not_to_be_visible()
        assert mutations == [base_url + '/api/presets'], mutations
        presets = await (await receiver.request.get(base_url + '/api/presets')).json()
        saved = next(item for item in presets if item['company'] == job['company'])
        assert saved['source_jd'] == job['jd'] and job['jd'] in saved['jd']
        await receiver.locator('.job-card').filter(has_text=job['company']).get_by_role('button', name='选择岗位 · 配置面试').click()
        await expect(receiver.locator('#interview-setup')).to_be_visible()
        assert mutations == [base_url + '/api/presets'], mutations
        await receiver.close()

        # Do not overwrite a draft typed while the sending page waits.
        await sender.goto(source)
        await sender.evaluate("""({target, origin}) => {
          window.ready = false;
          document.querySelector('#open').onclick = () => { window.receiver = window.open(target, '_blank'); };
          addEventListener('message', event => {
            if (event.origin === origin && event.source === window.receiver && event.data.type === 'interview-sim:ready') window.ready = true;
          });
        }""", {'target': target, 'origin': base_url})
        async with sender.expect_popup() as popup_info:
            await sender.locator('#open').click()
        busy_receiver = await popup_info.value
        await sender.wait_for_function('window.ready')
        await busy_receiver.locator('#quick-prep > summary').click()
        await busy_receiver.locator('#input-jd').fill('已有未保存内容')
        await sender.evaluate("""({origin, job}) => window.receiver.postMessage({type:'interview-sim:job',version:1,job}, origin)""", {'origin': base_url, 'job': job})
        await expect(busy_receiver.locator('#toast')).to_contain_text('未导入岗位')
        await expect(busy_receiver.locator('#job-editor')).not_to_be_visible()
        await expect(busy_receiver.locator('#input-jd')).to_have_value('已有未保存内容')
        assert mutations == [base_url + '/api/presets'], mutations
        assert not errors, errors
    finally:
        await context.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
