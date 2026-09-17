"""Normal service shutdown explicitly invalidates local preparation work."""
import unittest
from unittest.mock import AsyncMock, patch

from backend.main import app


class TaskLifespanTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_drains_preparation_tasks(self):
        with patch('backend.preparation_tasks.shutdown_tasks', new_callable=AsyncMock) as stop:
            async with app.router.lifespan_context(app):
                stop.assert_not_awaited()
            stop.assert_awaited_once_with()

    async def test_shutdown_drains_question_and_report_jobs(self):
        with patch('backend.routers.chat.shutdown_question_jobs', new_callable=AsyncMock) as questions, \
                patch('backend.routers.review.shutdown_report_jobs', new_callable=AsyncMock) as reports:
            async with app.router.lifespan_context(app):
                questions.assert_not_awaited()
                reports.assert_not_awaited()
            questions.assert_awaited_once_with()
            reports.assert_awaited_once_with()
