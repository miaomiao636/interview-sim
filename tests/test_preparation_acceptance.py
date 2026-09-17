"""The browser cannot bypass applicability or factual confirmation gates."""
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import config, preparation_store as prep
from backend.main import app


class SuggestionConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(config, 'DATA_DIR', Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        with TestClient(app) as client:
            self.preset = client.post('/api/presets', json={
                'name': '虚构岗位', 'target_role': '工程师',
                'jd': '使用 Python 开发工具。', 'resume': '编写 Python 脚本。',
            }).json()
        self.version = prep.create_version(self.preset['id'], 0, self.preset['resume'], '初稿')

    def suggestion(self, applicable=True):
        data = prep.read_preparation(self.preset['id'])
        identifier = uuid.uuid4().hex
        data['suggestions'][identifier] = {
            'id': identifier, 'source_version_id': self.version['current_version_id'],
            'target': 'Python', 'replacement': 'Python 自动化',
            'status': 'pending', 'applicable': applicable, 'needs_confirmation': True,
        }
        prep.write_preparation(self.preset['id'], data)
        return identifier

    def test_new_proposal_requires_truth_confirmation_without_mutation(self):
        identifier = self.suggestion()
        before = prep.preparation_path(self.preset['id']).read_bytes()
        with self.assertRaises(prep.PreparationConflict):
            prep.accept_suggestion(self.preset['id'], identifier, self.version['revision'])
        self.assertEqual(prep.preparation_path(self.preset['id']).read_bytes(), before)
        result = prep.accept_suggestion(self.preset['id'], identifier, self.version['revision'], truth_confirmed=True)
        self.assertIn('Python 自动化', result['version']['resume'])
        saved = prep.read_preparation(self.preset['id'])['suggestions'][identifier]
        self.assertTrue(saved['truth_confirmed'])
        self.assertTrue(saved['truth_confirmed_at'])

    def test_unsafe_proposal_cannot_be_accepted_even_when_confirmed(self):
        identifier = self.suggestion(applicable=False)
        before = prep.preparation_path(self.preset['id']).read_bytes()
        with self.assertRaises(prep.PreparationConflict):
            prep.accept_suggestion(self.preset['id'], identifier, self.version['revision'], truth_confirmed=True)
        self.assertEqual(prep.preparation_path(self.preset['id']).read_bytes(), before)
