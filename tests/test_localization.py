"""Offline tests for per-user, partial Hindi/English localization."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, localization, menu
from utils import func
from utils.custom_filters import user_steps


def query(action, uid=123):
    msg = SimpleNamespace(chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                          reply_text=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(message=msg, from_user=SimpleNamespace(id=uid),
                           data='ui:' + action, answer=AsyncMock())


def buttons(markup):
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


class LocalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        batch_queue.locks.clear()
        batch.Z.clear()
        menu.waiting.clear()
        menu.busy_inputs.clear()
        menu.download_quality.clear()
        user_steps.clear()

    async def test_preference_read_is_user_scoped_and_credentials_excluded(self):
        with patch.object(localization.users_collection, 'find_one', new_callable=AsyncMock, return_value={'language': 'hi'}) as find:
            self.assertEqual(await localization.language_for(123), 'hi')
        find.assert_awaited_once_with({'user_id': 123}, {'language': 1, '_id': 0})

    async def test_unknown_absent_and_database_error_fallback_to_english(self):
        for value in (None, {}, {'language': 'xx'}, {'language': None}):
            with patch.object(localization.users_collection, 'find_one', new_callable=AsyncMock, return_value=value):
                self.assertEqual(await localization.language_for(123), 'en')
        with patch.object(localization.users_collection, 'find_one', new_callable=AsyncMock, side_effect=RuntimeError('database down')), self.assertLogs(localization.log, level='WARNING'):
            self.assertEqual(await localization.language_for(123), 'en')

    async def test_every_main_page_has_hindi_and_same_actions_as_english(self):
        self.assertEqual(set(localization.HI_PAGES), set(menu.PAGES))
        self.assertEqual(set(localization.HI_PROMPTS), set(menu.PROMPTS))
        for name in menu.PAGES:
            with self.subTest(name=name):
                en, hi = query('home').message, query('home').message
                await menu.render(en, name, language='en')
                await menu.render(hi, name, language='hi')
                a = en.reply_text.await_args
                b = hi.reply_text.await_args
                self.assertIn('<blockquote>', b.args[0])
                self.assertEqual([v for _, v in buttons(a.kwargs['reply_markup'])], [v for _, v in buttons(b.kwargs['reply_markup'])])
                self.assertTrue(all(len(v.encode()) <= 64 for _, v in buttons(b.kwargs['reply_markup'])))
                self.assertNotEqual(a.args[0], b.args[0])

    async def test_navigation_reads_saved_preference_for_correct_user(self):
        q = query('home', 124)
        with patch.object(localization.users_collection, 'find_one', new_callable=AsyncMock, return_value={'language': 'hi'}) as find:
            await menu.menu_callback(None, q)
        self.assertIn('नमस्ते', q.message.edit_text.await_args.args[0])
        self.assertIn(('⚙️ सेटिंग्स', 'ui:settings'), buttons(q.message.edit_text.await_args.kwargs['reply_markup']))
        self.assertEqual(find.await_args.args[0], {'user_id': 124})

    async def test_saved_choice_does_not_change_payment_or_other_fields(self):
        q = query('language_hi')
        result = SimpleNamespace(acknowledged=True, matched_count=1, upserted_id=None)
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=result) as update:
            await menu.menu_callback(None, q)
        update.assert_awaited_once_with({'user_id': 123}, {'$set': {'language': 'hi'}}, upsert=True)
        self.assertIn('सेटिंग्स', q.message.edit_text.await_args.args[0])
        self.assertIn('हिन्दी', q.message.reply_text.await_args.args[0])
        self.assertIn('अंग्रेज़ी', q.message.reply_text.await_args.args[0])

    async def test_new_user_and_english_choice(self):
        q = query('language_en')
        result = SimpleNamespace(acknowledged=True, matched_count=0, upserted_id='new')
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=result) as update:
            await menu.menu_callback(None, q)
        self.assertEqual(update.await_args.args[1], {'$set': {'language': 'en'}})
        self.assertIn('English', q.message.reply_text.await_args.args[0])
        self.assertIn('Choose what', q.message.edit_text.await_args.args[0])

    async def test_failed_or_unacknowledged_write_is_not_reported_as_success(self):
        for outcome in (RuntimeError('db down'), SimpleNamespace(acknowledged=False), SimpleNamespace(acknowledged=True, matched_count=0, upserted_id=None)):
            q = query('language_hi')
            kwargs = {'side_effect': outcome} if isinstance(outcome, Exception) else {'return_value': outcome}
            with self.subTest(outcome=outcome), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, **kwargs), self.assertLogs(menu.logger, level='WARNING'):
                await menu.menu_callback(None, q)
            q.message.edit_text.assert_not_awaited()
            self.assertIn('Could not save', q.message.reply_text.await_args.args[0])

    async def test_pending_flow_and_login_prevent_language_write(self):
        for mode in ('input', 'login', 'busy'):
            menu.waiting.clear()
            menu.busy_inputs.clear()
            user_steps.clear()
            if mode == 'input': menu.waiting[123] = 'caption'
            elif mode == 'login': user_steps[123] = 'login_code'
            else: menu.busy_inputs[123] = object()
            q = query('language_hi')
            with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update:
                await menu.menu_callback(None, q)
            update.assert_not_awaited()

    async def test_foreign_or_group_callback_cannot_change_language(self):
        for mode in ('foreign', 'group'):
            q = query('language_hi')
            if mode == 'foreign': q.message.chat.id = 321
            else: q.message.chat.type.value = 'supergroup'
            with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update:
                await menu.menu_callback(None, q)
            update.assert_not_awaited()
            self.assertTrue(q.answer.await_args.kwargs['show_alert'])

    async def test_prompt_is_translated_and_input_action_unchanged(self):
        q = query('destination')
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
            await menu.menu_callback(None, q)
        self.assertEqual(menu.waiting[123], 'destination')
        self.assertIn('संख्यात्मक चैट ID', q.message.reply_text.await_args.args[0])
        self.assertIn(('❌ रद्द करें', 'ui:cancel'), buttons(q.message.reply_text.await_args.kwargs['reply_markup']))

    async def test_language_change_and_navigation_are_serialized(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def update(*args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(acknowledged=True, matched_count=1, upserted_id=None)
        setting, home = query('language_hi'), query('home')
        with patch.object(func.users_collection, 'update_one', side_effect=update), patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
            first = asyncio.create_task(menu.menu_callback(None, setting))
            await entered.wait()
            second = asyncio.create_task(menu.menu_callback(None, home))
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            release.set()
            await asyncio.gather(first, second)
        self.assertIn('नमस्ते', home.message.edit_text.await_args.args[0])

    def test_regular_main_menu_buttons_have_hindi_labels(self):
        exclusions = {'language_en', 'language_hi'}
        for name, (_, _, rows, _) in menu.PAGES.items():
            for row in rows:
                for _, action in row:
                    with self.subTest(page=name, action=action):
                        self.assertTrue(action in localization.HI_BUTTONS or action in exclusions)

    async def test_failed_prompt_send_does_not_leave_invisible_input(self):
        q = query('caption')
        q.message.reply_text.side_effect = RuntimeError('send failure')
        with patch.object(localization, 'language_for', new_callable=AsyncMock, return_value='hi'):
            with self.assertRaises(RuntimeError):
                await menu.menu_callback(None, q)
        self.assertNotIn(123, menu.waiting)

    async def test_concurrent_prompts_cannot_both_start(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def read_language(uid):
            entered.set()
            await release.wait()
            return 'hi'
        first, second = query('caption'), query('prefix')
        with patch.object(localization, 'language_for', side_effect=read_language):
            a = asyncio.create_task(menu.menu_callback(None, first))
            await entered.wait()
            b = asyncio.create_task(menu.menu_callback(None, second))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(a, b)
        self.assertEqual(menu.waiting[123], 'caption')
        self.assertIn('Cancel the current flow first', second.message.reply_text.await_args.args[0])

if __name__ == '__main__':
    unittest.main()
