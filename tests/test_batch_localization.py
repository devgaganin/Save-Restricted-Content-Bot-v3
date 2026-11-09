"""Offline tests for localized batch prompts and safety-sensitive task controls."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue as queue, localization, menu
from utils.custom_filters import user_steps
from test_batch_queue import FakeCollection, job


def message(text=''):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), chat=SimpleNamespace(id=123),
                           text=text, reply_text=AsyncMock(return_value=SimpleNamespace(id=99, edit=AsyncMock())),
                           reply=AsyncMock())


def query(action):
    return SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=123, type=SimpleNamespace(value='private')),
                                                   edit_text=AsyncMock(), reply_text=AsyncMock()),
                           from_user=SimpleNamespace(id=123), data='ui:' + action, answer=AsyncMock())


def buttons(markup):
    return [(button.text, button.callback_data) for row in markup.inline_keyboard for button in row]


class BatchLocalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        queue.locks.clear()
        queue.workers.clear()
        batch.Z.clear()
        batch.ACTIVE_USERS.clear()
        menu.waiting.clear()
        menu.busy_inputs.clear()
        user_steps.clear()

    async def test_begin_topic_in_hindi_preserves_link_and_scan_warning(self):
        msg = message()
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(batch, 'FREEMIUM_LIMIT', 5), patch.object(batch, 'sub', new_callable=AsyncMock, return_value=0), patch.object(queue, 'has_unfinished', new_callable=AsyncMock, return_value=False), patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()):
            await batch.begin_processing(None, msg, 123, 'topic_batch')
        self.assertIn('जाँच', msg.reply_text.await_args_list[0].args[0])
        edited = msg.reply_text.return_value.edit.await_args
        self.assertIn('टॉपिक और पोस्ट दोनों', edited.args[0])
        self.assertIn('https://t.me/c/123456/12/99', edited.args[0])
        self.assertIn(('❌ रद्द करें', 'ui:cancel'), buttons(edited.kwargs['reply_markup']))
        self.assertEqual(batch.Z[123]['language'], 'hi')
        self.assertTrue(batch.Z[123]['topic_mode'])

    async def test_hindi_topic_count_and_validation_preserve_flow(self):
        batch.Z[123] = {'step': 'start', 'topic_mode': True, 'language': 'hi'}
        with patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()):
            invalid = message('https://t.me/c/123456/99')
            await batch.text_handler(None, invalid)
            self.assertIn('दोनों की ID', invalid.reply_text.await_args.args[0])
            valid = message('https://t.me/c/123456/4/99')
            await batch.text_handler(None, valid)
            self.assertEqual(batch.Z[123]['source_topic'], 4)
            self.assertIn('कॉपी होने वाली पोस्ट की संख्या नहीं', valid.reply_text.await_args.args[0])
            bad_count = message('१०')
            await batch.text_handler(None, bad_count)
            self.assertIn('अंग्रेज़ी अंक', bad_count.reply_text.await_args.args[0])
            self.assertEqual(batch.Z[123]['step'], 'count')

    async def test_batch_start_passes_language_and_translates_reason(self):
        batch.Z[123] = {'step': 'count', 'language': 'hi', 'cid': 'source', 'sid': 10, 'lt': 'public', 'source_topic': None}
        msg = message('3')
        with patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(batch, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue, 'start_new', new_callable=AsyncMock, return_value=(False, 'Invalid message range.')) as start:
            await batch.text_handler(None, msg)
        self.assertEqual(start.await_args.kwargs['language'], 'hi')
        self.assertIn('सीमा मान्य नहीं', msg.reply_text.return_value.edit.await_args.args[0])
        self.assertNotIn(123, batch.Z)

    async def test_job_language_is_bounded_and_retry_keeps_snapshot(self):
        db = FakeCollection()
        with patch.object(queue, 'jobs', db), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'get_user_data_key', new_callable=AsyncMock, return_value='123'), patch.object(queue, 'launch', new_callable=AsyncMock):
            ok, _ = await queue.start_new(123, 'source', 10, 1, 'public', SimpleNamespace(id=99), language='hi')
        self.assertTrue(ok)
        self.assertEqual(db.value['language'], 'hi')
        db = FakeCollection(job(status='completed', pending=[], failed_ids=[10], language='hi'))
        with patch.object(queue, 'jobs', db), patch.object(queue, 'history', FakeCollection()), patch.object(queue, 'is_premium_user', new_callable=AsyncMock, return_value=True), patch.object(queue.batch, 'is_user_active', return_value=False), patch.object(queue.batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(queue.batch, 'get_uclient', new_callable=AsyncMock, return_value=object()), patch.object(queue, 'launch', new_callable=AsyncMock):
            ok, _ = await queue.retry_failed(123, SimpleNamespace(id=99), 'job1', queue.failed_fingerprint([10]))
        self.assertTrue(ok)
        self.assertEqual(db.value['language'], 'hi')

    async def test_completion_message_and_controls_use_saved_language(self):
        with patch.object(queue.app, 'send_message', new_callable=AsyncMock) as send:
            await queue.notify(123, job(language='hi', pending=[], failed_ids=[10]), '✅ Batch completed.')
        self.assertIn('बैच पूरा', send.await_args.args[1])
        self.assertIn('अनिश्चित, छोड़ने का चयन', send.await_args.args[1])
        self.assertIn(('🔁 असफल फिर चलाएँ', 'ui:retry_failed'), buttons(send.await_args.kwargs['reply_markup']))
        keyboard = queue.progress_keyboard('hi')
        self.assertIn(('⏸️ रोकें', 'ui:pause'), buttons(keyboard))
        self.assertIn(('🛑 टास्क रोकें', 'ui:stop'), buttons(keyboard))

    async def test_hindi_task_status_shows_uncertain_and_keeps_actions(self):
        q = query('task')
        entry = job(status='interrupted', inflight=10, source_topic=4)
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value=entry), patch.object(batch, 'is_user_active', return_value=False):
            await menu.menu_callback(None, q)
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('बाधित', text)
        self.assertIn('डिलीवरी का परिणाम अज्ञात', text)
        self.assertIn('स्रोत टॉपिक', text)
        self.assertIn(('▶️ फिर शुरू करें', 'ui:resume'), buttons(q.message.edit_text.await_args.kwargs['reply_markup']))

    async def test_hindi_uncertain_choices_warn_both_duplicate_and_omission(self):
        q = query('resume')
        entry = {'job_id': 'a' * 24, 'inflight': 42, 'inflight_nonce': 'b' * 16}
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(batch, 'is_user_active', return_value=False), patch.object(queue, 'resume', new_callable=AsyncMock, return_value=(False, 'uncertain')), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value=entry):
            await menu.menu_callback(None, q)
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('डुप्लिकेट', text)
        self.assertIn('संदेश छूट सकता है', text)
        labels = buttons(q.message.edit_text.await_args.kwargs['reply_markup'])
        self.assertIn(('⏭️ आइटम छोड़ें', f"ui:ri:s:{'a'*24}:42:{'b'*16}"), labels)
        self.assertIn(('🔁 आइटम फिर भेजें', f"ui:ri:r:{'a'*24}:42:{'b'*16}"), labels)

    async def test_hindi_failed_retry_keeps_duplicate_warning_and_fingerprint(self):
        q = query('retry_failed')
        entry = {'job_id': 'a' * 24, 'pending': [], 'failed_ids': [2, 3]}
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value=entry):
            await menu.menu_callback(None, q)
        self.assertIn('डुप्लिकेट', q.message.edit_text.await_args.args[0])
        self.assertIn(('✅ फिर कोशिश करें', f"ui:rf:{'a'*24}:{queue.failed_fingerprint([2,3])}"), buttons(q.message.edit_text.await_args.kwargs['reply_markup']))

    async def test_stop_is_requested_before_language_lookup(self):
        q = query('stop')
        stopped = False
        async def do_stop(uid):
            nonlocal stopped
            stopped = True
            return True
        async def lang(uid):
            self.assertTrue(stopped)
            return 'hi'
        with patch.object(batch, 'is_user_active', return_value=True), patch.object(batch, 'request_batch_cancel', side_effect=do_stop), patch.object(localization, 'language_for', side_effect=lang):
            await menu.menu_callback(None, q)
        self.assertIn('मौजूदा फ़ाइल पहले पूरी हो सकती है', q.message.edit_text.await_args.args[0])

    async def test_failed_initial_job_read_still_records_interruption(self):
        db = SimpleNamespace(find_one=AsyncMock(side_effect=RuntimeError('read failed')), update_one=AsyncMock())
        with patch.object(queue, 'jobs', db), patch.object(queue.batch, 'remove_active_batch', new_callable=AsyncMock), patch.object(queue.app, 'send_message', new_callable=AsyncMock) as send, self.assertLogs(queue.log, level='ERROR'):
            await queue.run_job(123, 'job1', object(), object())
        db.update_one.assert_awaited_once()
        self.assertIn('interrupted', send.await_args.args[1])

if __name__ == '__main__':
    unittest.main()
