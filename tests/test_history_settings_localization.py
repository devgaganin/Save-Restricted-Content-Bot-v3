"""Offline Hindi history/settings tests, including destructive-action safety."""
import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from pyrogram.parser.html import HTML
from plugins import batch, batch_history as history, batch_queue as queue, localization, menu, settings_clear, settings_view
from test_history_settings import HistoryCollection, doc
from utils.custom_filters import user_steps


def message():
    return SimpleNamespace(chat=SimpleNamespace(id=123, type=SimpleNamespace(value='private')),
                           edit_text=AsyncMock(), reply_text=AsyncMock())


def query(action):
    return SimpleNamespace(message=message(), from_user=SimpleNamespace(id=123),
                           data='ui:' + action, answer=AsyncMock())


def buttons(msg):
    return [(button.text, button.callback_data) for row in msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for button in row]


class HindiHistorySettingsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        queue.locks.clear()
        batch.Z.clear()
        menu.waiting.clear()
        menu.busy_inputs.clear()
        history.confirmations.clear()
        settings_clear.confirmations.clear()
        user_steps.clear()

    async def check_html(self, msg):
        # Exercise the actual installed Pyrogram HTML parser, not only text matching.
        await HTML(None).parse(msg.edit_text.await_args.args[0])

    async def test_hindi_history_is_scoped_and_paginates_without_callback_changes(self):
        db = HistoryCollection([doc(i) for i in range(1, 8)] + [doc(9, 456)])
        msg = message()
        with patch.object(queue, 'history', db), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': doc(7)['_id']}):
            await history.show_history(msg, 123, 0, 'hi')
        text = msg.edit_text.await_args.args[0]
        self.assertIn('केवल नवीनतम 1,005', text)
        self.assertIn('सिर्फ़ देखे', text)
        self.assertEqual(db.last_query['user_id'], 123)
        self.assertEqual(db.last_query['_id'], {'$ne': doc(7)['_id']})
        self.assertIn(('अगला ➡️', 'ui:history_page:1'), buttons(msg))
        self.assertIn(('🗑️ इतिहास हटाएँ', 'ui:history_clear'), buttons(msg))
        self.assertTrue(all(len(callback.encode()) <= 64 for _, callback in buttons(msg)))
        await self.check_html(msg)

    async def test_hindi_detail_is_private_and_escapes_html(self):
        foreign = doc(1, 456)
        mine = doc(2, 123)
        mine.update(source='<script>', destination='<b>dest</b>', status='<bad>')
        db = HistoryCollection([foreign, mine])
        msg = message()
        with patch.object(queue, 'history', db):
            await history.show_detail(msg, 123, foreign['_id'], 'hi')
            self.assertIn('उपलब्ध नहीं', msg.edit_text.await_args.args[0])
            await history.show_detail(msg, 123, mine['_id'], 'hi')
        text = msg.edit_text.await_args.args[0]
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('&lt;b&gt;dest&lt;/b&gt;', text)
        self.assertIn('&lt;bad&gt;', text)
        self.assertNotIn('<script>', text)
        self.assertIn('सिर्फ़ देखे जा सकते', text)
        await self.check_html(msg)

    async def test_hindi_clear_history_warns_and_preserves_current(self):
        db = HistoryCollection([doc(1), doc(2), doc(3, 456)])
        q = query('history_clear')
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(queue, 'history', db), patch.object(queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': doc(2)['_id']}):
            await menu.menu_callback(None, q)
            text = q.message.edit_text.await_args.args[0]
            self.assertIn('पुष्टि से पहले', text)
            self.assertIn('मौजूदा टास्क', text)
            self.assertIn('बनी रहेगी', text)
            await self.check_html(q.message)
            token = history.confirmations[123][0]
            self.assertIn(('✅ इतिहास हटाएँ', 'ui:history_clear_yes:' + token), buttons(q.message))
            self.assertIn(('❌ रद्द करें', 'ui:history'), buttons(q.message))
            q.data = 'ui:history_clear_yes:' + token
            await menu.menu_callback(None, q)
            self.assertIn('1 आर्काइव रिकॉर्ड हटाए', q.message.edit_text.await_args.args[0])
            self.assertEqual({item['_id'] for item in db.docs}, {doc(2)['_id'], doc(3)['_id']})
            await menu.menu_callback(None, q)
            self.assertIn('पुष्टि की अवधि समाप्त', q.message.edit_text.await_args.args[0])

    async def test_hindi_history_callback_checks_owner_chat(self):
        q = query('history')
        q.from_user.id = 456
        with patch.object(history, 'show_history', new_callable=AsyncMock) as show:
            await menu.menu_callback(None, q)
        show.assert_not_awaited()
        self.assertTrue(q.answer.await_args.kwargs['show_alert'])

    async def test_settings_preview_uses_one_safe_projected_read(self):
        msg = message()
        data = {'language': 'hi', 'caption': '<b>नमस्ते</b>', 'prefix': 'x' * 200,
                'bot_token': 'SECRET', 'session_string': 'CREDENTIAL', 'remove_caption': True,
                'delete_words': ['a', 'b'], 'replacement_words': {'x': 'y'}}
        with patch.object(settings_view.users_collection, 'find_one', new_callable=AsyncMock, return_value=data) as find, patch.object(settings_view.os.path, 'isfile', return_value=False):
            await settings_view.show_settings(msg, 123)
        find.assert_awaited_once()
        selector, projection = find.await_args.args
        self.assertEqual(selector, {'user_id': 123})
        self.assertEqual(projection['language'], 1)
        self.assertNotIn('session_string', projection)
        self.assertNotIn('bot_token', projection)
        text = msg.edit_text.await_args.args[0]
        self.assertIn('आपकी सेटिंग्स', text)
        self.assertIn('&lt;b&gt;नमस्ते&lt;/b&gt;', text)
        self.assertIn('हटाया जाएगा', text)
        self.assertNotIn('SECRET', text)
        self.assertNotIn('CREDENTIAL', text)
        self.assertIn('…', text)
        await self.check_html(msg)

    async def test_settings_choice_and_confirmation_warnings_are_hindi(self):
        msg = message()
        await settings_clear.show_choices(msg, 123, 'hi')
        text = msg.edit_text.await_args.args[0]
        self.assertIn('भुगतान विवरण नहीं बदलेंगे', text)
        self.assertIn('डेस्टिनेशन शुरू होने पर', text)
        self.assertIn(('कैप्शन', 'ui:setting_clear:caption'), buttons(msg))
        await self.check_html(msg)
        with patch.object(settings_clear.users_collection, 'find_one', new_callable=AsyncMock, return_value={'caption': 'old'}) as find:
            await settings_clear.ask_clear(msg, 123, 'caption', 'hi')
        find.assert_awaited_once_with({'user_id': 123}, {'caption': 1, '_id': 0})
        text = msg.edit_text.await_args.args[0]
        self.assertIn('केवल यह सेटिंग प्रभावित', text)
        self.assertIn('मान बदलने पर अनुरोध अस्वीकार', text)
        token = settings_clear.confirmations[123][0]
        self.assertIn(('✅ सेटिंग हटाएँ', 'ui:setting_clear_yes:' + token), buttons(msg))
        self.assertIn(('❌ रद्द करें', 'ui:settings_clear'), buttons(msg))
        await self.check_html(msg)

    async def test_changed_setting_still_cannot_be_cleared_using_hindi_confirmation(self):
        msg = message()
        captured = {}
        async def find(selector, projection):
            return {'caption': 'old'}
        async def update(selector, update):
            captured.update(selector)
            return SimpleNamespace(modified_count=0)
        with patch.object(settings_clear.users_collection, 'find_one', side_effect=find), patch.object(settings_clear.users_collection, 'update_one', side_effect=update):
            await settings_clear.ask_clear(msg, 123, 'caption', 'hi')
            token = settings_clear.confirmations[123][0]
            q = query('setting_clear_yes:' + token)
            with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
                await menu.menu_callback(None, q)
            self.assertIn('बदल गई है', q.message.edit_text.await_args.args[0])
            self.assertEqual(captured['user_id'], 123)
            self.assertEqual(captured['$and'][1]['caption']['$eq'], 'old')
            self.assertNotIn(123, settings_clear.confirmations)

    async def test_hindi_failed_clear_does_not_report_success(self):
        q = query('setting_clear_yes:' + 'a' * 16)
        with patch.object(settings_clear, 'clear', new_callable=AsyncMock, side_effect=RuntimeError('offline')), patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'), self.assertLogs(menu.logger, level='ERROR'):
            await menu.menu_callback(None, q)
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('पुष्टि नहीं हुई', text)
        self.assertNotIn('हटा दी गई', text)

    async def test_invalid_settings_do_not_read_sensitive_fields(self):
        msg = message()
        with patch.object(settings_clear.users_collection, 'find_one', new_callable=AsyncMock) as find:
            await settings_clear.ask_clear(msg, 123, 'bot_token', 'hi')
        find.assert_not_awaited()
        self.assertIn('मान्य नहीं', msg.edit_text.await_args.args[0])

if __name__ == '__main__':
    unittest.main()
