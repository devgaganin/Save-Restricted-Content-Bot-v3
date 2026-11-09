"""Offline batch state-machine tests; Telegram and MongoDB are simulated."""
import asyncio
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch_queue as queue


def job(**extra):
    value = {'_id': 123, 'job_id': 'job1', 'source': 'channel', 'destination': '-100222/4',
             'link_type': 'public', 'pending': [10, 11], 'failed_ids': [], 'total': 2,
             'current': 0, 'success': 0, 'failed': 0, 'skipped': 0, 'uncertain': 0,
             'status': 'running', 'status_message_id': 9}
    value.update(extra)
    return value


class FakeCollection:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value)

    async def find_one(self, query):
        if self.value is None:
            return None
        for key, value in query.items():
            if self.value.get(key) != value:
                return None
        return copy.deepcopy(self.value)

    async def replace_one(self, selector, value, upsert=False):
        self.value = copy.deepcopy(value)
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def update_one(self, selector, update, **kwargs):
        if self.value is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for key, value in selector.items():
            actual = self.value.get(key)
            if isinstance(value, dict) and '$in' in value:
                if actual not in value['$in']:
                    return SimpleNamespace(matched_count=0, modified_count=0)
            elif actual != value:
                return SimpleNamespace(matched_count=0, modified_count=0)
        for key, value in update.get('$set', {}).items():
            self.value[key] = value
        for key in update.get('$unset', {}):
            self.value.pop(key, None)
        for key, value in update.get('$inc', {}).items():
            self.value[key] = self.value.get(key, 0) + value
        for key, value in update.get('$pull', {}).items():
            self.value[key] = [v for v in self.value[key] if v != value]
        for key, value in update.get('$push', {}).items():
            self.value.setdefault(key, []).append(value)
        return SimpleNamespace(matched_count=1, modified_count=1)


class BatchQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        queue.locks.clear()

    async def test_worker_checkpoints_success_and_failure(self):
        db = FakeCollection(job())
        message = SimpleNamespace()
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, return_value=message), patch.object(queue.batch, 'process_msg', new_callable=AsyncMock, side_effect=['Done.', 'Failed.']) as process, patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'update_batch_progress', new_callable=AsyncMock), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'edit_message_text', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock), patch.object(queue.asyncio, 'sleep', new_callable=AsyncMock):
            await queue.run_job(123, 'job1', object(), object())
        self.assertEqual(db.value['status'], 'completed')
        self.assertEqual(db.value['success'], 1)
        self.assertEqual(db.value['failed_ids'], [11])
        self.assertEqual(db.value['current'], 2)
        self.assertNotIn('inflight', db.value)
        self.assertEqual(process.await_args.kwargs['destination_override'], '-100222/4')

    async def test_checkpoint_precedes_fetch_and_send(self):
        db = FakeCollection(job(pending=[10], total=1))
        async def fetch(*args):
            self.assertEqual(db.value.get('inflight'), 10)
            self.assertRegex(db.value.get('inflight_nonce', ''), r'^[0-9a-f]{16}$')
            self.assertEqual(db.value['pending'], [10])
            return object()
        async def send(*args, **kwargs):
            self.assertEqual(db.value.get('inflight'), 10)
            return 'Sent.'
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', side_effect=fetch), patch.object(queue.batch, 'process_msg', side_effect=send), patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'update_batch_progress', new_callable=AsyncMock), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'edit_message_text', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock):
            await queue.run_job(123, 'job1', object(), object())
        self.assertEqual(db.value['status'], 'completed')
        self.assertNotIn('inflight', db.value)

    async def test_cancel_during_delivery_keeps_inflight_for_review(self):
        db = FakeCollection(job())
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, return_value=SimpleNamespace()), patch.object(queue.batch, 'process_msg', new_callable=AsyncMock, side_effect=asyncio.CancelledError), patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock):
            with self.assertRaises(asyncio.CancelledError):
                await queue.run_job(123, 'job1', object(), object())
        self.assertEqual(db.value['status'], 'interrupted')
        self.assertEqual(db.value['inflight'], 10)
        self.assertEqual(db.value['pending'], [10, 11])

    async def test_resume_requires_explicit_inflight_choice(self):
        db = FakeCollection(job(status='interrupted', inflight=10))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True):
            ok, reason = await queue.resume(123, SimpleNamespace(id=99))
        self.assertFalse(ok)
        self.assertEqual(reason, 'uncertain')
        self.assertEqual(db.value['pending'], [10, 11])

    async def test_resume_skip_preserves_other_pending_items(self):
        db = FakeCollection(job(status='interrupted', inflight=10))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, _ = await queue.resume(123, SimpleNamespace(id=99), 'skip', 'job1', 10, 'legacy')
        self.assertTrue(ok)
        self.assertEqual(db.value['pending'], [11])
        self.assertEqual(db.value['uncertain'], 1)
        self.assertEqual(db.value['current'], 1)
        self.assertNotIn('inflight', db.value)
        launch.assert_awaited_once()

    async def test_retry_only_failed_items(self):
        db = FakeCollection(job(status='completed', pending=[], failed_ids=[11, 15], failed=2))
        archive = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'history', archive), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock):
            ok, _ = await queue.retry_failed(123, SimpleNamespace(id=99), 'job1', queue.failed_fingerprint([11, 15]))
        self.assertTrue(ok)
        self.assertEqual(db.value['pending'], [11, 15])
        self.assertEqual(db.value['total'], 2)
        self.assertEqual(archive.value['user_id'], 123)

    async def test_checkpoint_failure_never_sends(self):
        class FailingClaim(FakeCollection):
            async def update_one(self, selector, update, **kwargs):
                if 'inflight' in update.get('$set', {}):
                    raise RuntimeError('database offline')
                return await super().update_one(selector, update, **kwargs)
        db = FailingClaim(job())
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock) as fetch, patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock), self.assertLogs(queue.log, level='ERROR'):
            await queue.run_job(123, 'job1', object(), object())
        fetch.assert_not_awaited()
        self.assertEqual(db.value['status'], 'interrupted')
        self.assertEqual(db.value['pending'], [10, 11])

    async def test_post_send_checkpoint_failure_keeps_inflight(self):
        class FailingCheckpoint(FakeCollection):
            async def update_one(self, selector, update, **kwargs):
                if '$pull' in update:
                    raise RuntimeError('database offline')
                return await super().update_one(selector, update, **kwargs)
        db = FailingCheckpoint(job())
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'get_msg', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'process_msg', new_callable=AsyncMock, return_value='Sent.'), patch.object(queue.batch, 'should_cancel', return_value=False), patch.object(queue.batch, 'get_batch_info', return_value={}), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock), self.assertLogs(queue.log, level='ERROR'):
            await queue.run_job(123, 'job1', object(), object())
        self.assertEqual(db.value['status'], 'interrupted')
        self.assertEqual(db.value['inflight'], 10)
        self.assertEqual(db.value['pending'], [10, 11])

    async def test_concurrent_starts_do_not_replace_unfinished(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'get_user_data_key', new_callable=AsyncMock, return_value='-100222/4'), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            results = await asyncio.gather(queue.start_new(123, 'source', 1, 2, 'public', SimpleNamespace(id=99)), queue.start_new(123, 'other', 5, 2, 'public', SimpleNamespace(id=100)))
        self.assertEqual(sum(ok for ok, _ in results), 1)
        self.assertEqual(db.value['source'], 'source')
        self.assertEqual(db.value['destination'], '-100222/4')
        launch.assert_awaited_once()

    async def test_plan_limit_is_checked_before_batch_creation_or_resume(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'FREEMIUM_LIMIT', 1), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=False), patch.object(queue.batch, 'is_user_active', return_value=False):
            ok, _ = await queue.start_new(123, 'source', 1, 2, 'public', SimpleNamespace(id=99))
            self.assertFalse(ok)
            self.assertIsNone(db.value)
            db.value = job(status='interrupted')
            ok, _ = await queue.resume(123, SimpleNamespace(id=99))
            self.assertFalse(ok)
            self.assertEqual(db.value['status'], 'interrupted')

    async def test_explicit_retry_keeps_item_pending(self):
        db = FakeCollection(job(status='interrupted', inflight=10))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock):
            ok, _ = await queue.resume(123, SimpleNamespace(id=99), 'retry', 'job1', 10, 'legacy')
        self.assertTrue(ok)
        self.assertEqual(db.value['pending'], [10, 11])
        self.assertNotIn('inflight', db.value)

    async def test_cancel_interrupted_batch_retains_uncertain_item(self):
        db = FakeCollection(job(status='interrupted', inflight=10))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False):
            self.assertTrue(await queue.cancel_saved(123))
        self.assertEqual(db.value['status'], 'cancelled')
        self.assertEqual(db.value['inflight'], 10)

    async def test_cancelled_input_does_not_start(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, _ = await queue.start_new(123, 'source', 1, 1, 'public', SimpleNamespace(id=99), still_wanted=lambda: False)
        self.assertFalse(ok)
        self.assertIsNone(db.value)
        launch.assert_not_awaited()

    async def test_old_uncertain_choice_cannot_apply_to_new_item_or_job(self):
        db = FakeCollection(job(status='interrupted', inflight=11))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock) as bot, patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, reason = await queue.resume(123, SimpleNamespace(id=99), 'retry', 'job1', 10, 'legacy')
            self.assertFalse(ok)
            self.assertIn('outdated', reason)
            ok, _ = await queue.resume(123, SimpleNamespace(id=99), 'skip', 'different', 11, 'legacy')
            self.assertFalse(ok)
        bot.assert_not_awaited()
        launch.assert_not_awaited()
        self.assertEqual(db.value['inflight'], 11)
        self.assertEqual(db.value['pending'], [10, 11])

    async def test_old_choice_for_same_item_cannot_apply_to_new_attempt(self):
        db = FakeCollection(job(status='interrupted', inflight=10, inflight_nonce='b' * 16))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, reason = await queue.resume(123, SimpleNamespace(id=99), 'retry', 'job1', 10, 'a' * 16)
        self.assertFalse(ok)
        self.assertIn('outdated', reason)
        self.assertEqual(db.value['inflight_nonce'], 'b' * 16)
        launch.assert_not_awaited()

    async def test_cannot_retry_failures_while_cancelled_job_has_pending_items(self):
        db = FakeCollection(job(status='cancelled', pending=[11], failed_ids=[10]))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock) as bot:
            ok, reason = await queue.retry_failed(123, SimpleNamespace(id=99), 'job1', queue.failed_fingerprint([10]))
        self.assertFalse(ok)
        self.assertIn('remaining', reason)
        self.assertEqual(db.value['pending'], [11])
        bot.assert_not_awaited()

    async def test_failed_retry_confirmation_is_bound_to_job_and_failed_items(self):
        db = FakeCollection(job(status='completed', pending=[], failed_ids=[11, 15]))
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock) as bot, patch.object(queue, 'launch', new_callable=AsyncMock) as launch:
            ok, _ = await queue.retry_failed(123, SimpleNamespace(id=99), 'old-job', queue.failed_fingerprint([11, 15]))
            self.assertFalse(ok)
            ok, _ = await queue.retry_failed(123, SimpleNamespace(id=99), 'job1', queue.failed_fingerprint([11]))
            self.assertFalse(ok)
        bot.assert_not_awaited()
        launch.assert_not_awaited()
        self.assertEqual(db.value['failed_ids'], [11, 15])

    async def test_invalid_message_range_does_not_create_batch(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock) as bot:
            ok, _ = await queue.start_new(123, 'source', queue.MAX_MESSAGE_ID, 2, 'public', SimpleNamespace(id=99))
        self.assertFalse(ok)
        self.assertIsNone(db.value)
        bot.assert_not_awaited()

    def test_message_link_parsing_rejects_oversized_ids(self):
        from utils.func import E
        self.assertEqual(E('https://t.me/example/10'), ('example', 10, 'public'))
        self.assertEqual(E('https://t.me/c/123456/4/99'), ('-100123456', 99, 'private'))
        self.assertEqual(E('https://t.me/example/10?single'), ('example', 10, 'public'))
        self.assertEqual(E('https://t.me/example/10/not-a-post'), (None, None, None))
        self.assertEqual(E('https://t.me/example/2147483648'), (None, None, None))
        self.assertEqual(E('https://t.me/c/123456/' + '9' * 50), (None, None, None))
        self.assertEqual(E('https://t.me/example/' + '9' * 5000), (None, None, None))

    async def test_startup_marks_running_jobs_interrupted(self):
        db = SimpleNamespace(update_many=AsyncMock())
        with patch.object(queue, 'jobs', db):
            await queue.run_batch_queue_plugin()
        selector, update = db.update_many.await_args.args
        self.assertIn('running', selector['status']['$in'])
        self.assertEqual(update['$set']['status'], 'interrupted')
        self.assertNotIn('$unset', update)

    async def test_shutdown_cancels_and_awaits_workers(self):
        started = asyncio.Event()
        stopped = asyncio.Event()
        async def worker():
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                stopped.set()
        task = asyncio.create_task(worker())
        await started.wait()
        with patch.object(queue, 'workers', {123: task}):
            await queue.shutdown_workers()
        self.assertTrue(task.cancelled())
        self.assertTrue(stopped.is_set())

    async def test_login_in_progress_blocks_new_and_resumed_jobs(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'get_user_step', return_value=1):
            ok, _ = await queue.start_new(123, 'source', 1, 1, 'public', SimpleNamespace(id=99))
            self.assertFalse(ok)
            db.value = job(status='interrupted')
            ok, _ = await queue.resume(123, SimpleNamespace(id=99))
            self.assertFalse(ok)
            ok, _ = await queue.retry_failed(123, SimpleNamespace(id=99))
            self.assertFalse(ok)

    async def test_locks_are_isolated_by_user(self):
        self.assertIs(queue.lock_for(123), queue.lock_for(123))
        self.assertIsNot(queue.lock_for(123), queue.lock_for(456))

    async def test_direct_send_error_does_not_trigger_fallback_upload(self):
        media = SimpleNamespace(photo=SimpleNamespace(file_id='file'), video=None, video_note=None,
                                voice=None, sticker=None, audio=None, document=None, text=None,
                                media=True, caption=None)
        bot = SimpleNamespace(send_photo=AsyncMock(side_effect=RuntimeError('network error')), send_message=AsyncMock())
        user = SimpleNamespace(download_media=AsyncMock())
        with patch.object(queue.batch, 'get_user_data_key', new_callable=AsyncMock, side_effect=lambda uid, key, default=None: default), patch.object(queue.batch, 'process_text_with_rules', new_callable=AsyncMock, return_value=''):
            result = await queue.batch.process_msg(bot, user, media, '123', 'public', 123, 'source')
        self.assertTrue(result.startswith('Failed'))
        user.download_media.assert_not_awaited()
        bot.send_message.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
