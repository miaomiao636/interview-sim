"""One-command isolated browser acceptance on Windows, macOS and Linux."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {**os.environ, 'INTERVIEW_SIM_TEST_PORT':str(port), 'PYTHONUTF8':'1'}
    server = subprocess.Popen([sys.executable, '-m', 'tests.ui_fixture_server'], cwd=root, env=env)
    try:
        deadline = time.monotonic() + 30
        while True:
            if server.poll() is not None:
                raise RuntimeError('Test server exited before becoming ready')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/health', timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError('Test server startup timed out')
            time.sleep(0.2)
        return subprocess.run([sys.executable, str(root/'tests/browser_flow.py')], cwd=root, env=env, timeout=180).returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == '__main__':
    raise SystemExit(main())
