"""One-shot account/settings confirmations and credential changes (offline)."""
import asyncio
import os
import time
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, login, menu, mirror, payment_review
from utils import func
from utils.custom_filters import user_steps


def query(action, uid=123, mid=101):
    msg = SimpleNamespace(id=mid, chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                          edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), data='ui:' + action, message=msg, answer=AsyncMock())


class DestructiveTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        menu.simple_confirmations.clear(); menu.delete_confirmations.clear(); menu.waiting.clear()
        menu.busy_inputs.clear(); batch.Z.clear(); batch.ACTIVE_USERS.clear(); batch_queue.locks.clear()
        login.login_cache.clear(); user_steps.clear()
        self.locale_patch = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi')
        self.locale = self.locale_patch.start(); self.addCleanup(self.locale_patch.stop)

    async def ask(self, kind, uid=123, mid=101):
        q = query(kind + '_confirm', uid, mid)
        await menu.menu_callback(None, q)
        buttons = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        q.data = buttons[0][0].callback_data
        self.assertTrue(q.data.startswith('ui:confirm_yes:' + kind + ':'))
        self.assertLessEqual(len(q.data.encode()), 64)
        return q

    async def test_old_raw_callbacks_do_not_mutate_anything(self):
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update, patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop:
            for kind in ('logout', 'removebot', 'reset'):
                q = query(kind)
                await menu.menu_callback(None, q)
                self.assertIn('अमान्य', q.message.edit_text.await_args.args[0])
            update.assert_not_awaited(); stop.assert_not_awaited()

    async def test_confirmation_hindi_and_english_warnings_and_no_secret_snapshot(self):
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'private-session'}):
            q = await self.ask('logout')
        self.assertIn('10 मिनट', q.message.edit_text.await_args.args[0])
        self.assertIn('Devices', q.message.edit_text.await_args.args[0])
        self.assertNotIn('private-session', str(menu.simple_confirmations))
        self.assertIn('लॉगआउट', q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].text)
        self.locale.return_value = 'en'
        q = await self.ask('reset')
        self.assertIn('active task or mirror', q.message.edit_text.await_args.args[0])
        self.assertIn('10 minutes', q.message.edit_text.await_args.args[0])

    async def test_changed_credentials_are_not_revoked_or_removed(self):
        for kind, field in (('logout', 'session_string'), ('removebot', 'bot_token')):
            with self.subTest(kind=kind):
                with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, side_effect=[{field: 'old-secret'}, {field: 'new-secret'}]), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update, patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop:
                    q = await self.ask(kind)
                    await menu.menu_callback(None, q)
                    self.assertIn('बदल गई', q.message.edit_text.await_args.args[0])
                    self.assertNotIn('secret', q.message.edit_text.await_args.args[0])
                    stop.assert_not_awaited(); update.assert_not_awaited()

    async def test_logout_success_is_conditional_and_one_shot(self):
        self.locale.return_value = 'en'
        temp = SimpleNamespace(connect=AsyncMock(), log_out=AsyncMock(), disconnect=AsyncMock())
        with ExitStack() as stack:
            stack.enter_context(patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'cipher'}))
            update = stack.enter_context(patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(modified_count=1)))
            stop = stack.enter_context(patch.object(mirror, 'stop_mirror', new_callable=AsyncMock))
            stack.enter_context(patch('utils.encrypt.dcs', return_value='plain'))
            stack.enter_context(patch('pyrogram.Client', return_value=temp))
            q = await self.ask('logout'); await menu.menu_callback(None, q)
            self.assertIn('revoked and removed', q.message.edit_text.await_args.args[0])
            update.assert_awaited_once_with({'user_id': 123, 'session_string': 'cipher'}, {'$unset': {'session_string': ''}})
            stop.assert_awaited_once_with(123, drain=True); temp.log_out.assert_awaited_once()
            await menu.menu_callback(None, q)
            update.assert_awaited_once(); temp.log_out.assert_awaited_once()

    async def test_compare_and_unset_race_does_not_claim_success(self):
        self.locale.return_value = 'en'
        temp = SimpleNamespace(connect=AsyncMock(), log_out=AsyncMock(), disconnect=AsyncMock())
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'cipher'}), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(modified_count=0)), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch('utils.encrypt.dcs', return_value='plain'), patch('pyrogram.Client', return_value=temp):
            q = await self.ask('logout'); await menu.menu_callback(None, q)
        self.assertIn('Could not confirm removal', q.message.edit_text.await_args.args[0]); self.assertIn('Devices', q.message.edit_text.await_args.args[0])

    async def test_remove_bot_success_uses_conditional_field_and_warns_revoke(self):
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'bot_token': 'cipher'}), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(modified_count=1)) as update, patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop, patch('os.remove', side_effect=FileNotFoundError):
            q = await self.ask('removebot'); await menu.menu_callback(None, q)
        update.assert_awaited_once_with({'user_id': 123, 'bot_token': 'cipher'}, {'$unset': {'bot_token': ''}})
        stop.assert_awaited_once_with(123, drain=True)
        self.assertIn('BotFather', q.message.edit_text.await_args.args[0])

    async def test_reset_success_clears_only_content_fields(self):
        self.locale.return_value = 'en'
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update, patch('os.remove', side_effect=FileNotFoundError):
            q = await self.ask('reset'); await menu.menu_callback(None, q)
        selector, operation = update.await_args.args
        self.assertEqual(selector, {'user_id': 123})
        self.assertIn('chat_id', operation['$unset']); self.assertIn('caption', operation['$unset'])
        for field in ('language', 'bot_token', 'session_string', 'premium'):
            self.assertNotIn(field, operation['$unset'])
        self.assertIn('were reset', q.message.edit_text.await_args.args[0])

    async def test_reset_failure_in_database_or_local_file_warns_partial(self):
        for stage in ('db', 'file'):
            with self.subTest(stage=stage):
                with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, side_effect=RuntimeError('secret') if stage == 'db' else None), patch('os.remove', side_effect=PermissionError('secret') if stage == 'file' else None):
                    q = await self.ask('reset'); await menu.menu_callback(None, q)
                self.assertIn('पुष्टि नहीं हुई', q.message.edit_text.await_args.args[0]); self.assertNotIn('secret', q.message.edit_text.await_args.args[0])

    async def test_navigation_expiry_wrong_user_and_wrong_message(self):
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update:
            q = await self.ask('reset')
            wrong_msg = query(q.data[3:], mid=102); await menu.menu_callback(None, wrong_msg)
            other = query(q.data[3:], uid=456); await menu.menu_callback(None, other)
            self.assertIn(123, menu.simple_confirmations)
            menu.simple_confirmations[123]['expires'] = time.monotonic() - 1
            await menu.menu_callback(None, q); update.assert_not_awaited()
            self.assertNotIn(123, menu.simple_confirmations)
            q = await self.ask('reset'); await menu.menu_callback(None, query('home'))
            await menu.menu_callback(None, q); update.assert_not_awaited()

    async def test_second_confirmation_invalidates_first_and_cancel(self):
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update:
            old = await self.ask('reset'); new = await self.ask('reset')
            self.assertNotEqual(old.data, new.data)
            await menu.menu_callback(None, old)
            self.assertIn(123, menu.simple_confirmations)
            await menu.menu_callback(None, query('cancel'))
            await menu.menu_callback(None, new); update.assert_not_awaited()

    async def test_group_and_other_private_chat_cannot_request_confirmation(self):
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock) as read:
            for group in (True, False):
                q = query('logout_confirm')
                if group: q.message.chat.type.value = 'supergroup'
                else: q.message.chat.id = 456
                await menu.menu_callback(None, q)
                self.assertTrue(q.answer.await_args.kwargs['show_alert'])
            read.assert_not_awaited(); self.assertFalse(menu.simple_confirmations)

    async def test_confirm_read_failure_and_stop_failure_are_reported(self):
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, side_effect=RuntimeError('secret')):
            q = query('logout_confirm'); await menu.menu_callback(None, q)
            self.assertIn('जाँची नहीं', q.message.edit_text.await_args.args[0]); self.assertNotIn(123, menu.simple_confirmations)
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'bot_token': 'cipher'}), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock, side_effect=RuntimeError('secret')), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update:
            q = await self.ask('removebot'); await menu.menu_callback(None, q)
            self.assertIn('मिरर बंद', q.message.edit_text.await_args.args[0]); update.assert_not_awaited()

    async def test_concurrent_double_click_consumes_once(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def update(*args, **kwargs):
            entered.set(); await release.wait()
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, side_effect=update) as store, patch('os.remove', side_effect=FileNotFoundError):
            q = await self.ask('reset')
            first = asyncio.create_task(menu.menu_callback(None, q)); await entered.wait()
            second = asyncio.create_task(menu.menu_callback(None, q)); await asyncio.sleep(0)
            release.set(); await asyncio.gather(first, second)
            store.assert_awaited_once()
