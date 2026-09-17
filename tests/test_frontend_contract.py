import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).parent.parent


class FrontendContractTests(unittest.TestCase):
    def test_report_exports_are_disabled_in_initial_markup(self):
        targets = {'btn-export-md', 'btn-export-json', 'btn-print'}
        disabled = {}

        class ReportButtonParser(HTMLParser):
            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                button_id = attributes.get('id')
                if tag == 'button' and button_id in targets:
                    disabled[button_id] = 'disabled' in attributes

        parser = ReportButtonParser()
        parser.feed((ROOT / 'frontend/index.html').read_text(encoding='utf-8'))
        self.assertEqual(set(disabled), targets)
        for button_id in sorted(targets):
            with self.subTest(button=button_id):
                self.assertTrue(disabled[button_id], 'Exports must not become usable before a real report is loaded')

    def test_live_interview_has_no_teaching_dom_or_renderers(self):
        html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
        live = html.split('<section id="view-interview"', 1)[1].split('<section id="view-report"', 1)[0]
        for forbidden in ('current-focus', 'focus-detail', 'blueprint-list', '回答建议', '当前训练重点', 'micro-tips'):
            self.assertNotIn(forbidden, live)
        for filename in ('app.js', 'workspace.js'):
            script = (ROOT / 'frontend' / filename).read_text(encoding='utf-8')
            self.assertNotIn('renderBlueprint(', script)
            self.assertNotIn('updateCurrentFocus(', script)
        self.assertIn('id="btn-recover-question"', live)

    def test_answer_and_skip_send_attempt_and_stable_operation_identity(self):
        script = (ROOT / 'frontend/app.js').read_text(encoding='utf-8')
        workspace = (ROOT / 'frontend/workspace.js').read_text(encoding='utf-8')
        self.assertIn('operation_id', script)
        self.assertIn('attempt:', script)
        self.assertIn('operation_id', workspace)
        self.assertIn('attempt:', workspace)
        self.assertIn('data.active_question', script)
        self.assertNotIn("state.activeQuestionId = 'q-1'", script)

    def test_report_callbacks_track_epoch_and_job_identity(self):
        script = (ROOT / 'frontend/reports.js').read_text(encoding='utf-8')
        self.assertIn('reportEpoch', script)
        self.assertIn('generation', script)
        self.assertIn('active_question', script)

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
