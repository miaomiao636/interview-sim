import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config, store
from backend.routers.review import ReviewRequest, review


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

        model_responses = iter([
            json.dumps({
                "question_feedback": [{
                    "question_id": "q-1",
                    "attempt": 1,
                    "score": 6,
                    "confidence": "high",
                    "evidence_quotes": ["负责订单系统重构", "未出现的引用"],
                    "covered_points": ["行动"],
                    "missed_points": ["结果"],
                    "coaching_tip": "补充可验证结果",
                }],
            }, ensure_ascii=False),
            json.dumps({
                "score": {"dimensions": {name:{"score":0,"comment":"待加强"} for name in ['岗位匹配度','经历说服力','专业深度','表达与结构','面试表现折算']}, "conclusion":"继续练习"},
                "polish_list": [],
                "interview_tips": ["补充可验证结果"],
                "practice_plan": [{"question_id": "q-1", "focus": "结果", "reason": "证据不足"}],
                "skill_map": [],
                "tailored_resume": "负责订单系统重构",
            }, ensure_ascii=False),
        ])

        async def fake_chat_once(**_kwargs):
            return next(model_responses)

        with patch("backend.routers.review.chat_once", fake_chat_once):
            result = await review(ReviewRequest(session_id=session["id"]))

        saved = store.get_session(session["id"])
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(result.question_feedback[0]["question"], "请介绍系统重构")
        self.assertEqual(result.question_feedback[0]["answer"], "我负责订单系统重构。")
        self.assertEqual(result.question_feedback[0]["evidence_quotes"], ["负责订单系统重构"])
        self.assertEqual(result.practice_plan[0]["question_id"], "q-1")


if __name__ == "__main__":
    unittest.main()
