"""Manual browser QA with synthetic local data and stub model calls only.

Run: python3 -m tests.ui_fixture_server (http://127.0.0.1:8830).
No existing configuration, API credentials or sessions are used.
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

temporary = tempfile.TemporaryDirectory(prefix='interview-sim-ui-')
os.environ['INTERVIEW_SIM_HOME'] = temporary.name
os.environ['SESSION_DIR'] = str(Path(temporary.name) / 'sessions')
os.environ['XIAOMI_API_KEY'] = 'local-test-placeholder'
os.environ['XIAOMI_BASE_URL'] = 'https://unused.example/v1'

from backend import config, xiaomi_client, preparation_tasks
from backend.routers import plan, chat, review, presets
from backend.main import app

config.save_local_settings({'profile':{'target_role':'测试岗位'},'ai':{**config.DEFAULTS['ai'],'api_key':'local-test-placeholder'},'defaults':config.DEFAULTS['defaults']})

async def fake_plan(**kwargs):
    return json.dumps({'jd_parsed':{'job_title':'测试岗位'},'gap_analysis':[], 'blueprint':[{'id':i,'dimension':d,'question':q,'expect':['行动','结果']} for i,d,q in [(1,'动机','为什么选择这个岗位？'),(2,'项目','请介绍一次项目经历。'),(3,'复盘','如何验证项目效果？')]]}, ensure_ascii=False)

_failed_chat_inputs = set()


async def fake_chat(*args, **kwargs):
    messages = args[0] if args else kwargs.get('messages', [])
    answer = next((item.get('content', '') for item in reversed(messages) if item.get('role') == 'user'), '')
    if '测试延迟下一题' in answer:
        await asyncio.sleep(2)
    if '测试触发下一题失败' in answer and answer not in _failed_chat_inputs:
        _failed_chat_inputs.add(answer)
        raise RuntimeError('合成下一题连接失败')
    for text in ['请介绍', '你如何验证项目结果？']:
        await asyncio.sleep(0.1)
        yield text

async def fake_review(**kwargs):
    await asyncio.sleep(3)
    prompt = kwargs['messages'][1]['content']
    if '本批作答：' in prompt:
        from tests.test_report_v2 import feedback
        turns = json.loads(prompt.split('本批作答：',1)[1])
        return json.dumps({'question_feedback': [feedback(t, 8 if t.get('attempt', 1) > 1 else 6) for t in turns]}, ensure_ascii=False)
    return json.dumps({'interview_tips':['按真实经历练习 STAR。'],'practice_plan':[]}, ensure_ascii=False)

async def fake_parse(**kwargs):
    return json.dumps({'company':'示例内容科技公司','target_role':'AI 应用工程师','city':'无锡','salary_min':7,'salary_max':16,'salary_months':13,'education':'本科','responsibilities':'开发 AI 内容工具','requirements':'掌握 Python','company_context':'为内容团队提供工具'}, ensure_ascii=False)


async def fake_preparation(**kwargs):
    await asyncio.sleep(0.2)
    payload = json.loads(kwargs['messages'][1]['content'])
    kind = payload['kind']
    if kind == 'session_materials':
        return json.dumps({'facts': [{'question_id': t['question_id'], 'attempt': t.get('attempt', 1),
            'quote': t['answer'][:240], 'text': t['answer'][:240]} for t in payload['turns'][:12]]}, ensure_ascii=False)
    resume = payload['resume_version']['resume']
    if kind == 'resume':
        edits = [('参与了一个测试项目', '在测试项目中参与协作'), ('负责需求访谈', '承担需求访谈工作')]
        data = {
            'assessment': {'dimensions': {name: {'score': 7, 'comment': '依据提供的文字材料评估'}
                for name in ('clarity', 'structure', 'relevance', 'evidence')}},
            'suggestions': [{'target': target, 'replacement': replacement, 'reason': '明确个人行动'}
                for target, replacement in edits if resume.count(target) == 1],
            'missing_information': ['补充可核实的项目结果'],
            'advice': [{'category': 'evidence', 'priority': 'high', 'title': '补充验证方式与交付物',
                'problem': '材料说明了个人行动，但还缺少如何验证交付的说明。',
                'action': '先说明真实交付物、验收方式及本人负责的部分；没有数据时不要编造效率提升。',
                'questions': ['访谈结论由谁确认？有哪些可展示的交付物？'],
                'evidence': [{'source_id': 'resume', 'quote': resume}]}],
        }
    elif kind == 'recruitment':
        data = {'requirements': [{
            'requirement': '岗位所述职责', 'priority': 'must', 'status': 'needs_verification',
            'source_quote': payload['job']['jd'],
            'evidence': [{'source_id': 'resume', 'quote': resume}],
            'note': '已有简历文字，实际能力仍需面试验证',
        }], 'missing_information': []}
    else:
        data = {'materials': [{
            'title': '项目经历提纲', 'situation': '', 'task': '', 'action': resume, 'result': '',
            'evidence': [{'source_id': 'resume', 'quote': resume}],
        }], 'self_introduction': resume, 'missing_information': ['补充可核实的结果']}
    return json.dumps(data, ensure_ascii=False)

async def fake_asr(*args, **kwargs):
    for text in ['这是', '测试转写。']:
        await asyncio.sleep(0.1)
        yield text

async def fake_tts(*args, **kwargs):
    yield bytes(24000)

plan.chat_once = fake_plan
chat.chat_stream = fake_chat
review.chat_once = fake_review
presets.chat_once = fake_parse
preparation_tasks.chat_once = fake_preparation
xiaomi_client.transcribe_audio_stream = fake_asr
xiaomi_client.tts_synthesize_stream = fake_tts

asyncio.run(presets.create_preset(presets.PresetInput(name='QA 测试岗位',target_role='测试岗位',company='示例公司',jd='负责产品需求分析与验证。',resume='参与了一个测试项目，负责需求访谈。')))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('INTERVIEW_SIM_TEST_PORT', '8830')))
