import os
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLauncherTests(unittest.TestCase):
    def test_isolation_precedes_imports_and_propagates_failure(self):
        from tests.run_tests import main
        original = dict(os.environ)
        called = []
        def run(command, *, cwd, env):
            called.append((command, cwd, env))
            self.assertNotEqual(env['INTERVIEW_SIM_HOME'], original.get('INTERVIEW_SIM_HOME'))
            self.assertEqual(Path(env['SESSION_DIR']).parent, Path(env['INTERVIEW_SIM_HOME']))
            self.assertTrue(Path(env['INTERVIEW_SIM_HOME']).is_dir())
            self.assertEqual(env['XIAOMI_API_KEY'], 'synthetic-test-placeholder')
            self.assertEqual(env['XIAOMI_BASE_URL'], 'http://127.0.0.1:9/v1')
            self.assertEqual(command[1:], ['-m', 'unittest', 'discover', '-s', 'tests', '-v'])
            return type('Result', (), {'returncode': 3})()
        with patch('tests.run_tests.subprocess.run', side_effect=run):
            self.assertEqual(main(), 3)
        self.assertEqual(dict(os.environ), original)
        self.assertFalse(Path(called[0][2]['INTERVIEW_SIM_HOME']).exists())
