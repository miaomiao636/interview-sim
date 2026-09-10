import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend import config, store
from backend.routers.chat import ChatRequest, chat


class ChatFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_session_dir = config.SESSION_DIR
        config.SESSION_DIR = Path(self.temp_dir.name)

    async def asyncTearDown(self):
        config.SESSION_DIR = self.original_session_dir
        self.temp_dir.cleanup()

    async def test_chat_uses_server_history_without_duplicating_current_answer(self):
        session = store.create_session("后端工程师", "五年 Python 经验", "技术负责人", "标准")
        store.update_session(
            session["id"],
            {
                "blueprint": [
                    {
                        "id": 1,
                        "dimension": "架构能力",
                        "question": "请讲一个系统设计案例。",
                        "expect": ["约束", "权衡"],
                    }
                ],
                "jd_parsed": {"job_title": "后端工程师"},
            },
        )
        store.set_active_question(session["id"], "q-1", "请讲一个系统设计案例。", 1)
        captured_messages = []

        async def fake_stream(messages, **_kwargs):
            captured_messages.extend(messages)
            yield "如果流量增长十倍，你会怎么调整？"

        request = ChatRequest(
            session_id=session["id"],
            asr_text="我先确认容量和一致性要求。",
            history=[{"role": "user", "content": "我先确认容量和一致性要求。"}],
        )

        with patch("backend.routers.chat.chat_stream", fake_stream):
            response = await chat(request)
            body = b""
            async for chunk in response.body_iterator:
                body += chunk.encode() if isinstance(chunk, str) else chunk

        self.assertIn("流量增长十倍", body.decode())
        contents = [message["content"] for message in captured_messages]
        self.assertEqual(contents.count("我先确认容量和一致性要求。"), 1)
        self.assertIn("请讲一个系统设计案例。", contents)


if __name__ == "__main__":
    unittest.main()
