import unittest
from pathlib import Path

from backend import document_parser


ROOT = Path(__file__).parent.parent


class LocalConsoleContractTests(unittest.TestCase):
    def test_project_exposes_installable_cli_and_skill(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        skill = (ROOT / ".agents" / "skills" / "interview-sim" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn('interview-sim = "backend.cli:main"', pyproject)
        self.assertIn("interview-sim web", skill)
        self.assertIn("http://127.0.0.1:", skill)
        self.assertNotIn("frontend/index.html", skill)
        self.assertTrue((ROOT / ".agents" / "skills" / "interview-sim" / "agents" / "openai.yaml").exists())

    def test_frontend_has_first_run_settings_and_document_import(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('data-view="settings"', html)
        self.assertIn('id="settings-form"', html)
        self.assertIn(".docx", html)
        self.assertIn(".pdf", html)
        self.assertIn("image/png", html)
        self.assertIn("/api/documents/extract", script)
        self.assertIn("/api/settings", script)
        self.assertNotIn("请先在 config/.env 中配置", script)

    def test_macos_ocr_script_is_shipped_as_backend_package_data(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        script = Path(document_parser.__file__).resolve().parent / "ocr_image.swift"

        self.assertTrue(script.is_file())
        self.assertIn('backend = ["*.swift"]', pyproject)


if __name__ == "__main__":
    unittest.main()
