import tempfile
import unittest
import os
from pathlib import Path

from backend import config, store


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_session_dir = config.SESSION_DIR
        config.SESSION_DIR = Path(self.temp_dir.name)

    def tearDown(self):
        config.SESSION_DIR = self.original_session_dir
        self.temp_dir.cleanup()

    def test_answer_is_linked_to_the_visible_question_once(self):
        session = store.create_session("后端工程师 JD", "候选人简历", "技术负责人", "标准")
        store.set_active_question(
            session["id"],
            question_id="q-1",
            question="请介绍一个你主导的系统。",
            blueprint_id=1,
        )

        turn = store.record_answer(session["id"], "我负责了订单系统重构。")
        saved = store.get_session(session["id"])

        self.assertEqual(turn["question"], "请介绍一个你主导的系统。")
        self.assertEqual(turn["answer"], "我负责了订单系统重构。")
        self.assertEqual(len(saved["turns"]), 1)
        self.assertEqual(
            [item["role"] for item in saved["transcript"]],
            ["interviewer", "candidate"],
        )
        self.assertEqual(
            [item["content"] for item in saved["transcript"]],
            ["请介绍一个你主导的系统。", "我负责了订单系统重构。"],
        )

    def test_retry_preserves_question_and_increments_attempt(self):
        session = store.create_session("JD", "简历", "HR", "标准")
        store.set_active_question(session["id"], "q-1", "为什么选择我们？", 1)
        store.record_answer(session["id"], "第一次回答")

        retry = store.prepare_retry(session["id"], "q-1")

        self.assertEqual(retry["question_id"], "q-1")
        self.assertEqual(retry["question"], "为什么选择我们？")
        self.assertEqual(retry["attempt"], 2)
        self.assertTrue(retry["is_retry"])

    def test_session_summary_includes_job_title_and_completed_turn_count(self):
        session = store.create_session("JD", "简历", "HR", "标准")
        store.update_session(session["id"], {
            "jd_parsed": {"job_title": "高级后端工程师"},
            "blueprint": [],
        })
        store.set_active_question(session["id"], "q-1", "请做自我介绍。", 1)
        store.record_answer(session["id"], "我有五年后端经验。")

        summary = store.list_sessions()[0]

        self.assertEqual(summary["job_title"], "高级后端工程师")
        self.assertEqual(summary["turn_count"], 1)

    def test_session_file_is_private_to_current_user(self):
        session = store.create_session("jd", "resume", "HR", "标准")
        path = config.SESSION_DIR / f"{session['id']}.json"

        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_session_list_hides_abandoned_records_without_a_blueprint(self):
        abandoned = store.create_session("JD", "简历", "HR", "标准")

        session_ids = [item["id"] for item in store.list_sessions()]

        self.assertNotIn(abandoned["id"], session_ids)


if __name__ == "__main__":
    unittest.main()
