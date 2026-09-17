"""Preparation tasks use synthetic data and stub only the remote model boundary."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend import config, preparation_store as store
from backend import xiaomi_client
from backend import preparation_tasks as tasks
from backend.preparation_models import validate_result
from backend.routers import presets
from backend.main import app


def result_fixture(kind, resume="参与了一个测试项目，负责需求访谈。", jd="负责产品需求分析与验证。"):
    if kind == "resume":
        return {"assessment": {"dimensions": {name: {"score": 7, "comment": "以已提供材料评价"} for name in ("clarity", "structure", "relevance", "evidence")}},
                "suggestions": [{"target": "参与了一个测试项目", "replacement": "在测试项目中参与协作", "reason": "明确项目场景"},
                                {"target": "负责需求访谈", "replacement": "承担需求访谈工作", "reason": "明确个人行动"}], "missing_information": ["补充可核实的结果"],
                "advice": [{"category": "evidence", "priority": "high", "title": "补充项目验证依据",
                    "problem": "已有项目行动，材料尚未交代验证方式。", "action": "补充可核实交付物与验收方式，没有真实数字时不要猜测。",
                    "questions": ["结论由谁确认？有哪些交付物？"], "evidence": [{"source_id": "resume", "quote": resume}]}]}
    if kind == "recruitment":
        return {"requirements": [{"requirement": "产品需求分析与验证", "priority": "must", "status": "needs_verification", "source_quote": jd,
                                   "evidence": [{"source_id": "resume", "quote": "负责需求访谈"}], "note": "验证工作还需核实"}], "missing_information": []}
    return {"materials": [{"title": "测试项目需求访谈", "situation": "测试项目", "task": "需求访谈", "action": "负责需求访谈", "result": "",
                            "evidence": [{"source_id": "resume", "quote": "负责需求访谈"}]}],
            "self_introduction": "我参与了测试项目，负责需求访谈。", "missing_information": ["补充可核实的结果"]}


class PreparationTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.connection = {"base_url": "https://models.example/v1", "api_key": "synthetic-placeholder", "protocol": "openai"}
        self.patches = [patch.object(config, "DATA_DIR", self.root), patch.object(config, "SESSION_DIR", self.root / "sessions"),
                        patch.object(config, "get_connection", side_effect=lambda _: dict(self.connection)), patch.object(config, "LLM_MODEL_PRO", "test-analysis")]
        for item in self.patches:
            item.start()
        self.calls = []

        async def fake(**kwargs):
            self.calls.append(kwargs)
            payload = json.loads(kwargs["messages"][1]["content"])
            return json.dumps(result_fixture(payload["kind"]), ensure_ascii=False)

        self.fake_patch = patch.object(tasks, "chat_once", side_effect=fake)
        self.fake_patch.start()
        self.job = await presets.create_preset(presets.PresetInput(name="测试岗位", company="示例公司", target_role="产品助理", jd="负责产品需求分析与验证。", resume="参与了一个测试项目，负责需求访谈。"))
        self.view = store.get_preparation_view(self.job["id"])
        self.version = self.view["current_version_id"]

    async def asyncTearDown(self):
        await tasks.shutdown_tasks()
        self.fake_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def start(self, kind="resume", **kwargs):
        view = store.get_preparation_view(self.job["id"])
        return tasks.create_task(self.job["id"], kind=kind, version_id=kwargs.get("version_id", self.version), revision=kwargs.get("revision", view["revision"]))

    async def finish(self, task):
        for _ in range(100):
            envelope = tasks.get_task(self.job["id"], task["id"])
            if envelope["task"]["status"] != "running":
                return envelope
            await asyncio.sleep(0.001)
        self.fail("stub task did not finish")

    async def test_three_real_tasks_reuse_completed_and_keep_diagnosis_bound(self):
        for kind in ("resume", "recruitment", "materials"):
            started = self.start(kind)
            duplicate = self.start(kind)
            self.assertEqual(started["task"]["id"], duplicate["task"]["id"])
            result = await self.finish(started["task"])
            self.assertEqual(result["task"]["status"], "completed")
            self.assertEqual(result["task"]["version_id"], self.version)
            self.assertEqual(self.start(kind)["task"]["id"], started["task"]["id"])
        self.assertEqual(len(self.calls), 3)
        dossier = store.get_preparation_view(self.job["id"])
        self.assertEqual(len(dossier["suggestions"]), 2)
        self.assertEqual(len(dossier["facts"]), 0)
        self.assertEqual(len(dossier["versions"]), 1)
        snap = tasks.resolve_launch(self.job["id"], self.version)
        self.assertEqual(snap["preparation_snapshot"]["resume_assessment"]["total"], 70)
        self.assertEqual(len(snap["preparation_snapshot"]["requirements"]), 1)
        self.assertNotIn("synthetic-placeholder", store.preparation_path(self.job["id"]).read_text())

    async def test_new_advisor_version_invalidates_only_resume_and_never_auto_reruns(self):
        with patch.object(tasks, 'RESUME_PROMPT_VERSION', 'preparation-v1'):
            old = await self.finish(self.start('resume')['task'])
            recruitment = await self.finish(self.start('recruitment')['task'])
        calls_before = len(self.calls)
        self.assertTrue(tasks.get_task(self.job['id'], old['task']['id'])['task']['stale'])
        self.assertFalse(tasks.get_task(self.job['id'], recruitment['task']['id'])['task']['stale'])
        self.assertEqual(len(self.calls), calls_before)
        fresh = await self.finish(self.start('resume')['task'])
        self.assertNotEqual(fresh['task']['id'], old['task']['id'])
        self.assertEqual(len(self.calls), calls_before + 1)
        self.assertTrue(fresh['task']['result']['advice'])
        self.assertEqual(store.get_preparation_view(self.job['id'])['facts'], [])

    async def test_whitespace_anchor_is_adopted_exactly_without_mutating_original(self):
        resume = "负责需求访谈，使用 Python\n  工具整理反馈。"
        self.job = await presets.create_preset(presets.PresetInput(
            name="换行定位测试", company="示例公司", target_role="产品助理",
            jd="负责产品需求分析与验证。", resume=resume))
        self.version = store.get_preparation_view(self.job["id"])["current_version_id"]
        raw = result_fixture("resume", resume=resume)
        raw["suggestions"] = [{"target": "使用 Python 工具整理反馈",
            "replacement": "用 Python 工具归整反馈", "reason": "明确已有行动"}]
        with patch.object(tasks, "chat_once", return_value=json.dumps(raw)):
            done = await self.finish(self.start()["task"])
        self.assertEqual(done["task"]["status"], "completed")
        view = store.get_preparation_view(self.job["id"])
        suggestion = view["suggestions"][0]
        self.assertTrue(suggestion["anchor_adjusted"])
        self.assertEqual(suggestion["target"], "使用 Python\n  工具整理反馈")
        with self.assertRaises(store.PreparationConflict):
            store.accept_suggestion(self.job["id"], suggestion["id"], view["revision"])
        accepted = store.accept_suggestion(self.job["id"], suggestion["id"], view["revision"], truth_confirmed=True)
        self.assertEqual(accepted["version"]["resume"], "负责需求访谈，用 Python 工具归整反馈。")
        original = next(version for version in store.get_preparation_view(self.job["id"])["versions"]
                        if version["id"] == self.version)
        self.assertEqual(original["resume"], resume)
        self.assertTrue(store.accept_suggestion(self.job["id"], suggestion["id"], view["revision"], truth_confirmed=True)["reused"])

    async def test_connection_snapshot_and_job_change_keep_old_result_without_publishing(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(**kwargs):
            self.calls.append(kwargs)
            entered.set()
            await release.wait()
            return json.dumps(result_fixture("resume"))
        with patch.object(tasks, "chat_once", side_effect=blocked):
            started = self.start()
            await entered.wait()
            self.connection["base_url"] = "https://second.example/v1"
            release.set()
            done = await self.finish(started["task"])
        self.assertTrue(done["task"]["stale"])
        self.assertFalse(done["task"]["proposals_published"])
        self.assertEqual(self.calls[0]["connection_snapshot"]["base_url"], "https://models.example/v1")
        self.assertEqual(store.get_preparation_view(self.job["id"])["suggestions"], [])
        self.assertIsNone(tasks.resolve_launch(self.job["id"], self.version)["preparation_snapshot"]["resume_assessment"])

    async def test_cancel_and_retry_generation_ignore_uncancellable_late_result(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def late(**kwargs):
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return json.dumps(result_fixture("resume"))
        with patch.object(tasks, "chat_once", side_effect=late):
            first = self.start()["task"]
            await entered.wait()
            tasks.cancel_task(self.job["id"], first["id"])
            retried = tasks.retry_task(self.job["id"], first["id"])["task"]
            self.assertGreater(retried["generation"], first["generation"])
            tasks.cancel_task(self.job["id"], first["id"])
            release.set()
            await asyncio.sleep(0.02)
        state = tasks.get_task(self.job["id"], first["id"])["task"]
        self.assertEqual(state["status"], "cancelled")
        self.assertIsNone(state["result"])
        self.assertEqual(store.get_preparation_view(self.job["id"])["suggestions"], [])

    async def test_cancelled_late_invalid_output_cannot_start_corrective_remote_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def invalid_late(**kwargs):
            calls.append(kwargs)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return 'not-json'
        with patch.object(tasks, "chat_once", side_effect=invalid_late):
            first = self.start()["task"]
            await entered.wait()
            tasks.cancel_task(self.job["id"], first["id"])
            release.set()
            await asyncio.sleep(0.03)
        self.assertEqual(len(calls), 1, "cancelled task issued a second billable request")
        self.assertEqual(tasks.get_task(self.job["id"], first["id"])["task"]["status"], "cancelled")

    async def test_old_generation_late_invalid_output_cannot_retry_after_new_success(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def first_invalid_then_valid(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                entered.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return 'not-json'
            return json.dumps(result_fixture("resume"))
        with patch.object(tasks, "chat_once", side_effect=first_invalid_then_valid):
            first = self.start()["task"]
            await entered.wait()
            tasks.cancel_task(self.job["id"], first["id"])
            second = tasks.retry_task(self.job["id"], first["id"])["task"]
            done = await self.finish(second)
            self.assertEqual(done["task"]["status"], "completed")
            release.set()
            await asyncio.sleep(0.03)
        self.assertEqual(len(calls), 2, "old generation issued a corrective request after retry completed")
        latest = tasks.get_task(self.job["id"], first["id"])["task"]
        self.assertEqual(latest["generation"], second["generation"])
        self.assertEqual(latest["status"], "completed")

    async def test_shutdown_invalidates_work_before_uncancellable_response_returns(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def invalid_late(**kwargs):
            calls.append(kwargs)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return 'not-json'
        with patch.object(tasks, "chat_once", side_effect=invalid_late):
            first = self.start()["task"]
            await entered.wait()
            stopping = asyncio.create_task(tasks.shutdown_tasks())
            await asyncio.sleep(0)
            release.set()
            await stopping
        state = tasks.get_task(self.job["id"], first["id"])["task"]
        self.assertEqual(len(calls), 1, "shutdown triggered another remote call")
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(store.get_preparation_view(self.job["id"])["suggestions"], [])

    async def test_failed_kind_preserves_other_results_and_retries_explicitly(self):
        good = await self.finish(self.start("recruitment")["task"])
        with patch.object(tasks, "chat_once", side_effect=RuntimeError("secret full personal resume")):
            bad = await self.finish(self.start("resume")["task"])
        self.assertEqual(bad["task"]["status"], "failed")
        self.assertNotIn("secret", bad["task"]["error"])
        self.assertEqual(tasks.get_task(self.job["id"], good["task"]["id"])["task"]["status"], "completed")
        retried = tasks.retry_task(self.job["id"], bad["task"]["id"])
        self.assertEqual((await self.finish(retried["task"]))["task"]["status"], "completed")

    async def test_restart_orphans_interrupted_never_auto_run(self):
        task = self.start()["task"]
        await tasks.shutdown_tasks()
        with store.locked_preparation(self.job["id"]):
            data = store.read_preparation(self.job["id"])
            data["tasks"][task["id"]]["status"] = "running"
            data["tasks"][task["id"]]["runner_id"] = "old-process"
            store.write_preparation(self.job["id"], data)
        old_count = len(self.calls)
        state = tasks.get_task(self.job["id"], task["id"])["task"]
        self.assertEqual(state["status"], "interrupted")
        self.assertEqual(len(self.calls), old_count)
        retried = tasks.retry_task(self.job["id"], task["id"])
        self.assertEqual((await self.finish(retried["task"]))["task"]["status"], "completed")

    async def test_current_version_change_does_not_erase_old_assessment_and_can_republish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(**kwargs):
            entered.set()
            await release.wait()
            return json.dumps(result_fixture("resume"))
        with patch.object(tasks, "chat_once", side_effect=blocked):
            initial = self.start()["task"]
            await entered.wait()
            old = store.get_preparation_view(self.job["id"])
            store.create_version(self.job["id"], old["revision"], self.job["resume"] + "\n另有项目。", "新版本", parent_id=self.version)
            release.set()
            done = await self.finish(initial)
        self.assertFalse(done["task"]["stale"])
        self.assertFalse(done["task"]["proposals_published"])
        self.assertEqual(tasks.resolve_launch(self.job["id"], self.version)["preparation_snapshot"]["resume_assessment"]["total"], 70)
        explicit = self.start()["task"]
        fresh = await self.finish(explicit)
        self.assertTrue(fresh["task"]["proposals_published"])
        self.assertEqual(len(store.get_preparation_view(self.job["id"])["suggestions"]), 2)

    async def test_confirmed_facts_affect_fingerprint_pending_facts_do_not(self):
        first = await self.finish(self.start()["task"])
        view = store.get_preparation_view(self.job["id"])
        fact = store.ingest_fact_proposal(self.job["id"], revision=view["revision"], text="我参与原型设计。", source={"kind": "test", "quote": "我参与原型设计。"})
        self.assertFalse(tasks.get_task(self.job["id"], first["task"]["id"])["task"]["stale"])
        store.set_fact_decision(self.job["id"], fact["id"], fact["revision"], "confirm", "")
        self.assertTrue(tasks.get_task(self.job["id"], first["task"]["id"])["task"]["stale"])
        self.assertNotEqual(first["task"]["id"], self.start()["task"]["id"])

    async def test_other_jobs_and_wrong_revision_cannot_write_or_read_task(self):
        first = await self.finish(self.start()["task"])
        other = await presets.create_preset(presets.PresetInput(name="另一个", target_role="产品助理", jd=self.job["jd"], resume=self.job["resume"]))
        with self.assertRaises(store.PreparationNotFound):
            tasks.get_task(other["id"], first["task"]["id"])
        with self.assertRaises(store.PreparationConflict):
            tasks.create_task(other["id"], kind="resume", version_id=store.get_preparation_view(other["id"])["current_version_id"], revision=99)
        self.assertFalse(store.preparation_path(other["id"]).exists())

    async def test_invalid_model_shape_uses_only_one_corrective_retry(self):
        with patch.object(tasks, "chat_once", return_value='{"assessment": "bad"}') as call:
            failed = await self.finish(self.start()["task"])
        self.assertEqual(failed["task"]["status"], "failed")
        self.assertEqual(call.call_count, 2)

    async def test_task_api_lifecycle_confirmation_and_invalid_retry_payload(self):
        with TestClient(app) as client:
            created = client.post(f'/api/preparation/{self.job["id"]}/tasks', json={"kind": "resume", "version_id": self.version, "revision": 0})
            self.assertEqual(created.status_code, 202)
            task_id = created.json()["task"]["id"]
            for _ in range(30):
                state = client.get(f'/api/preparation/{self.job["id"]}/tasks/{task_id}').json()
                if state["task"]["status"] != "running":
                    break
            self.assertEqual(state["task"]["status"], "completed")
            suggestion = state["task"]["result"]["suggestions"][0]
            accept_url = f'/api/preparation/{self.job["id"]}/suggestions/{suggestion["id"]}/accept'
            for truth, expected in [(None, 409), (False, 409), ("true", 422), (True, 200)]:
                body = {"revision": state["revision"]}
                if truth is not None:
                    body["truth_confirmed"] = truth
                response = client.post(accept_url, json=body)
                self.assertEqual(response.status_code, expected, response.text)
            accepted = response.json()
            replay = client.post(accept_url, json={"revision": state["revision"], "truth_confirmed": True})
            self.assertEqual(replay.json()["version"]["id"], accepted["version"]["id"])
            bad_retry = client.post(f'/api/preparation/{self.job["id"]}/tasks/{task_id}/retry', json={"result": {}})
            self.assertEqual(bad_retry.status_code, 422)
            self.assertEqual(client.post(f'/api/preparation/{self.job["id"]}/tasks/{task_id}/retry', json={}).status_code, 409)

    async def test_revoked_evidence_blocks_old_suggestion_inside_store(self):
        fact = store.ingest_fact_proposal(self.job["id"], revision=0, text="做过产品原型。", source={"kind": "test", "quote": "做过产品原型。"})
        store.set_fact_decision(self.job["id"], fact["id"], fact["revision"], "confirm", "")
        done = await self.finish(self.start()["task"])
        view = store.get_preparation_view(self.job["id"])
        rejected = store.set_fact_decision(self.job["id"], fact["id"], view["revision"], "reject", "")
        suggestion = done["task"]["result"]["suggestions"][0]
        with self.assertRaises(store.PreparationConflict):
            store.accept_suggestion(self.job["id"], suggestion["id"], rejected["revision"], truth_confirmed=True)

    async def test_credential_bearing_url_rejected_without_persistence(self):
        self.connection["base_url"] = "https://model.example/v1?api_key=do-not-store"
        with self.assertRaises(store.PreparationError):
            self.start()
        self.assertFalse(store.preparation_path(self.job["id"]).exists())
        self.assertEqual(self.calls, [])

    async def test_json_client_uses_captured_connection_without_rereading_settings(self):
        create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'), finish_reason='stop')]))
        fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        fake.with_options = lambda **kwargs: fake
        with patch.object(xiaomi_client, "get_client", return_value=fake) as get, patch.object(config, "get_connection", side_effect=AssertionError("must not reread")):
            value = await xiaomi_client.chat_once([], model="test-analysis", json_mode=True, connection_snapshot=self.connection)
        self.assertEqual(value, '{}')
        get.assert_called_once_with("analysis", connection_snapshot=self.connection)


class PreparationValidationTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {"kind": "resume", "job": {"jd": "负责产品需求分析与验证。"}, "version_id": "a" * 32,
                       "resume_version": {"id": "a" * 32, "resume": "参与了一个测试项目，负责需求访谈。"},
                       "sources": {"resume": "参与了一个测试项目，负责需求访谈。"}}

    def test_numbers_and_bad_anchor_are_nonapplicable_drafts(self):
        raw = result_fixture("resume")
        raw["suggestions"][0]["replacement"] += "，提升效率 90%。"
        raw["suggestions"][1]["target"] = "材料中不存在"
        validated = validate_result("resume", raw, self.inputs)
        self.assertTrue(all(not item["applicable"] for item in validated["suggestions"]))
        self.assertTrue(all(item["needs_confirmation"] for item in validated["suggestions"]))
        self.assertEqual(validated["assessment"]["total"], 70)

    def test_invalid_requirement_or_evidence_quote_fails_closed(self):
        for field in ("source_quote", "evidence"):
            raw = result_fixture("recruitment")
            raw["requirements"][0][field] = "不存在" if field == "source_quote" else [{"source_id": "fact:other", "quote": "不存在"}]
            with self.assertRaises(ValueError):
                validate_result("recruitment", raw, self.inputs)

    def test_bounded_strict_scores_and_material_sources(self):
        raw = result_fixture("resume")
        for bad in (True, float("nan"), 12, "7"):
            raw["assessment"]["dimensions"]["clarity"]["score"] = bad
            with self.assertRaises(ValueError):
                validate_result("resume", raw, self.inputs)
        raw = result_fixture("materials")
        raw["materials"][0]["evidence"][0]["quote"] = "伪造原文"
        with self.assertRaises(ValueError):
            validate_result("materials", raw, self.inputs)

    def test_client_cannot_submit_model_output(self):
        with TestClient(app) as client:
            response = client.post('/api/preparation/' + 'a' * 32 + '/tasks', json={"kind": "resume", "version_id": "b" * 32, "revision": 0, "result": {"assessment": {}}})
        self.assertEqual(response.status_code, 422)
