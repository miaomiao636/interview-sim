import unittest
from pathlib import Path


ROOT = Path(__file__).parent.parent


class FrontendContractTests(unittest.TestCase):
    def test_markup_uses_bound_labels_and_no_inline_click_handlers(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("onclick=", html)
        self.assertIn('for="input-jd"', html)
        self.assertIn('for="input-resume"', html)
        self.assertIn('aria-live="polite"', html)

    def test_client_supports_grounded_feedback_retry_and_streaming_audio(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("question_feedback", script)
        self.assertIn("/retry", script)
        self.assertIn("/api/tts/stream", script)
        self.assertIn("/api/transcribe/stream", (ROOT / "frontend" / "voice.js").read_text(encoding="utf-8"))
        self.assertNotIn("history: state.history", script)

    def test_voice_data_only_uses_configured_model_provider(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("SpeechRecognition", script)
        self.assertNotIn("webkitSpeechRecognition", script)

    def test_start_button_has_a_stable_label_target(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="start-label"', html)
        self.assertNotIn("button.firstChild.textContent", script)

    def test_sidebar_exposes_service_readiness(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="service-status-title"', html)
        self.assertIn('id="service-status-detail"', html)
        self.assertIn("/api/health", script)
        self.assertIn("serviceReady: false", script)
        self.assertIn("busy || !state.serviceReady", script)

    def test_interrupted_session_can_recover_to_report_flow(self):
        script = (ROOT / "frontend" / "app.js").read_text(encoding="utf-8")

        self.assertIn("session.turns?.length", script)
        self.assertIn("当前没有待回答的问题", script)
        self.assertIn("选择仅保存记录或生成报告", script)

    def test_upload_labels_do_not_claim_text_only_support(self):
        html = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        self.assertNotIn("导入 TXT / MD", html)
        self.assertIn("图片 / Word / PDF", html)


if __name__ == "__main__":
    unittest.main()
