"""Synthetic, explicit post-interview extraction: no report prose becomes fact."""
import asyncio
import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import httpx

from backend import config, store as sessions, preparation_store as store, preparation_tasks as tasks
from backend.routers import presets, review
from backend.main import app


class SessionMaterialTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = {"base_url": "https://models.example/v1", "api_key": "synthetic-placeholder", "protocol": "openai"}
        self.patches = [patch.object(config, "DATA_DIR", self.root), patch.object(config, "SESSION_DIR", self.root / "sessions"),
                        patch.object(config, "get_connection", side_effect=lambda _: dict(self.connection)),
                        patch.object(config, "LLM_MODEL_PRO", "test-analysis")]
        for item in self.patches:
            item.start()
        self.calls = []
        async def fake(**kwargs):
            self.calls.append(kwargs)
            return json.dumps(self.output(), ensure_ascii=False)
        self.fake_patch = patch.object(tasks, "chat_once", side_effect=fake)
        self.fake_patch.start()
        self.job = await presets.create_preset(presets.PresetInput(name="测试岗位", company="示例公司", target_role="产品助理",
                                                                jd="负责产品需求分析与验证。", resume="参与了一个测试项目，负责需求访谈。"))
        self.version = store.get_preparation_view(self.job["id"])["current_version_id"]
        tasks.resolve_launch(self.job["id"], self.version)
        self.session = sessions.create_session(self.job["jd"], self.job["resume"], "HR", "标准")
        cfg = {**self.session["config"], "preset_id": self.job["id"], "resume_version_id": self.version}
        sessions.update_session(self.session["id"], {"config": cfg})
        sessions.set_active_question(self.session["id"], "q-1", "介绍一次项目经历。", 1)
        sessions.record_answer(self.session["id"], "我负责需求访谈，并整理了问题清单。")
        sessions.end_session(self.session["id"])

    async def asyncTearDown(self):
        await tasks.shutdown_tasks()
        self.fake_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def output(self):
        return {"facts": [{"question_id": "q-1", "attempt": 1, "quote": "我负责需求访谈，并整理了问题清单。", "text": "我负责需求访谈"}]}

    def start(self, **kwargs):
        return tasks.create_session_material_task(self.job["id"], session_id=kwargs.get("session_id", self.session["id"]),
                                                  revision=kwargs.get("revision", store.get_preparation_view(self.job["id"])["revision"]))

    async def finish(self, task):
        for _ in range(100):
            result = tasks.get_task(self.job["id"], task["id"])
            if result["task"]["status"] != "running":
                return result
            await asyncio.sleep(0.001)
        self.fail("stub extraction did not finish")

    async def test_launch_fingerprint_uses_only_exact_material_fields_and_history(self):
        keys = ("preset_id", "resume_version_id", "jd", "resume", "company", "target_role", "company_context", "coaching_goal")
        cfg = {"preset_id": self.job["id"], "resume_version_id": self.version, **{k: self.job.get(k, "") for k in keys[2:]}}
        expected = hashlib.sha256(json.dumps({k: cfg.get(k, "") for k in keys}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(store.launch_material_fingerprint(cfg), expected)
        self.assertEqual(store.launch_material_fingerprint({**cfg, "voice": "changed", "persona": "CTO", "timestamp": "other"}), expected)
        launch = tasks.resolve_launch(self.job["id"], self.version)
        self.assertEqual(launch["preparation_snapshot"]["material_fingerprint"], expected)
        store.create_version(self.job["id"], revision=0, name="新版本", resume="新简历", parent_id=self.version)
        self.assertEqual(tasks.resolve_launch(self.job["id"], self.version)["preparation_snapshot"]["material_fingerprint"], expected)
        self.assertNotEqual(store.launch_material_fingerprint({**cfg, "jd": "改变岗位原文"}), expected)

    async def test_explicit_task_uses_only_original_answers_exact_source_version_and_reuses(self):
        # Neither report ideals nor a newly selected resume are extraction input.
        sessions.update_session(self.session["id"], {"review": {"ideal_answer": "报告理想答案不是经历"}})
        store.create_version(self.job["id"], revision=0, name="另一个版本", resume="不可作为本次素材来源", parent_id=self.version)
        self.assertEqual(self.calls, [])
        first = self.start()["task"]
        self.assertEqual(first["kind"], "session_materials")
        self.assertEqual(first["session_id"], self.session["id"])
        self.assertEqual(self.start()["task"]["id"], first["id"])
        done = await self.finish(first)
        self.assertEqual(done["task"]["status"], "completed")
        self.assertFalse(done["task"]["stale"])
        self.assertEqual(done["task"]["result"]["created_count"], 1)
        fact = done["task"]["result"]["facts"][0]
        self.assertEqual(fact["status"], "pending")
        self.assertEqual(fact["source_version_id"], self.version)
        self.assertEqual(fact["source_quote"], self.output()["facts"][0]["quote"])
        self.assertEqual(fact["source"], {"kind": "session", "session_id": self.session["id"], "question_id": "q-1", "attempt": 1, "resume_version_id": self.version})
        self.assertEqual(store.get_preparation_view(self.job["id"])["evidence_facts"], [])
        before = store.preparation_path(self.job["id"]).read_bytes()
        self.assertEqual(self.start()["task"]["id"], first["id"])
        self.assertEqual(store.preparation_path(self.job["id"]).read_bytes(), before)
        self.assertEqual(len(self.calls), 1)
        payload = json.loads(self.calls[0]["messages"][1]["content"])
        self.assertEqual(payload["resume_version_id"], self.version)
        self.assertEqual(payload["turns"][0]["answer"], sessions.get_session(self.session["id"])["turns"][0]["answer"])
        self.assertNotIn("报告理想答案", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("不可作为本次", json.dumps(payload, ensure_ascii=False))
        self.assertNotIn("synthetic-placeholder", store.preparation_path(self.job["id"]).read_text())

    async def test_http_requires_revision_strict_identifiers_and_excludes_injected_material(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            url = f'/api/preparation/{self.job["id"]}/session-materials'
            for body in ({"session_id": self.session["id"]}, {"session_id": "../../private", "revision": 0},
                         {"session_id": self.session["id"], "revision": "0"}, {"session_id": self.session["id"], "revision": 0, "answer": "inject"}):
                with patch.object(sessions, "get_session") as read:
                    self.assertEqual((await client.post(url, json=body)).status_code, 422)
                read.assert_not_called()
            response = await client.post(url, json={"session_id": self.session["id"], "revision": 0})
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["task"]["session_id"], self.session["id"])
        self.assertEqual((await self.finish(response.json()["task"]))["task"]["status"], "completed")

    async def test_wrong_job_missing_version_active_and_running_report_block_before_model(self):
        original = sessions.get_session(self.session["id"])
        for cfg in ({**original["config"], "preset_id": "f" * 32}, {**original["config"], "resume_version_id": ""},
                    {**original["config"], "resume_version_id": "f" * 32}):
            sessions.update_session(self.session["id"], {"config": cfg})
            with self.assertRaises(store.PreparationError):
                self.start()
        sessions.update_session(self.session["id"], {"config": original["config"]})
        sessions.prepare_retry(self.session["id"], "q-1")
        with self.assertRaises(store.PreparationConflict):
            self.start()
        sessions.end_session(self.session["id"])
        with patch.object(review, "_run_review", side_effect=lambda *args: asyncio.sleep(1)):
            running = review.start_review(self.session["id"])
            with self.assertRaises(store.PreparationConflict):
                self.start()
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
        self.assertEqual(self.calls, [])

    async def test_orphan_report_is_recovered_without_model_and_empty_answers_rejected(self):
        sessions.update_session(self.session["id"], {"status": "reviewing", "report_job": {"id": "orphan", "generation": 1, "status": "running"}})
        done = await self.finish(self.start()["task"])
        self.assertEqual(done["task"]["status"], "completed")
        self.assertEqual(sessions.get_session(self.session["id"])["status"], "review_failed")
        self.assertEqual(len(self.calls), 1)
        sessions.update_session(self.session["id"], {"turns": [{"question_id": "q-2", "attempt": 1, "status": "unanswered", "answer": ""}]})
        with self.assertRaises(store.PreparationError):
            self.start()

    async def test_invalid_outputs_never_publish_wrong_attempt_quotes_or_generated_summary(self):
        variants = []
        for changes in ({"question_id": "q-forged"}, {"attempt": 2}, {"quote": "报告里的优秀示范"},
                        {"text": "我独立领导全部项目"}, {"attempt": "1"}, {"confirmed": True}, {"text": ""}, {"question_id": " q-1 "}):
            value = self.output()
            value["facts"][0].update(changes)
            variants.append(value)
        variants.append({"facts": self.output()["facts"] * 13})
        for value in variants:
            with self.subTest(value=value), patch.object(tasks, "chat_once", return_value=json.dumps(value, ensure_ascii=False)) as remote:
                done = await self.finish(self.start()["task"])
                self.assertEqual(done["task"]["status"], "failed")
                self.assertEqual(remote.call_count, 2)
                self.assertEqual(store.get_preparation_view(self.job["id"])["facts"], [])

    async def test_wrong_turn_quote_and_retry_attempt_are_kept_separate(self):
        sessions.prepare_retry(self.session["id"], "q-1")
        sessions.record_answer(self.session["id"], "我核对并补齐了需求清单。")
        first = self.output()
        first["facts"][0].update(attempt=2)
        with patch.object(tasks, "chat_once", return_value=json.dumps(first)):
            self.assertEqual((await self.finish(self.start()["task"]))["task"]["status"], "failed")
        correct = {"facts": [{"question_id": "q-1", "attempt": 2, "quote": "我核对并补齐了需求清单。", "text": "补齐了需求清单"}]}
        with patch.object(tasks, "chat_once", return_value=json.dumps(correct)):
            done = await self.finish(self.start()["task"])
        self.assertEqual(done["task"]["result"]["facts"][0]["source"]["attempt"], 2)

    async def test_retry_during_request_blocks_publication_and_corrective_retry(self):
        for invalid in (False, True):
            entered, release = asyncio.Event(), asyncio.Event()
            calls = []
            async def blocked(**kwargs):
                calls.append(kwargs)
                entered.set()
                await release.wait()
                return "invalid-json" if invalid else json.dumps(self.output())
            with patch.object(tasks, "chat_once", side_effect=blocked):
                task = self.start()["task"]
                await entered.wait()
                sessions.prepare_retry(self.session["id"], "q-1")
                release.set()
                done = await self.finish(task)
            self.assertEqual(len(calls), 1)
            self.assertTrue(done["task"]["stale"])
            self.assertNotEqual(done["task"]["status"], "completed")
            self.assertEqual(store.get_preparation_view(self.job["id"])["facts"], [])
            with self.assertRaises(store.PreparationConflict):
                tasks.retry_task(self.job["id"], task["id"])
            sessions.end_session(self.session["id"])

    async def test_cancel_and_new_generation_late_invalid_response_cannot_publish_or_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def late(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return "invalid-json"
            return json.dumps(self.output())
        with patch.object(tasks, "chat_once", side_effect=late):
            first = self.start()["task"]
            await entered.wait()
            tasks.cancel_task(self.job["id"], first["id"])
            new = tasks.retry_task(self.job["id"], first["id"])["task"]
            done = await self.finish(new)
            release.set()
            await asyncio.sleep(0.02)
        self.assertEqual(len(calls), 2)
        self.assertEqual(done["task"]["generation"], 2)
        self.assertEqual(len(store.get_preparation_view(self.job["id"])["facts"]), 1)

    async def test_duplicate_results_preserve_fact_decision_and_revision(self):
        done = await self.finish(self.start()["task"])
        fact = done["task"]["result"]["facts"][0]
        store.set_fact_decision(self.job["id"], fact["id"], revision=done["revision"], decision="reject")
        before = store.get_preparation_view(self.job["id"])["revision"]
        self.connection["base_url"] = "https://second.example/v1"
        duplicate = await self.finish(self.start()["task"])
        self.assertEqual(duplicate["task"]["result"]["created_count"], 0)
        self.assertEqual(duplicate["revision"], before)
        self.assertEqual(duplicate["task"]["result"]["facts"][0]["id"], fact["id"])
        self.assertEqual(duplicate["task"]["result"]["facts"][0]["status"], "rejected")

    async def test_changed_or_deleted_source_and_job_never_publish_old_results(self):
        original = sessions.get_session(self.session["id"])
        original_presets = copy.deepcopy(presets._read())
        for change in ("answer", "source_version", "missing_session", "job", "missing_job"):
            with self.subTest(change=change):
                entered, release = asyncio.Event(), asyncio.Event()
                async def blocked(**kwargs):
                    entered.set()
                    await release.wait()
                    return json.dumps(self.output())
                with patch.object(tasks, "chat_once", side_effect=blocked) as model:
                    task = self.start()["task"]
                    await entered.wait()
                    if change == "answer":
                        amended = copy.deepcopy(original["turns"])
                        amended[0]["answer"] = "原回答已更正，不能发布此前提取的片段。"
                        sessions.update_session(self.session["id"], {"turns": amended})
                    elif change == "source_version":
                        sessions.update_session(self.session["id"], {"config": {**original["config"], "resume_version_id": "f" * 32}})
                    elif change == "job":
                        changed = copy.deepcopy(original_presets)
                        changed[0]["jd"] = "岗位输入已改变"
                        presets._save(changed)
                    elif change == "missing_job":
                        presets._save([])
                    source_patch = patch.object(review, "recover_report_job", return_value=None) if change == "missing_session" else patch.object(review, "recover_report_job", wraps=review.recover_report_job)
                    with source_patch:
                        release.set()
                        # Poll storage directly when the source job no longer exists.
                        for _ in range(100):
                            final = store.read_preparation(self.job["id"])["tasks"][task["id"]]
                            if final["status"] != "running":
                                break
                            await asyncio.sleep(.001)
                    self.assertEqual(final["status"], "failed")
                    self.assertEqual(model.call_count, 1)
                    self.assertEqual(store.read_preparation(self.job["id"])["facts"], {})
                sessions.update_session(self.session["id"], {"config": original["config"], "turns": original["turns"]})
                presets._save(copy.deepcopy(original_presets))

    async def test_source_changed_before_runner_scheduled_makes_zero_calls(self):
        task = self.start()["task"]
        sessions.prepare_retry(self.session["id"], "q-1")
        done = await self.finish(task)
        self.assertEqual(done["task"]["status"], "failed")
        self.assertEqual(self.calls, [])
        self.assertEqual(store.get_preparation_view(self.job["id"])["facts"], [])

    async def test_shutdown_restart_stops_invalid_late_response_and_requires_explicit_retry(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def late(**kwargs):
            self.calls.append(kwargs)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return "invalid-json"
        with patch.object(tasks, "chat_once", side_effect=late):
            first = self.start()["task"]
            await entered.wait()
            stop = asyncio.create_task(tasks.shutdown_tasks())
            await asyncio.sleep(0)
            release.set()
            await stop
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(tasks.get_task(self.job["id"], first["id"])["task"]["status"], "interrupted")
        with store.locked_preparation(self.job["id"]):
            data = store.read_preparation(self.job["id"])
            data["tasks"][first["id"]].update(status="running", runner_id="previous-process")
            store.write_preparation(self.job["id"], data)
        self.assertEqual(tasks.get_preparation(self.job["id"])["tasks"][0]["status"], "interrupted")
        self.assertEqual(len(self.calls), 1)
        retry = tasks.retry_task(self.job["id"], first["id"])
        self.assertEqual((await self.finish(retry["task"]))["task"]["status"], "completed")
        self.assertEqual(len(self.calls), 2)

    async def test_confirmed_revision_feeds_future_prep_only_after_explicit_confirmation(self):
        done = await self.finish(self.start()["task"])
        fact = done["task"]["result"]["facts"][0]
        revised = store.set_fact_decision(self.job["id"], fact["id"], revision=done["revision"], decision="confirm", text="在测试项目中负责需求访谈。")
        self.assertNotEqual(revised["id"], fact["id"])
        self.assertEqual(revised["status"], "pending")
        self.assertEqual(store.get_preparation_view(self.job["id"])["evidence_facts"], [])
        revised = store.set_fact_decision(self.job["id"], revised["id"], revision=revised["revision"], decision="confirm")
        view = store.get_preparation_view(self.job["id"])
        self.assertEqual([item["id"] for item in view["evidence_facts"]], [revised["id"]])
        self.assertEqual(revised["source_quote"], fact["source_quote"])
        data = store.read_preparation(self.job["id"])
        explicit_inputs = tasks._inputs("materials", data, self.job, self.version, self.connection)
        self.assertEqual(explicit_inputs["sources"]["fact:" + revised["id"]], revised["text"])
        self.assertNotIn("fact:" + fact["id"], explicit_inputs["sources"])
        newer = store.create_version(self.job["id"], revision=view["revision"], name="核对后简历", resume="参与测试项目；在测试项目中负责需求访谈。", parent_id=self.version)
        launch = tasks.resolve_launch(self.job["id"], newer["current_version_id"])
        self.assertEqual(launch["version"]["id"], newer["current_version_id"])
        self.assertEqual(sessions.get_session(self.session["id"])["config"]["resume_version_id"], self.version)
        self.assertEqual(len(self.calls), 1, "confirm/version/launch must not implicitly call an expert")

    async def test_source_locks_are_session_then_preparation_in_read_retry_and_publish(self):
        # A reverse acquisition would deadlock with a concurrent end/retry.
        held = []
        actual_session, actual_prep = sessions.locked_session, store.locked_preparation
        @contextmanager
        def session_lock(sid):
            self.assertNotIn("preparation", held)
            with actual_session(sid):
                held.append("session")
                try:
                    yield
                finally:
                    held.pop()
        @contextmanager
        def prep_lock(pid):
            with actual_prep(pid):
                held.append("preparation")
                try:
                    yield
                finally:
                    held.pop()
        with patch.object(sessions, "locked_session", side_effect=session_lock), patch.object(store, "locked_preparation", side_effect=prep_lock):
            first = self.start()["task"]
            tasks.cancel_task(self.job["id"], first["id"])
            retried = tasks.retry_task(self.job["id"], first["id"])
            done = await self.finish(retried["task"])
            self.assertEqual(done["task"]["status"], "completed")
            self.assertFalse(tasks.get_preparation(self.job["id"])["tasks"][0]["stale"])
