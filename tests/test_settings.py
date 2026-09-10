import json
import os
import tempfile
import unittest
from tests.permission_checks import assert_storage_permissions
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend import config
from backend.main import app


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp_dir.name) / "local.json"
        self.runtime_values = {
            name: getattr(config, name)
            for name in (
                "XIAOMI_BASE_URL",
                "XIAOMI_API_KEY",
                "LLM_MODEL",
                "LLM_MODEL_PRO",
                "ASR_MODEL",
                "TTS_MODEL",
                "TTS_VOICE",
            )
        }

    def tearDown(self):
        for name, value in self.runtime_values.items():
            setattr(config, name, value)
        self.temp_dir.cleanup()

    def test_data_directory_is_private_before_first_save(self):
        assert_storage_permissions(self, config.DATA_DIR, 0o700)

    def test_secret_is_preserved_but_never_returned(self):
        with patch.object(config, "LOCAL_SETTINGS_PATH", self.settings_path):
            first = config.save_local_settings({
                "profile": {
                    "display_name": "小林",
                    "target_role": "AI 产品经理",
                    "coaching_goal": "练习案例深挖",
                },
                "ai": {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "top-secret-key",
                    "llm_model": "model-a",
                    "llm_model_pro": "model-b",
                    "asr_model": "asr-a",
                    "tts_model": "tts-a",
                },
                "defaults": {
                    "persona": "HR",
                    "difficulty": "进阶",
                    "voice": "茉莉",
                },
            })
            second = config.save_local_settings({
                "profile": first["profile"],
                "ai": {**first["ai"], "api_key": ""},
                "defaults": first["defaults"],
            })

            stored = json.loads(self.settings_path.read_text(encoding="utf-8"))

        self.assertTrue(first["ai"]["has_api_key"])
        self.assertTrue(second["ai"]["has_api_key"])
        self.assertNotIn("api_key", first["ai"])
        self.assertNotIn("top-secret-key", json.dumps(first, ensure_ascii=False))
        self.assertEqual(stored["ai"]["api_key"], "top-secret-key")
        assert_storage_permissions(self, self.settings_path, 0o600)

    def test_first_run_requires_key_and_target_role(self):
        with patch.object(config, "LOCAL_SETTINGS_PATH", self.settings_path), patch.object(
            config, "XIAOMI_API_KEY", ""
        ):
            public = config.get_public_settings()

        self.assertTrue(public["is_first_run"])
        self.assertFalse(public["ai"]["has_api_key"])

    def test_settings_api_accepts_secret_without_ever_reading_it_back(self):
        payload = {
            "profile": {"display_name": "", "target_role": "后端工程师", "coaching_goal": ""},
            "ai": {
                "base_url": "https://api.example.com/v1",
                "api_key": "api-secret-for-test",
                "llm_model": "model-a",
                "llm_model_pro": "model-b",
                "asr_model": "asr-a",
                "tts_model": "tts-a",
            },
            "defaults": {"persona": "HR", "difficulty": "标准", "voice": "茉莉"},
        }
        client = TestClient(app)
        with patch.object(config, "LOCAL_SETTINGS_PATH", self.settings_path):
            saved = client.put("/api/settings", json=payload)
            loaded = client.get("/api/settings")

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(loaded.status_code, 200)
        self.assertTrue(saved.json()["ai"]["has_api_key"])
        self.assertNotIn("api-secret-for-test", saved.text)
        self.assertNotIn("api-secret-for-test", loaded.text)

    def test_settings_api_rejects_unencrypted_remote_base_url(self):
        payload = {
            "profile": {"display_name": "", "target_role": "后端工程师", "coaching_goal": ""},
            "ai": {
                "base_url": "http://api.example.com/v1",
                "api_key": "",
                "llm_model": "model-a",
                "llm_model_pro": "model-b",
                "asr_model": "asr-a",
                "tts_model": "tts-a",
            },
            "defaults": {"persona": "HR", "difficulty": "标准", "voice": "茉莉"},
        }

        response = TestClient(app).put("/api/settings", json=payload)

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
