import threading
import asyncio
import json
import tempfile
import unittest
import uuid
from itertools import permutations
from pathlib import Path
from unittest.mock import patch
import httpx

from fastapi.testclient import TestClient

from backend import config, store, preparation_store as prep, preparation_tasks
from backend.main import app


class PreparationP1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patches = [
            patch.object(config, "DATA_DIR", self.root),
            patch.object(config, "SESSION_DIR", self.root / "sessions"),
        ]
        for patcher in self.patches:
            patcher.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        for patcher in self.patches:
            patcher.stop()
        self.temp.cleanup()

    def _create_preset(self):
        body = {
            "name": "岗位A",
            "target_role": "AI工程师",
            "company": "示例公司",
            "city": "北京",
            "salary_min": 8,
            "salary_max": 20,
            "salary_months": 12,
            "education": "本科",
            "responsibilities": "负责系统重构",
            "requirements": "熟练Python",
            "company_context": "SaaS服务",
            "jd": "负责对接客户并交付",
            "source_jd": "原始JD",
            "resume": "我有两年经验，熟悉 Python 与 SQL。",
        }
        response = self.client.post('/api/presets', json=body)
        self.assertEqual(response.status_code, 201)
        return response.json()

    def _create_version(self, preset, *, name="基线版本", resume=None, revision=0, parent_id=""):
        return self.client.post(
            f"/api/preparation/{preset['id']}/versions",
            json={
                "revision": revision,
                "name": name,
                "resume": resume or preset["resume"],
                "parent_id": parent_id,
            },
        )

    def _ingest_fact(self, preset_id, revision, text, source):
        return prep.ingest_fact_proposal(preset_id, revision=revision, text=text, source=source)

    def _end_session_and_get_id(self, preset_id=None, resume_version_id=None):
        session = store.create_session('JD', '真实简历', 'HR', '标准')
        store.set_active_question(session['id'], 'q-1', '请介绍一下经历。', 1)
        store.record_answer(session['id'], '我主导过一个招聘平台重构。')
        if preset_id:
            current = store.get_session(session['id'])
            config_data = dict(current["config"])
            config_data["preset_id"] = preset_id
            if resume_version_id:
                config_data["resume_version_id"] = resume_version_id
            store.update_session(session["id"], {"config": config_data})
        store.end_session(session['id'])
        return session["id"]

    def test_get_preparation_view_is_deterministic_and_no_side_effect_on_preset(self):
        preset = self._create_preset()
        presets_path = self.root / 'presets.json'
        before = presets_path.read_text(encoding='utf-8')

        response = self.client.get(f"/api/preparation/{preset['id']}")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['preset_id'], preset['id'])
        self.assertEqual(body['revision'], 0)
        self.assertFalse(body['stale'])
        self.assertEqual(body['versions'][0]['resume'], preset['resume'])
        self.assertEqual(body['facts'], [])
        self.assertFalse((self.root / 'preparations' / f"{preset['id']}.json").exists())

        self.assertEqual(presets_path.read_text(encoding='utf-8'), before)
        self.assertFalse((self.root / 'preparations').exists())
        self.assertEqual(body, self.client.get(f"/api/preparation/{preset['id']}").json())
        saved = self._create_version(preset, parent_id=body['current_version_id'])
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(prep.read_preparation(preset['id'])['versions'][body['current_version_id']], body['versions'][0])

    def test_two_job_data_is_isolated(self):
        a = self._create_preset()
        b = self._create_preset()

        a_created = self._create_version(a, resume="我有两年经验。")
        b_created = self._create_version(b, resume="我有三年经验。")

        self.assertEqual(a_created.status_code, 201)
        self.assertEqual(b_created.status_code, 201)
        self.assertNotEqual(a_created.json()['current_version_id'], b_created.json()['current_version_id'])

        a_data = prep.read_preparation(a['id'])
        b_data = prep.read_preparation(b['id'])
        self.assertIsNotNone(a_data)
        self.assertIsNotNone(b_data)
        self.assertNotEqual(a_data['current_version_id'], b_data['current_version_id'])

    def test_preset_change_marks_dossier_stale_and_preserves_old_versions(self):
        preset = self._create_preset()
        create = self._create_version(preset, resume=preset['resume'])
        self.assertEqual(create.status_code, 201)
        baseline_rev = create.json()['revision']

        before = self.client.get(f"/api/preparation/{preset['id']}").json()
        self.assertFalse(before['stale'])
        self.assertGreaterEqual(len(before['versions']), 1)

        # Inject an uncommitted fact via internal helper.
        fact = self._ingest_fact(preset['id'], baseline_rev, "我主导过一次重构。", {
            "session_id": "s1",
            "question_id": "q-1",
            "attempt": 1,
            "question": "请介绍一次重构。",
        })
        self.assertEqual(fact['status'], 'pending')

        updated = dict(preset)
        updated['resume'] = "我有三年经验，熟悉 Python、SQL 与 Redis。"
        update = self.client.put(f"/api/presets/{preset['id']}", json=updated)
        self.assertEqual(update.status_code, 200)

        stale = self.client.get(f"/api/preparation/{preset['id']}").json()
        self.assertTrue(stale['stale'])
        self.assertEqual(len(stale['facts']), 1)

        fresh_ids = {item['id'] for item in stale['versions']}
        new_snapshot_ids = [
            item['id']
            for item in stale['versions']
            if item.get('source', {}).get('version_signature') == prep._preset_signature(update.json())
        ]
        self.assertEqual(len(new_snapshot_ids), 1)
        self.assertNotIn(create.json()['current_version_id'], new_snapshot_ids)

    def test_first_visible_parent_save_and_invalid_stale_parent(self):
        preset = self._create_preset()
        old = self._create_version(preset, resume=preset['resume'])
        self.assertEqual(old.status_code, 201)
        old_json = old.json()

        changed = dict(preset)
        changed['resume'] = "我有三年经验，熟悉 Python 与 SQL 与 Redis。"
        changed_snapshot = self.client.put(f"/api/presets/{preset['id']}", json=changed).json()
        stale = self.client.get(f"/api/preparation/{preset['id']}").json()
        self.assertTrue(stale['stale'])
        new_snapshot_version = next(
            v for v in stale['versions']
            if v.get('source', {}).get('version_signature') == prep._preset_signature(changed_snapshot)
        )

        rejected = self.client.post(
            f"/api/preparation/{preset['id']}/versions",
            json={
                "revision": stale['revision'],
                "name": "基线更新",
                "resume": changed['resume'],
                "parent_id": old_json['current_version_id'],
            },
        )
        self.assertEqual(rejected.status_code, 409)

        accepted = self.client.post(
            f"/api/preparation/{preset['id']}/versions",
            json={
                "revision": stale['revision'],
                "name": "基线更新",
                "resume": changed['resume'],
                "parent_id": new_snapshot_version['id'],
            },
        )
        self.assertEqual(accepted.status_code, 201)
        accepted_json = accepted.json()
        self.assertNotEqual(accepted_json['revision'], stale['revision'])
        persisted = prep.read_preparation(preset['id'])
        self.assertIsNotNone(persisted)
        self.assertIn(new_snapshot_version['id'], persisted['versions'])
        self.assertIn(old_json['current_version_id'], persisted['versions'])
        self.assertEqual(persisted['current_version_id'], accepted_json['current_version_id'])

    def test_invalid_version_save_does_not_create_file(self):
        preset = self._create_preset()
        path = self.root / 'preparations' / f"{preset['id']}.json"
        invalid = self.client.post(
            f"/api/preparation/{preset['id']}/versions",
            json={"revision": 1, "name": "错误版本", "resume": preset['resume']},
        )
        self.assertEqual(invalid.status_code, 409)
        self.assertFalse(path.exists())

    def test_cumulative_disjoint_suggestions_and_old_request_replay(self):
        preset = self._create_preset()
        base = self._create_version(
            preset,
            resume="我有两年经验，熟悉 Python、SQL 与分布式系统。",
            revision=0,
        )
        self.assertEqual(base.status_code, 201)
        revision = base.json()['revision']
        current_id = base.json()['current_version_id']

        data = prep.read_preparation(preset['id'])
        self.assertIsNotNone(data)
        s1 = uuid.uuid4().hex
        s2 = uuid.uuid4().hex
        data['suggestions'] = {
            s1: {
                'id': s1,
                'source_version_id': current_id,
                'target': 'Python',
                'replacement': 'Python 3',
                'status': 'pending',
            },
            s2: {
                'id': s2,
                'source_version_id': current_id,
                'target': 'SQL',
                'replacement': 'PostgreSQL',
                'status': 'pending',
            },
        }
        prep.write_preparation(preset['id'], data)

        first = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s1}/accept",
            json={'revision': revision},
        )
        self.assertEqual(first.status_code, 200)
        after_first = first.json()
        self.assertFalse(after_first['reused'])

        second = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s2}/accept",
            json={'revision': after_first['revision']},
        )
        self.assertEqual(second.status_code, 200)
        after_second = second.json()
        self.assertFalse(after_second['reused'])
        self.assertNotEqual(after_second['version']['id'], after_first['version']['id'])
        final = prep.read_preparation(preset['id'])
        self.assertIn("Python 3", final['versions'][final['current_version_id']]['resume'])
        self.assertIn("PostgreSQL", final['versions'][final['current_version_id']]['resume'])

        replay = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s1}/accept",
            json={'revision': revision},
        )
        self.assertEqual(replay.status_code, 200)
        replay_json = replay.json()
        self.assertTrue(replay_json['reused'])
        self.assertEqual(replay_json['version']['id'], after_first['version']['id'])
        self.assertNotEqual(replay_json['current_version_id'], after_first['version']['id'])

    def test_overlap_and_ambiguity_in_suggestion_target(self):
        preset = self._create_preset()
        base = self._create_version(
            preset,
            resume="Python经验，aaa 是测试定位文本。",
            revision=0,
        )
        self.assertEqual(base.status_code, 201)
        revision = base.json()['revision']
        current_id = base.json()['current_version_id']

        ambiguous_data = prep.read_preparation(preset['id'])
        s_overlap = uuid.uuid4().hex
        ambiguous_data['suggestions'] = {
            s_overlap: {
                'id': s_overlap,
                'source_version_id': current_id,
                'target': 'aa',
                'replacement': 'x',
                'status': 'pending',
            }
        }
        prep.write_preparation(preset['id'], ambiguous_data)
        ambiguous = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s_overlap}/accept",
            json={'revision': revision},
        )
        self.assertEqual(ambiguous.status_code, 409)

        data = prep.read_preparation(preset['id'])
        s_base = uuid.uuid4().hex
        s_overlap2 = uuid.uuid4().hex
        data['suggestions'] = {
            s_base: {
                'id': s_base,
                'source_version_id': current_id,
                'target': 'Python',
                'replacement': 'Python 3',
                'status': 'pending',
            },
            s_overlap2: {
                'id': s_overlap2,
                'source_version_id': current_id,
                'target': 'Python',
                'replacement': 'PyPython',
                'status': 'pending',
            },
        }
        prep.write_preparation(preset['id'], data)

        first = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s_base}/accept",
            json={'revision': revision},
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{s_overlap2}/accept",
            json={'revision': first.json()['revision']},
        )
        self.assertEqual(second.status_code, 409)

    def test_concurrent_stale_revision_accept_one_wins(self):
        preset = self._create_preset()
        base = self._create_version(preset, resume="我有两年经验，熟悉 Python、SQL 与 Redis。")
        self.assertEqual(base.status_code, 201)
        revision = base.json()['revision']
        current_id = base.json()['current_version_id']

        data = prep.read_preparation(preset['id'])
        first = uuid.uuid4().hex
        second = uuid.uuid4().hex
        data['suggestions'] = {
            first: {
                'id': first,
                'source_version_id': current_id,
                'target': 'Python',
                'replacement': 'Python 3',
                'status': 'pending',
            },
            second: {
                'id': second,
                'source_version_id': current_id,
                'target': 'SQL',
                'replacement': 'PostgreSQL',
                'status': 'pending',
            },
        }
        prep.write_preparation(preset['id'], data)

        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def run_accept(sid: str):
            try:
                barrier.wait()
                response = self.client.post(
                    f"/api/preparation/{preset['id']}/suggestions/{sid}/accept",
                    json={'revision': revision},
                )
                with lock:
                    results.append((response.status_code, response.json()))
            except Exception as exc:
                with lock:
                    results.append((500, str(exc)))

        threads = [
            threading.Thread(target=run_accept, args=(first,)),
            threading.Thread(target=run_accept, args=(second,)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join()

        success = [item for item in results if item[0] == 200]
        conflicts = [item for item in results if item[0] == 409]
        self.assertEqual(len(success), 1)
        self.assertEqual(len(conflicts), 1)

        current = prep.read_preparation(preset['id'])
        self.assertEqual(current['revision'], revision + 1)
        self.assertEqual(len(current['versions']), 3)

    def test_fact_confirmation_supersession_and_reject_does_not_erase_evidence(self):
        preset = self._create_preset()
        create = self._create_version(preset, resume=preset['resume'])
        self.assertEqual(create.status_code, 201)
        revision = create.json()['revision']

        confirmed = self._ingest_fact(
            preset['id'],
            revision,
            "主导过一次重构。",
            {
                "session_id": "s1",
                "question_id": "q-1",
                "attempt": 1,
                "question": "请讲一个项目。",
            },
        )
        self.assertEqual(confirmed['status'], 'pending')
        fact_id = confirmed['id']

        confirm = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{fact_id}/decision",
            json={'revision': confirmed['revision'], 'decision': 'confirm'},
        )
        self.assertEqual(confirm.status_code, 200)
        confirmed = confirm.json()
        self.assertEqual(confirmed['status'], 'confirmed')
        first_revision = confirmed['revision']

        replay = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{fact_id}/decision",
            json={'revision': first_revision, 'decision': 'confirm'},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['revision'], first_revision)

        edited = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{fact_id}/decision",
            json={
                'revision': first_revision,
                'decision': 'confirm',
                'text': '我主导过两次重构。',
            },
        )
        self.assertEqual(edited.status_code, 200)
        pending = edited.json()
        self.assertEqual(pending['status'], 'pending')
        pending_id = pending['id']

        confirmed_child = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{pending_id}/decision",
            json={'revision': pending['revision'], 'decision': 'confirm'},
        )
        self.assertEqual(confirmed_child.status_code, 200)
        self.assertEqual(confirmed_child.json()['status'], 'confirmed')

        view = self.client.get(f"/api/preparation/{preset['id']}").json()
        self.assertEqual(len(view['evidence_facts']), 1)
        self.assertEqual(view['evidence_facts'][0]['id'], pending_id)
        self.assertEqual(len(view['facts']), 2)

        pending2 = self._ingest_fact(
            preset['id'],
            view['revision'],
            '另一条补充',
            {
                "session_id": "s1",
                "question_id": "q-2",
                "attempt": 1,
                "question": "再补一条。",
            },
        )
        rejected = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{pending2['id']}/decision",
            json={'revision': pending2['revision'], 'decision': 'reject'},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()['status'], 'rejected')
        view = self.client.get(f"/api/preparation/{preset['id']}").json()
        self.assertEqual(len(view['evidence_facts']), 1)

    def test_invalid_id_and_size_limits(self):
        preset = self._create_preset()
        invalid_id_response = self.client.get("/api/preparation/not-a-hex-id")
        self.assertEqual(invalid_id_response.status_code, 422)

        create = self._create_version(preset, resume=preset['resume'])
        self.assertEqual(create.status_code, 201)
        fact = self._ingest_fact(
            preset['id'],
            create.json()['revision'],
            "简要经历",
            {"session_id": "s1", "question_id": "q-1", "attempt": 1, "question": "q"},
        )
        overlong = 'x' * (prep.MAX_FACT_TEXT + 1)
        too_long = self.client.post(
            f"/api/preparation/{preset['id']}/facts/{fact['id']}/decision",
            json={'revision': fact['revision'], 'decision': 'confirm', 'text': overlong},
        )
        self.assertEqual(too_long.status_code, 422)

    def test_session_materials_requires_explicit_preset_link(self):
        preset = self._create_preset()
        _ = self._create_version(preset, resume=preset['resume'])
        session_id = self._end_session_and_get_id()
        no_link = self.client.post(
            f"/api/preparation/{preset['id']}/session-materials",
            json={'session_id': session_id, 'revision': prep.get_preparation_view(preset['id'])['revision']},
        )
        self.assertEqual(no_link.status_code, 409)

        version_id = prep.get_preparation_view(preset['id'])['current_version_id']
        session_id = self._end_session_and_get_id(preset['id'], version_id)
        with_config = store.get_session(session_id)
        conf = dict(with_config['config'])
        conf['preset_id'] = preset['id']
        conf['resume_version_id'] = conf.get('resume_version_id')
        store.update_session(session_id, {'config': conf})
        async def extract():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                response = await client.post(f"/api/preparation/{preset['id']}/session-materials",
                                            json={'session_id': session_id, 'revision': prep.get_preparation_view(preset['id'])['revision']})
                self.assertEqual(response.status_code, 202)
                task_id = response.json()['task']['id']
                for _ in range(100):
                    task = (await client.get(f"/api/preparation/{preset['id']}/tasks/{task_id}")).json()['task']
                    if task['status'] != 'running':
                        break
                    await asyncio.sleep(.001)
                self.assertEqual(task['status'], 'completed')
        with patch.object(config, 'get_connection', return_value={'base_url':'https://models.example/v1', 'api_key':'synthetic-placeholder', 'protocol':'openai'}), \
             patch.object(preparation_tasks, 'chat_once', return_value=json.dumps({'facts':[{'question_id':'q-1', 'attempt':1, 'quote':'我主导过一个招聘平台重构。', 'text':'我主导过一个招聘平台重构。'}]})):
            asyncio.run(extract())
        fact = self.client.get(f"/api/preparation/{preset['id']}").json()['facts'][0]
        self.assertEqual(fact['source_version_id'], version_id)
        self.assertEqual(fact['source']['resume_version_id'], version_id)
        self.assertEqual(fact['source_quote'], '我主导过一个招聘平台重构。')
        with patch.object(store, 'get_session') as read:
            response = self.client.post(f"/api/preparation/{preset['id']}/session-materials", json={'session_id':'../../private', 'revision': 0})
        self.assertEqual(response.status_code, 422)
        read.assert_not_called()

    def test_stale_old_ancestors_cannot_refresh_input_signature(self):
        preset = self._create_preset()
        initial = prep.get_preparation_view(preset['id'])['current_version_id']
        first = self._create_version(preset).json()
        second = self._create_version(
            preset, revision=first['revision'], parent_id=first['current_version_id'],
        ).json()
        changed = self.client.put(
            f"/api/presets/{preset['id']}",
            json={**preset, 'resume': '当前岗位的新简历材料。'},
        )
        self.assertEqual(changed.status_code, 200)
        path = prep.preparation_path(preset['id'])
        original = path.read_bytes()
        for parent_id in (initial, first['current_version_id'], second['current_version_id'], ''):
            with self.subTest(parent_id=parent_id):
                rejected = self._create_version(
                    preset, revision=second['revision'], parent_id=parent_id,
                )
                self.assertEqual(rejected.status_code, 409)
                self.assertEqual(path.read_bytes(), original)
                self.assertTrue(prep.get_preparation_view(preset['id'])['stale'])

        current_input = next(
            version for version in prep.get_preparation_view(preset['id'])['versions']
            if version['resume'] == '当前岗位的新简历材料。'
        )
        bad_revision = self._create_version(
            preset, revision=0, parent_id=current_input['id'],
        )
        self.assertEqual(bad_revision.status_code, 409)
        self.assertEqual(path.read_bytes(), original)
        refreshed = self._create_version(
            preset, revision=second['revision'], parent_id=current_input['id'],
            resume=current_input['resume'],
        )
        self.assertEqual(refreshed.status_code, 201)
        self.assertFalse(prep.get_preparation_view(preset['id'])['stale'])

    def test_accepted_suggestion_on_other_branch_does_not_block_current_branch(self):
        preset = self._create_preset()
        base = self._create_version(preset).json()
        data = prep.read_preparation(preset['id'])
        first_id, second_id = uuid.uuid4().hex, uuid.uuid4().hex
        data['suggestions'] = {
            suggestion_id: {
                'id': suggestion_id, 'source_version_id': base['current_version_id'],
                'target': 'Python', 'replacement': replacement, 'status': 'pending',
            }
            for suggestion_id, replacement in ((first_id, 'Python 3'), (second_id, 'Python 脚本'))
        }
        prep.write_preparation(preset['id'], data)
        first = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{first_id}/accept",
            json={'revision': base['revision']},
        )
        self.assertEqual(first.status_code, 200)
        branch = self._create_version(
            preset, revision=first.json()['revision'], parent_id=base['current_version_id'],
        )
        self.assertEqual(branch.status_code, 201)
        second = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{second_id}/accept",
            json={'revision': branch.json()['revision']},
        )
        self.assertEqual(second.status_code, 200)
        self.assertIn('Python 脚本', second.json()['version']['resume'])
        self.assertNotIn('Python 3', second.json()['version']['resume'])
        before_replay = prep.preparation_path(preset['id']).read_bytes()
        replay = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{first_id}/accept",
            json={'revision': base['revision']},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()['reused'])
        self.assertEqual(replay.json()['version']['id'], first.json()['version']['id'])
        self.assertEqual(replay.json()['current_version_id'], second.json()['current_version_id'])
        self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before_replay)

    def _decide_fact(self, preset_id, fact_id, decision='confirm', text='', revision=None):
        if revision is None:
            revision = prep.get_preparation_view(preset_id)['revision']
        return self.client.post(
            f'/api/preparation/{preset_id}/facts/{fact_id}/decision',
            json={'revision': revision, 'decision': decision, 'text': text},
        )

    def test_stale_preset_blocks_new_accept_but_preserves_historical_replay(self):
        preset = self._create_preset()
        base = self._create_version(preset).json()
        data = prep.read_preparation(preset['id'])
        accepted_id, pending_id = uuid.uuid4().hex, uuid.uuid4().hex
        data['suggestions'] = {
            suggestion_id: {
                'id': suggestion_id, 'source_version_id': base['current_version_id'],
                'target': target, 'replacement': replacement, 'status': 'pending',
            }
            for suggestion_id, target, replacement in (
                (accepted_id, 'SQL', 'SQL 查询'), (pending_id, 'Python', 'Python 脚本'),
            )
        }
        prep.write_preparation(preset['id'], data)
        accepted = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{accepted_id}/accept",
            json={'revision': base['revision']},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(self.client.put(
            f"/api/presets/{preset['id']}", json={**preset, 'jd': '更新后的岗位：开发数据平台。'},
        ).status_code, 200)
        path = prep.preparation_path(preset['id'])
        before = path.read_bytes()
        denied = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{pending_id}/accept",
            json={'revision': accepted.json()['revision']},
        )
        self.assertEqual(denied.status_code, 409)
        self.assertEqual(path.read_bytes(), before)
        stale = prep.get_preparation_view(preset['id'])
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['current_version_id'], accepted.json()['current_version_id'])
        replay = self.client.post(
            f"/api/preparation/{preset['id']}/suggestions/{accepted_id}/accept",
            json={'revision': base['revision']},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()['reused'])
        self.assertEqual(replay.json()['version']['id'], accepted.json()['version']['id'])
        self.assertEqual(replay.json()['current_version_id'], accepted.json()['current_version_id'])
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(prep.get_preparation_view(preset['id'])['stale'])

    def _pending_fact(self, preset):
        return self._ingest_fact(
            preset['id'], prep.get_preparation_view(preset['id'])['revision'], '我参与过一次重构。',
            {'session_id': 'aabbccdd', 'question_id': 'q-1', 'attempt': 1,
             'quote': '我参与过一次重构。'},
        )

    def test_confirm_grandchild_supersedes_entire_fact_ancestry(self):
        preset = self._create_preset()
        original = self._pending_fact(preset)
        confirmed = self._decide_fact(preset['id'], original['id'])
        self.assertEqual(confirmed.status_code, 200)
        child = self._decide_fact(preset['id'], original['id'], text='我负责过一次重构。').json()
        grandchild = self._decide_fact(preset['id'], child['id'], text='我完成过一次重构。').json()
        self.assertEqual(child['status'], 'pending')
        self.assertEqual(grandchild['status'], 'pending')
        self.assertEqual(
            [fact['id'] for fact in prep.get_preparation_view(preset['id'])['evidence_facts']],
            [original['id']],
        )
        result = self._decide_fact(preset['id'], grandchild['id'])
        self.assertEqual(result.status_code, 200)
        view = prep.get_preparation_view(preset['id'])
        self.assertEqual([fact['id'] for fact in view['evidence_facts']], [grandchild['id']])
        self.assertEqual(len(view['facts']), 3)
        self.assertEqual(view['evidence_facts'][0]['source_quote'], original['source_quote'])
        for ancestor in (original, child):
            before = prep.preparation_path(preset['id']).read_bytes()
            self.assertEqual(self._decide_fact(preset['id'], ancestor['id']).status_code, 409)
            self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before)

    def test_fact_confirmation_permutations_never_reactivate_an_ancestor(self):
        for order in permutations(range(3)):
            with self.subTest(order=order):
                preset = self._create_preset()
                root = self._pending_fact(preset)
                child = self._decide_fact(preset['id'], root['id'], text='修订一。').json()
                leaf = self._decide_fact(preset['id'], child['id'], text='修订二。').json()
                facts = (root, child, leaf)
                latest_depth = -1
                for depth in order:
                    before = prep.preparation_path(preset['id']).read_bytes()
                    response = self._decide_fact(preset['id'], facts[depth]['id'])
                    if depth < latest_depth:
                        self.assertEqual(response.status_code, 409)
                        self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before)
                    else:
                        self.assertEqual(response.status_code, 200)
                        latest_depth = depth
                    self.assertEqual(
                        [fact['id'] for fact in prep.get_preparation_view(preset['id'])['evidence_facts']],
                        [facts[latest_depth]['id']],
                    )

    def test_confirming_sibling_and_its_descendants_conflicts_with_effective_fact(self):
        for winning_branch in (0, 1):
            with self.subTest(winning_branch=winning_branch):
                preset = self._create_preset()
                root = self._pending_fact(preset)
                siblings = [
                    self._decide_fact(preset['id'], root['id'], text=f'分支{index}。').json()
                    for index in range(2)
                ]
                leaves = [
                    self._decide_fact(preset['id'], sibling['id'], text=f'分支{index}修订。').json()
                    for index, sibling in enumerate(siblings)
                ]
                winner = siblings[winning_branch]
                self.assertEqual(self._decide_fact(preset['id'], winner['id']).status_code, 200)
                for loser in (siblings[1 - winning_branch], leaves[1 - winning_branch]):
                    before = prep.preparation_path(preset['id']).read_bytes()
                    self.assertEqual(self._decide_fact(preset['id'], loser['id']).status_code, 409)
                    self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before)
                newer = leaves[winning_branch]
                self.assertEqual(self._decide_fact(preset['id'], newer['id']).status_code, 200)
                self.assertEqual(
                    [fact['id'] for fact in prep.get_preparation_view(preset['id'])['evidence_facts']],
                    [newer['id']],
                )

    def test_reject_with_edited_text_targets_original_and_replay_is_noop(self):
        preset = self._create_preset()
        original = self._pending_fact(preset)
        self.assertEqual(self._decide_fact(preset['id'], original['id']).status_code, 200)
        edited = self._decide_fact(preset['id'], original['id'], text='编辑后的文字。').json()
        before_reject = prep.get_preparation_view(preset['id'])['revision']
        rejected = self._decide_fact(
            preset['id'], original['id'], decision='reject', text=edited['text'],
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()['id'], original['id'])
        self.assertEqual(rejected.json()['status'], 'rejected')
        self.assertEqual(rejected.json()['revision'], before_reject + 1)
        self.assertEqual(prep.get_preparation_view(preset['id'])['evidence_facts'], [])
        before_replay = prep.preparation_path(preset['id']).read_bytes()
        replay = self._decide_fact(
            preset['id'], original['id'], decision='reject', text=edited['text'], revision=before_reject,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['id'], original['id'])
        self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before_replay)
        self.assertEqual(prep.read_preparation(preset['id'])['facts'][edited['id']]['status'], 'pending')

    def test_reediting_rejected_child_creates_pending_not_false_confirmation(self):
        preset = self._create_preset()
        original = self._pending_fact(preset)
        child = self._decide_fact(preset['id'], original['id'], text='编辑后的文字。').json()
        self.assertEqual(self._decide_fact(preset['id'], child['id'], decision='reject').status_code, 200)
        previous_revision = prep.get_preparation_view(preset['id'])['revision']
        reedited = self._decide_fact(preset['id'], original['id'], text=child['text'])
        self.assertEqual(reedited.status_code, 200)
        self.assertEqual(reedited.json()['status'], 'pending')
        self.assertNotEqual(reedited.json()['id'], child['id'])
        self.assertEqual(reedited.json()['revision'], previous_revision + 1)
        self.assertEqual(prep.read_preparation(preset['id'])['facts'][child['id']]['status'], 'rejected')
        before = prep.preparation_path(preset['id']).read_bytes()
        replay = self._decide_fact(
            preset['id'], original['id'], text=child['text'], revision=previous_revision,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['id'], reedited.json()['id'])
        self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before)

    def test_extract_long_session_answer_keeps_full_input_and_deduplicates_exact_quote_without_revision_change(self):
        preset = self._create_preset()
        version = self._create_version(preset).json()['current_version_id']
        session_id = self._end_session_and_get_id(preset['id'], version)
        session = store.get_session(session_id)
        long_answer = '甲' * prep.MAX_FACT_TEXT + '乙'
        session['turns'][0]['answer'] = long_answer
        store.update_session(session_id, {'turns': session['turns']})
        quote = long_answer[-20:]
        async def remote(**kwargs):
            payload = json.loads(kwargs['messages'][1]['content'])
            self.assertEqual(payload['turns'][0]['answer'], long_answer)
            return json.dumps({'facts':[{'question_id':'q-1','attempt':1,'quote':quote,'text':quote}]})
        async def extract():
            started = preparation_tasks.create_session_material_task(preset['id'], session_id=session_id, revision=prep.get_preparation_view(preset['id'])['revision'])
            for _ in range(100):
                first = preparation_tasks.get_task(preset['id'], started['task']['id'])
                if first['task']['status'] != 'running':
                    break
                await asyncio.sleep(.001)
            self.assertEqual(first['task']['status'], 'completed')
            self.assertEqual(first['task']['result']['created_count'], 1)
            fact = prep.get_preparation_view(preset['id'])['facts'][0]
            self.assertEqual(fact['text'], quote)
            self.assertEqual(fact['source_quote'], quote)
            before = prep.preparation_path(preset['id']).read_bytes()
            second = preparation_tasks.create_session_material_task(preset['id'], session_id=session_id, revision=first['revision'])
            self.assertEqual(second['task']['id'], first['task']['id'])
            self.assertEqual(second['revision'], first['revision'])
            self.assertEqual(len(prep.get_preparation_view(preset['id'])['facts']), 1)
            self.assertEqual(prep.preparation_path(preset['id']).read_bytes(), before)
        with patch.object(config, 'get_connection', return_value={'base_url':'https://models.example/v1', 'api_key':'synthetic-placeholder', 'protocol':'openai'}), \
             patch.object(preparation_tasks, 'chat_once', side_effect=remote) as model:
            asyncio.run(extract())
        self.assertEqual(model.call_count, 1)


if __name__ == '__main__':
    unittest.main()
