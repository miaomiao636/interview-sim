import unittest

from backend.routers.review import _normalize_review


class ReviewNormalizationTests(unittest.TestCase):
    def test_total_is_recomputed_and_untraceable_evidence_is_removed(self):
        review = {
            "score": {
                "total": 99,
                "dimensions": {
                    "岗位匹配度": {"score": 24, "max": 30, "comment": "匹配"},
                    "经历说服力": {"score": 20, "max": 25, "comment": "清晰"},
                    "专业深度": {"score": 16, "max": 20, "comment": "扎实"},
                    "表达与结构": {"score": 12, "max": 15, "comment": "完整"},
                    "面试表现折算": {"score": 8, "max": 10, "comment": "稳定"},
                },
            },
            "question_feedback": [
                {
                    "question_id": "q-1",
                    "question": "请介绍项目",
                    "answer": "我负责订单系统重构。",
                    "score": 8,
                    "max_score": 10,
                    "confidence": "high",
                    "evidence_quotes": ["负责订单系统重构", "不存在的原句"],
                    "covered_points": ["职责"],
                    "missed_points": ["结果"],
                }
            ],
        }

        normalized = _normalize_review(review, "我负责订单系统重构。", [])

        self.assertEqual(normalized["score"]["total"], 80)
        self.assertEqual(
            normalized["question_feedback"][0]["evidence_quotes"],
            ["负责订单系统重构"],
        )

    def test_new_numeric_claim_in_polish_example_is_flagged(self):
        review = {
            "score": {"dimensions": {}},
            "polish_list": [
                {
                    "original": "优化接口性能",
                    "suggestion": "补充真实结果",
                    "example": "将接口延迟降低 60%",
                }
            ],
        }

        normalized = _normalize_review(review, "负责优化接口性能", [])
        item = normalized["polish_list"][0]

        self.assertFalse(item["_validated"])
        self.assertIn("60%", item["_unverified_claims"])


if __name__ == "__main__":
    unittest.main()
