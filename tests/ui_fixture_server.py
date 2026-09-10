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

from backend import config, xiaomi_client
from backend.routers import plan, chat, review, presets
from backend.main import app

config.save_local_settings({'profile':{'target_role':'测试岗位'},'ai':{**config.DEFAULTS['ai'],'api_key':'local-test-placeholder'},'defaults':config.DEFAULTS['defaults']})

async def fake_plan(**kwargs):
    return json.dumps({'jd_parsed':{'job_title':'测试岗位'},'gap_analysis':[], 'blueprint':[{'id':i,'dimension':d,'question':q,'expect':['行动','结果']} for i,d,q in [(1,'动机','为什么选择这个岗位？'),(2,'项目','请介绍一次项目经历。'),(3,'复盘','如何验证项目效果？')]]}, ensure_ascii=False)

async def fake_chat(*args, **kwargs):
    for text in ['请介绍', '你如何验证项目结果？']:
        await asyncio.sleep(0.1)
        yield text

async def fake_review(**kwargs):
    await asyncio.sleep(3)
    prompt = kwargs['messages'][1]['content']
    if '本批作答：' in prompt:
        turns = json.loads(prompt.split('本批作答：',1)[1])
        return json.dumps({'question_feedback':[{'question_id':t['question_id'],'attempt':t.get('attempt',1),'score':0 if t.get('status')=='unanswered' else 6,'evidence_quotes':[],'coaching_tip':'补充自己的行动与结果'} for t in turns]})
    return json.dumps({'score':{'dimensions':{name:{'score':5,'comment':'继续练习'} for name in review.SCORE_WEIGHTS},'conclusion':'继续练习'},'question_feedback':[],'polish_list':[],'interview_tips':['按真实经历练习 STAR。'],'practice_plan':[]})

async def fake_parse(**kwargs):
    return json.dumps({'company':'示例内容科技公司','target_role':'AI 应用工程师','city':'无锡','salary_min':7,'salary_max':16,'salary_months':13,'education':'本科','responsibilities':'开发 AI 内容工具','requirements':'掌握 Python','company_context':'为内容团队提供工具'}, ensure_ascii=False)

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
xiaomi_client.transcribe_audio_stream = fake_asr
xiaomi_client.tts_synthesize_stream = fake_tts

asyncio.run(presets.create_preset(presets.PresetInput(name='QA 测试岗位',target_role='测试岗位',company='示例公司',jd='负责产品需求分析与验证。',resume='参与了一个测试项目，负责需求访谈。')))

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('INTERVIEW_SIM_TEST_PORT', '8830')))
