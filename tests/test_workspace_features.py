import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from backend import config, store
from backend.main import app
from backend.routers.review import _normalize_review, SCORE_WEIGHTS


class WorkspaceFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [patch.object(config, 'DATA_DIR', self.root), patch.object(config, 'SESSION_DIR', self.root / 'sessions'), patch.object(config, 'LOCAL_SETTINGS_PATH', self.root / 'config.json')]
        self.runtime_values = {key: getattr(config, key) for key in ('XIAOMI_BASE_URL','XIAOMI_API_KEY','LLM_MODEL','LLM_MODEL_PRO','ASR_MODEL','TTS_MODEL','TTS_VOICE')}
        for item in self.patches: item.start()
        self.client = TestClient(app)

    def tearDown(self):
        for item in self.patches: item.stop()
        for key, value in self.runtime_values.items(): setattr(config, key, value)
        self.temp.cleanup()

    def session(self):
        session = store.create_session('JD', '真实简历', 'HR', '标准')
        store.update_session(session['id'], {'blueprint': [{'id':1,'question':'动机？'}, {'id':2,'question':'项目？'}]})
        store.set_active_question(session['id'], 'q-1', '动机？', 1)
        return session['id']

    def test_skip_persists_zero_evidence_and_moves_exactly_once(self):
        sid = self.session()
        result = self.client.post(f'/api/sessions/{sid}/skip', json={'question_id':'q-1','reason':'暂时没有思路'})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['active_question']['question_id'], 'q-2')
        self.assertEqual(result.json()['turn']['status'], 'unanswered')
        self.assertEqual(result.json()['turn']['answer'], '')
        duplicate = self.client.post(f'/api/sessions/{sid}/skip', json={'question_id':'q-1'})
        self.assertEqual(duplicate.status_code, 409)
        last = self.client.post(f'/api/sessions/{sid}/skip', json={'question_id':'q-2'})
        self.assertTrue(last.json()['finished'])
        self.assertEqual(len(store.get_session(sid)['turns']), 2)

    def test_skipped_questions_cannot_gain_fabricated_evidence_or_full_score(self):
        sid = self.session()
        store.skip_question(sid, 'q-1')
        store.record_answer(sid, '我做了项目。')
        raw = {'score': {'dimensions': {k: {'score':v} for k,v in SCORE_WEIGHTS.items()}}, 'question_feedback': [{'question_id':'q-1','answer':'虚构回答','score':10,'evidence_quotes':['虚构回答']} ]}
        report = _normalize_review(raw, '', store.get_session(sid)['turns'])
        item = report['question_feedback'][0]
        self.assertEqual(item['answer'], '')
        self.assertEqual(item['score'], 0)
        self.assertEqual(item['evidence_quotes'], [])
        self.assertEqual(report['score']['total'], 50)
        self.assertIn('未说明', item['reason_analysis'])

    def test_multiple_presets_are_independent_and_can_be_updated(self):
        payload = {'name':'岗位 A','target_role':'后端','jd':'JD A','resume':'简历 A'}
        a = self.client.post('/api/presets', json=payload).json()
        b = self.client.post('/api/presets', json={**payload,'name':'岗位 B','jd':'JD B'}).json()
        self.client.put(f'/api/presets/{a["id"]}', json={**payload,'jd':'JD updated'})
        items = self.client.get('/api/presets').json()
        self.assertEqual(len(items), 2)
        self.assertEqual(next(x for x in items if x['id']==b['id'])['jd'], 'JD B')
        self.assertEqual((self.root/'presets.json').stat().st_mode & 0o777, 0o600)

    def test_connections_preserve_legacy_settings_and_hide_every_key(self):
        payload = {'profile': {'target_role':'后端'}, 'ai': {**config.DEFAULTS['ai'], 'api_key':'test-placeholder'}, 'defaults':config.DEFAULTS['defaults'], 'connections': {'analysis': {'inherit':False,'base_url':'https://analysis.example/v1','api_key':'analysis-placeholder','protocol':'openai'}}}
        response = self.client.put('/api/settings', json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('analysis-placeholder', response.text)
        self.assertEqual(config.get_connection('analysis')['base_url'], 'https://analysis.example/v1')
        self.assertNotEqual(config.get_connection('analysis')['base_url'], config.get_connection('asr')['base_url'])
        payload['connections']['analysis']['api_key'] = ''
        response = self.client.put('/api/settings', json=payload)
        self.assertTrue(response.json()['connections']['analysis']['has_api_key'])
        payload['connections']['analysis']['base_url'] = 'https://other.example/v1'
        self.assertEqual(self.client.put('/api/settings', json=payload).status_code, 422)

    def test_report_estimate_labels_unsampled_default(self):
        result = self.client.get('/api/review/estimate').json()
        self.assertEqual(result['sample_count'], 0)
        self.assertGreater(result['high_seconds'], result['low_seconds'])
