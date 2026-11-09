"""Offline account status and one-shot deletion safety tests."""
import asyncio
import os
import time
import unittest
from contextlib import ExitStack
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, login, menu, mirror
from utils import func
from utils.custom_filters import user_steps


def query(action, uid=123, message_id=101):
    msg = SimpleNamespace(id=message_id, chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=uid),
                          edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), data='ui:' + action, message=msg, answer=AsyncMock())


class AccountTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        menu.delete_confirmations.clear(); menu.simple_confirmations.clear(); menu.waiting.clear(); menu.busy_inputs.clear()
        batch.Z.clear(); batch.ACTIVE_USERS.clear(); batch_queue.locks.clear()
        login.login_cache.clear(); user_steps.clear()
        self.language = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi')
        self.language_mock = self.language.start(); self.addCleanup(self.language.stop)

    async def confirm(self, uid=123, message_id=101):
        q = query('delete_data_confirm', uid, message_id)
        await menu.menu_callback(None, q)
        buttons = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        action = buttons[0][0].callback_data
        self.assertTrue(action.startswith('ui:delete_data_yes:'))
        self.assertLessEqual(len(action.encode()), 64)
        q.data = action
        return q

    def deletion_mocks(self, stack, document=None):
        result = {}
        result['read'] = stack.enter_context(patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value=document or {}))
        result['users'] = stack.enter_context(patch.object(func.users_collection, 'delete_many', new_callable=AsyncMock))
        result['mirror_stop'] = stack.enter_context(patch.object(mirror, 'stop_mirror', new_callable=AsyncMock))
        result['mirrors'] = stack.enter_context(patch.object(mirror.mirrors, 'delete_one', new_callable=AsyncMock))
        result['jobs'] = stack.enter_context(patch.object(batch_queue.jobs, 'delete_one', new_callable=AsyncMock))
        result['history'] = stack.enter_context(patch.object(batch_queue.history, 'delete_many', new_callable=AsyncMock))
        return result

    async def test_status_private_projected_hindi_never_shows_secrets(self):
        q = query('status')
        with patch.object(menu, 'OWNER_ID', []), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'secret-session', 'bot_token': '<secret>'}) as users, patch.object(func.premium_users_collection, 'find_one', new_callable=AsyncMock, return_value={'subscription_end': datetime(2020, 1, 1, tzinfo=timezone.utc)}) as premium:
            await menu.menu_callback(None, q)
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('सेव है', text); self.assertIn('समाप्त हुआ', text); self.assertIn('UTC', text)
        self.assertIn('मान्य', text)
        self.assertNotIn('secret-session', text); self.assertNotIn('<secret>', text)
        users.assert_awaited_once_with({'user_id': 123}, {'session_string': 1, 'bot_token': 1, '_id': 0})
        premium.assert_awaited_once_with({'user_id': 123}, {'subscription_end': 1, '_id': 0})

    async def test_status_errors_and_malformed_plan_show_unknown_not_free(self):
        q = query('status')
        with patch.object(menu, 'OWNER_ID', []), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, side_effect=RuntimeError('private')), patch.object(func.premium_users_collection, 'find_one', new_callable=AsyncMock, return_value={'subscription_end': 'bad'}):
            await menu.menu_callback(None, q)
        text = q.message.edit_text.await_args.args[0]
        self.assertEqual(text.count('अज्ञात'), 3)
        self.assertNotIn('फ़्री', text); self.assertNotIn('private', text)

    async def test_status_english_free_and_owner(self):
        self.language_mock.return_value = 'en'
        q = query('status')
        with patch.object(menu, 'OWNER_ID', []), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value=None), patch.object(func.premium_users_collection, 'find_one', new_callable=AsyncMock, return_value=None):
            await menu.menu_callback(None, q)
        self.assertIn('Not saved', q.message.edit_text.await_args.args[0])
        self.assertIn('Free', q.message.edit_text.await_args.args[0])
        with patch.object(menu, 'OWNER_ID', [123]), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={}), patch.object(func.premium_users_collection, 'find_one', new_callable=AsyncMock) as plan:
            await menu.menu_callback(None, q)
        self.assertIn('Owner', q.message.edit_text.await_args.args[0]); plan.assert_not_awaited()

    async def test_hindi_confirmation_and_delete_success_uses_snapshot(self):
        q = await self.confirm()
        warning = q.message.edit_text.await_args.args[0]
        self.assertIn('बिलिंग', warning); self.assertIn('10 मिनट', warning)
        self.assertIn('हाँ', q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].text)
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack)
            await menu.menu_callback(None, q)
            mocks['jobs'].assert_awaited_once_with({'_id': 123})
            mocks['history'].assert_awaited_once_with({'user_id': 123})
            mocks['mirrors'].assert_awaited_once_with({'user_id': 123})
            mocks['users'].assert_awaited_once_with({'user_id': 123})
            mocks['mirror_stop'].assert_awaited_once_with(123, drain=True)
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('हटाए गए', text); self.assertIn('Devices', text); self.assertIn('बैकअप', text)
        self.assertNotIn(123, menu.delete_confirmations)

    async def test_legacy_invalid_expired_other_message_and_other_user_cannot_delete(self):
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack)
            old = query('delete_data')
            await menu.menu_callback(None, old)
            self.assertIn('अमान्य', old.message.edit_text.await_args.args[0])
            q = await self.confirm()
            other = query(q.data[3:], 456, 101)
            await menu.menu_callback(None, other)
            wrong_message = query(q.data[3:], 123, 102)
            await menu.menu_callback(None, wrong_message)
            self.assertIn(123, menu.delete_confirmations)
            token, _, mid = menu.delete_confirmations[123]
            menu.delete_confirmations[123] = (token, time.monotonic() - 1, mid)
            await menu.menu_callback(None, q)
            mocks['users'].assert_not_awaited(); mocks['jobs'].assert_not_awaited()
            self.assertNotIn(123, menu.delete_confirmations)

    async def test_navigation_and_cancel_invalidate_and_old_tokens_cannot_delete(self):
        for action in ('home', 'cancel'):
            with self.subTest(action=action):
                q = await self.confirm()
                with ExitStack() as stack:
                    mocks = self.deletion_mocks(stack)
                    await menu.menu_callback(None, query(action))
                    self.assertNotIn(123, menu.delete_confirmations)
                    await menu.menu_callback(None, q)
                    mocks['users'].assert_not_awaited()

    async def test_replay_and_concurrent_taps_are_one_shot(self):
        q = await self.confirm()
        entered, release = asyncio.Event(), asyncio.Event()
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack)
            async def slow_stop(*args, **kwargs):
                entered.set(); await release.wait()
            mocks['mirror_stop'].side_effect = slow_stop
            first = asyncio.create_task(menu.menu_callback(None, q))
            await entered.wait()
            second = asyncio.create_task(menu.menu_callback(None, q))
            await asyncio.sleep(0); release.set()
            await asyncio.gather(first, second)
            mocks['users'].assert_awaited_once(); mocks['jobs'].assert_awaited_once()
            await menu.menu_callback(None, q)
            mocks['users'].assert_awaited_once()

    async def test_new_confirmation_invalidates_older_token_without_consuming_new_one(self):
        old = await self.confirm()
        new = await self.confirm()
        self.assertNotEqual(old.data, new.data)
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack)
            await menu.menu_callback(None, old)
            self.assertIn(123, menu.delete_confirmations)
            mocks['users'].assert_not_awaited()
            await menu.menu_callback(None, new)
            mocks['users'].assert_awaited_once()

    async def test_english_success_reports_confirmed_revocation(self):
        self.language_mock.return_value = 'en'
        q = await self.confirm()
        temp = SimpleNamespace(connect=AsyncMock(), log_out=AsyncMock(), disconnect=AsyncMock())
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack, {'session_string': 'cipher'})
            stack.enter_context(patch('utils.encrypt.dcs', return_value='session'))
            stack.enter_context(patch('pyrogram.Client', return_value=temp))
            await menu.menu_callback(None, q)
            mocks['users'].assert_awaited_once()
        self.assertIn('Telegram session revoked.', q.message.edit_text.await_args.args[0])
        self.assertIn('Billing records and backups', q.message.edit_text.await_args.args[0])
        temp.log_out.assert_awaited_once()

    async def test_read_failure_no_deletion_and_partial_failure_never_claims_success(self):
        for fail_stage in ('read', 'history', 'users'):
            with self.subTest(stage=fail_stage):
                q = await self.confirm()
                with ExitStack() as stack:
                    mocks = self.deletion_mocks(stack)
                    mocks[fail_stage].side_effect = RuntimeError('credential-secret')
                    await menu.menu_callback(None, q)
                    text = q.message.edit_text.await_args.args[0]
                    self.assertNotIn('हटाए गए', text)
                    self.assertNotIn('credential-secret', text)
                    if fail_stage == 'read':
                        mocks['mirror_stop'].assert_not_awaited()
                        self.assertIn('कोई डेटा नहीं हटाया', text)
                    else:
                        self.assertIn('कुछ डेटा', text); self.assertIn('Devices', text)
                self.assertNotIn(123, menu.delete_confirmations)

    async def test_revoke_failure_warns_but_does_not_claim_revocation(self):
        from plugins import payment_review  # decorators register before patching Client
        q = await self.confirm()
        client = SimpleNamespace(connect=AsyncMock(), log_out=AsyncMock(side_effect=RuntimeError('secret')), disconnect=AsyncMock())
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack, {'session_string': 'cipher'})
            stack.enter_context(patch('utils.encrypt.dcs', return_value='sensitive'))
            stack.enter_context(patch('pyrogram.Client', return_value=client))
            await menu.menu_callback(None, q)
            mocks['users'].assert_awaited_once()
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('Devices', text); self.assertNotIn('सेशन रद्द हुआ', text)
        self.assertNotIn('secret', text); client.disconnect.assert_awaited_once()

    async def test_active_task_blocks_valid_confirmation(self):
        q = await self.confirm()
        batch.ACTIVE_USERS['123'] = {'kind': 'single'}
        with ExitStack() as stack:
            mocks = self.deletion_mocks(stack)
            await menu.menu_callback(None, q)
            mocks['users'].assert_not_awaited(); mocks['jobs'].assert_not_awaited()
        self.assertIn('टास्क', q.message.edit_text.await_args.args[0])
        self.assertNotIn(123, menu.delete_confirmations)

    async def test_group_and_foreign_chat_rejected_before_confirmation(self):
        q = query('delete_data_confirm'); q.message.chat.type.value = 'supergroup'
        await menu.menu_callback(None, q)
        q.answer.assert_awaited_once(); self.assertNotIn(123, menu.delete_confirmations)
        q = query('delete_data_confirm'); q.message.chat.id = 456
        await menu.menu_callback(None, q)
        self.assertTrue(q.answer.await_args.kwargs['show_alert'])
        self.assertNotIn(123, menu.delete_confirmations)
