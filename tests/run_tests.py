"""Run backend tests without importing application configuration in the launcher.

Same command on Windows, macOS and Linux: python -m tests.run_tests.
Synthetic settings are injected before the subprocess imports any application.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='interview-sim-tests-') as folder:
        env = {**os.environ, 'INTERVIEW_SIM_HOME': folder, 'SESSION_DIR': str(Path(folder) / 'sessions'),
               'XIAOMI_API_KEY': 'synthetic-test-placeholder', 'XIAOMI_BASE_URL': 'http://127.0.0.1:9/v1',
               'PYTHONUTF8': '1'}
        return subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=root, env=env).returncode


if __name__ == '__main__':
    raise SystemExit(main())
