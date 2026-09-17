import json
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config, store
from backend.routers.review import ReviewRequest, review
from tests.test_report_v2 import feedback, turn


class ReviewFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_session_dir = config.SESSION_DIR
        config.SESSION_DIR = Path(self.temp_dir.name)

    async def asyncTearDown(self):
        config.SESSION_DIR = self.original_session_dir
        self.temp_dir.cleanup()

    async def test_review_persists_grounded_feedback_and_practice_plan(self):
        session = store.create_session("Python 后端 JD", "负责订单系统重构", "技术负责人", "标准")
        store.update_session(session["id"], {
            "blueprint": [{"id": 1, "question": "请介绍系统重构", "expect": ["行动", "结果"]}],
        })
        store.set_active_question(session["id"], "q-1", "请介绍系统重构", 1)
        store.record_answer(session["id"], "我负责订单系统重构。")

        grounded = feedback(turn(answer='我负责订单系统重构。'))
        grounded['evidence_quotes'] = ['负责订单系统重构']
        invalid = copy.deepcopy(grounded)
        invalid['evidence_quotes'].append('未出现的引用')
        model_responses = iter([
            json.dumps({'question_feedback': [invalid]}, ensure_ascii=False),
            json.dumps({'question_feedback': [grounded]}, ensure_ascii=False),
            json.dumps({
                "interview_tips": ["补充可验证结果"],
                "practice_plan": [{"question_id": "q-1", "focus": "结果", "reason": "证据不足"}],
            }, ensure_ascii=False),
        ])

        calls = []
        async def fake_chat_once(**_kwargs):
            calls.append(_kwargs)
            return next(model_responses)

        with patch("backend.routers.review.chat_once", fake_chat_once):
            result = await review(ReviewRequest(session_id=session["id"]))

        saved = store.get_session(session["id"])
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(len(calls), 3, 'Invalid quote must trigger correction before summary')
        self.assertNotIn('未出现的引用', calls[-1]['messages'][1]['content'])
        self.assertEqual(result['question_feedback'][0]["question"], "请介绍系统重构")
        self.assertEqual(result['question_feedback'][0]["answer"], "我负责订单系统重构。")
        self.assertEqual(result['question_feedback'][0]["evidence_quotes"], ["负责订单系统重构"])
        self.assertEqual(result['practice_plan'][0]["question_id"], "q-1")


if __name__ == "__main__":
    unittest.main()
