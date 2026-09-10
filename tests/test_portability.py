"""Cross-platform regressions; all paths and input are synthetic."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from backend import document_parser

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('skill_installer', ROOT / 'scripts/install_skill.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class PortableOCRTests(unittest.TestCase):
    def test_ocr_file_is_closed_before_external_reader_and_removed_afterwards(self):
        buffer = io.BytesIO()
        Image.new('RGB', (12, 12), 'white').save(buffer, format='PNG')
        opened = []
        original = tempfile.NamedTemporaryFile
        def track(*args, **kwargs):
            handle = original(*args, **kwargs)
            opened.append(handle)
            return handle
        paths = []
        def reader(path):
            self.assertTrue(all(handle.closed for handle in opened))
            paths.append(Path(path))
            self.assertEqual(Path(path).read_bytes(), buffer.getvalue())
            return '中文识别'
        with patch.object(document_parser.platform, 'system', return_value='Windows'), \
             patch.object(document_parser.shutil, 'which', return_value='tesseract'), \
             patch.object(document_parser.tempfile, 'NamedTemporaryFile', side_effect=track), \
             patch.object(document_parser, '_ocr_with_tesseract', side_effect=reader):
            self.assertEqual(document_parser.extract_document('测试.png', buffer.getvalue())['text'], '中文识别')
        self.assertTrue(paths)
        self.assertTrue(all(not path.exists() for path in paths))

    def test_tesseract_explicitly_decodes_utf8(self):
        with patch.object(document_parser.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '中文', '')) as run:
            self.assertEqual(document_parser._ocr_with_tesseract('image.png'), '中文')
        self.assertEqual(run.call_args.kwargs.get('encoding'), 'utf-8')


class PortableSkillTests(unittest.TestCase):
    def test_copy_install_runs_launcher_without_symlink_privileges(self):
        with tempfile.TemporaryDirectory(prefix='skill portable ') as directory:
            target = Path(directory) / 'skills' / 'interview-sim'
            with patch.object(Path, 'symlink_to', side_effect=PermissionError('no privilege')):
                installer.install_skill(target, mode='copy')
            marker = json.loads((target / 'interview-sim-location.json').read_text(encoding='utf-8'))
            self.assertEqual(Path(marker['project_root']), ROOT)
            result = subprocess.run([sys.executable, str(target/'scripts/launch.py'), '--help'], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('--no-open', result.stdout)

    def test_copy_install_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'interview-sim'
            target.mkdir()
            sentinel = target/'SKILL.md'
            sentinel.write_text('user-owned', encoding='utf-8')
            with self.assertRaises(SystemExit):
                installer.install_skill(target, mode='copy')
            self.assertEqual(sentinel.read_text(), 'user-owned')

    def test_auto_mode_uses_copy_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'interview-sim'
            with patch.object(installer.platform, 'system', return_value='Windows'), \
                 patch.object(Path, 'symlink_to', side_effect=PermissionError('no privilege')):
                installer.install_skill(target, mode='auto')
            self.assertTrue((target/'interview-sim-location.json').is_file())

    def test_copied_launcher_rejects_missing_project_without_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'interview-sim'
            installer.install_skill(target, mode='copy')
            (target/'interview-sim-location.json').write_text(json.dumps({'project_root':str(Path(directory)/'missing')}), encoding='utf-8')
            result = subprocess.run([sys.executable, str(target/'scripts/launch.py'), '--help'], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('完整项目', result.stderr)


if __name__ == '__main__':
    unittest.main()
