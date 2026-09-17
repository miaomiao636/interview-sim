"""A selected version, not browser-authored text, becomes the session snapshot."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from backend import config, store, preparation_store as prep
from backend.main import app


class PreparationLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, value in [('DATA_DIR', self.root), ('SESSION_DIR', self.root / 'sessions')]:
            patcher = patch.object(config, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.preset = self.client.post('/api/presets', json={
            'name': '虚构岗位', 'target_role': '工具工程师', 'company': '虚构公司',
            'jd': '开发 Python 工具。', 'resume': '我编写 Python 脚本。',
            'company_context': '为虚构团队提供工具', 'coaching_goal': '说明个人贡献',
        }).json()
        view = prep.get_preparation_view(self.preset['id'])
        self.base_id = view['current_version_id']
        self.response = json.dumps({'jd_parsed': {'job_title': '工具工程师'}, 'gap_analysis': [],
            'blueprint': [{'id': 1, 'question': '请介绍你的工具项目。', 'dimension': '项目'}]})
        self.fake = AsyncMock(return_value=self.response)
        patcher = patch('backend.routers.plan.chat_once', self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def launch(self, **extra):
        return self.client.post('/api/plan', json={
            'jd': '浏览器提交的不同岗位', 'resume': '浏览器提交的不同简历',
            'company': '浏览器公司', 'target_role': '浏览器岗位',
            'persona': 'CEO', 'difficulty': '压力', 'interviewer_gender': '男性',
            'company_context': '浏览器背景', 'coaching_goal': '浏览器目标', 'voice': '白桦',
            'preset_id': self.preset['id'], 'resume_version_id': self.base_id, **extra,
        })

    def test_virtual_baseline_launch_uses_server_material_and_persists_exact_source(self):
        response = self.launch()
        self.assertEqual(response.status_code, 200, response.text)
        session = store.get_session(response.json()['session_id'])
        self.assertEqual(session['config']['resume'], self.preset['resume'])
        self.assertEqual(session['config']['jd'], self.preset['jd'])
        self.assertEqual(session['config']['company'], self.preset['company'])
        self.assertEqual(session['config']['target_role'], self.preset['target_role'])
        self.assertEqual(session['config']['preset_id'], self.preset['id'])
        self.assertEqual(session['config']['resume_version_id'], self.base_id)
        self.assertEqual(session['config']['persona'], 'CEO')
        self.assertEqual(session['config']['difficulty'], '压力')
        self.assertEqual(session['config']['interviewer_gender'], '男性')
        self.assertEqual(session['config']['voice'], '白桦')
        self.assertEqual(session['config']['company_context'], self.preset['company_context'])
        self.assertEqual(session['config']['coaching_goal'], self.preset['coaching_goal'])
        self.assertIsNone(session['preparation_snapshot']['resume_assessment'])
        self.assertEqual(response.json()['active_question'], session['active_question'])
        self.assertIn(self.base_id, prep.read_preparation(self.preset['id'])['versions'])

    def test_historical_version_launch_does_not_change_current_version_or_old_session(self):
        created = prep.create_version(self.preset['id'], 0, '新版本简历。', '新版', parent_id=self.base_id)
        response = self.launch()
        self.assertEqual(response.status_code, 200)
        session_id = response.json()['session_id']
        saved_before = store.get_session(session_id)
        self.assertEqual(saved_before['config']['resume'], self.preset['resume'])
        self.assertEqual(prep.get_preparation_view(self.preset['id'])['current_version_id'], created['current_version_id'])
        self.client.put(f"/api/presets/{self.preset['id']}", json={**self.preset, 'jd': '更新岗位。'})
        self.assertEqual(store.get_session(session_id), saved_before)

    def test_partial_identity_is_rejected_before_model_or_session_creation(self):
        for extra in ({'preset_id': ''}, {'resume_version_id': ''}):
            with self.subTest(extra=extra):
                self.assertEqual(self.launch(**extra).status_code, 422)
        self.fake.assert_not_awaited()
        self.assertFalse(list((self.root / 'sessions').glob('*.json')))

    def test_foreign_version_is_rejected_before_model_or_session_creation(self):
        other = self.client.post('/api/presets', json={**self.preset, 'name': '另一个岗位'}).json()
        foreign = prep.get_preparation_view(other['id'])['current_version_id']
        self.assertEqual(self.launch(resume_version_id=foreign).status_code, 404)
        self.fake.assert_not_awaited()
        self.assertFalse(list((self.root / 'sessions').glob('*.json')))

    def test_direct_manual_launch_remains_available_without_preparation(self):
        result = self.client.post('/api/plan', json={'jd': '直接填写的 JD', 'resume': '真实手填简历'})
        self.assertEqual(result.status_code, 200)
        session = store.get_session(result.json()['session_id'])
        self.assertEqual(session['config']['resume'], '真实手填简历')
        self.assertNotIn('preparation_snapshot', session)
        self.assertEqual(result.json()['preset_id'], '')

    def test_invalid_identifiers_do_not_access_sessions_or_call_model(self):
        for extra in ({'preset_id': '../other'}, {'resume_version_id': '../other'}):
            with self.subTest(extra=extra):
                self.assertEqual(self.launch(**extra).status_code, 422)
        self.fake.assert_not_awaited()
        self.assertFalse(list((self.root / 'sessions').glob('*.json')))

    def test_historical_resume_can_use_updated_job_without_inheriting_old_assessment(self):
        prep.create_version(self.preset['id'], 0, '新版本简历。', '新版', parent_id=self.base_id)
        self.client.put(f"/api/presets/{self.preset['id']}", json={**self.preset, 'jd': '更新的真实岗位。'})
        result = self.launch()
        self.assertEqual(result.status_code, 200)
        session = store.get_session(result.json()['session_id'])
        self.assertEqual(session['config']['jd'], '更新的真实岗位。')
        self.assertEqual(session['config']['resume'], self.preset['resume'])
        self.assertIsNone(session['preparation_snapshot']['resume_assessment'])
