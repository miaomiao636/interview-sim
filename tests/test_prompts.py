import unittest

from backend import prompts


class PromptContractTests(unittest.TestCase):
    def test_review_prompt_requires_grounded_question_feedback_and_practice_plan(self):
        prompt = prompts.build_review_prompt(
            resume="简历",
            jd="JD",
            transcript="面试官：问题\n候选人：回答",
            minutes="{}",
            turns='[{"question_id":"q-1","question":"问题","answer":"回答"}]',
            blueprint='[{"id":1,"expect":["要点"]}]',
        )

        self.assertIn('"question_feedback"', prompt)
        self.assertIn('"evidence_quotes"', prompt)
        self.assertIn('"practice_plan"', prompt)
        self.assertIn('"tailored_resume"', prompt)

    def test_interviewer_prompt_uses_job_title_instead_of_persona_as_role(self):
        prompt = prompts.build_interviewer_prompt(
            jd="JD",
            resume="简历",
            persona="HR",
            difficulty="标准",
            persona_name="面试官",
            style="",
            focus_areas="",
            job_title="后端工程师",
        )

        self.assertIn("应聘【后端工程师】", prompt)
        self.assertNotIn("应聘【HR】岗位", prompt)


if __name__ == "__main__":
    unittest.main()
