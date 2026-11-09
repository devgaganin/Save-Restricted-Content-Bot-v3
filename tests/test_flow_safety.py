"""Exercise input, login, and single-task concurrency without Telegram/MongoDB."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, gift, login, menu
from utils.custom_filters import set_user_step, user_steps


def callback(action):
    msg = SimpleNamespace(id=100, chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123),
                          edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(message=msg, from_user=SimpleNamespace(id=123),
                           data='ui:' + action, answer=AsyncMock())


def input_message(text):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), text=text, photo=None,
                           reply_text=AsyncMock(), delete=AsyncMock(), stop_propagation=MagicMock())


class FlowSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        language_patch = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')
        language_patch.start()
        self.addCleanup(language_patch.stop)
        batch_queue.locks.clear()
        batch.Z.clear()
        batch.ACTIVE_USERS.clear()
        menu.waiting.clear()
        menu.simple_confirmations.clear()
        menu.busy_inputs.clear()
        gift.pending.clear()
        login.login_cache.clear()
        user_steps.clear()

    async def test_navigation_discards_unsubmitted_batch_and_gift_inputs(self):
        batch.Z[123] = {'step': 'count'}
        menu.waiting[123] = 'gift_reference'
        gift.pending[123] = {'plan': 'd'}
        q = callback('home')
        await menu.menu_callback(None, q)
        self.assertNotIn(123, batch.Z)
        self.assertNotIn(123, menu.waiting)
        self.assertNotIn(123, gift.pending)
        q.message.edit_text.assert_awaited_once()

    async def test_pending_input_blocks_other_actions_until_cancelled(self):
        menu.waiting[123] = 'caption'
        q = callback('history')
        with patch.object(batch_queue, 'get_job', new_callable=AsyncMock) as job:
            await menu.menu_callback(None, q)
        job.assert_not_awaited()
        self.assertEqual(menu.waiting[123], 'caption')
        self.assertIn('Finish this input', q.message.reply_text.await_args.args[0])
        q.data = 'ui:home'
        await menu.menu_callback(None, q)
        self.assertNotIn(123, menu.waiting)

    async def test_pending_batch_count_blocks_unrelated_settings(self):
        batch.Z[123] = {'step': 'count'}
        q = callback('settings_view')
        await menu.menu_callback(None, q)
        self.assertEqual(batch.Z[123]['step'], 'count')
        self.assertIn('Finish this input', q.message.reply_text.await_args.args[0])

    async def test_navigation_does_not_silently_interrupt_login(self):
        set_user_step(123, login.STEP_CODE)
        q = callback('home')
        await menu.menu_callback(None, q)
        self.assertEqual(user_steps[123], login.STEP_CODE)
        self.assertIn('Cancel', q.message.reply_text.await_args.args[0])
        q.message.edit_text.assert_not_awaited()

    async def test_navigation_does_not_stop_started_batch(self):
        batch.Z[123] = {'step': 'process'}
        batch.ACTIVE_USERS['123'] = {'kind': 'batch', 'cancel_requested': False}
        q = callback('home')
        with patch.object(batch, 'request_batch_cancel', new_callable=AsyncMock) as stop:
            await menu.menu_callback(None, q)
        stop.assert_not_awaited()
        self.assertIn(123, batch.Z)

    async def test_two_inputs_do_not_apply_one_setting_twice(self):
        menu.waiting[123] = 'caption'
        entered, release = asyncio.Event(), asyncio.Event()
        async def save(*args):
            entered.set()
            await release.wait()
        first, second = input_message('one'), input_message('two')
        with patch.object(menu, 'save_user_data', new_callable=AsyncMock, side_effect=save) as store:
            task = asyncio.create_task(menu.menu_input(None, first))
            await entered.wait()
            await menu.menu_input(None, second)
            self.assertIn('still being processed', second.reply_text.await_args.args[0])
            release.set()
            await task
        store.assert_awaited_once_with(123, 'caption', 'one')
        self.assertNotIn(123, menu.busy_inputs)

    async def test_busy_input_blocks_menu_navigation(self):
        menu.busy_inputs[123] = object()
        q = callback('home')
        await menu.menu_callback(None, q)
        q.message.edit_text.assert_not_awaited()
        self.assertIn('still being processed', q.message.reply_text.await_args.args[0])

    async def test_duplicate_owner_grant_input_cannot_extend_twice(self):
        from plugins import pay
        from utils import func
        menu.waiting[123] = 'owner_grant'
        entered, release = asyncio.Event(), asyncio.Event()
        audit = SimpleNamespace(insert_one=AsyncMock(), update_one=AsyncMock())
        async def insert(*args, **kwargs):
            entered.set()
            await release.wait()
            return SimpleNamespace(inserted_id='audit-1')
        audit.insert_one.side_effect = insert
        db = MagicMock()
        db.__getitem__.return_value = audit
        async def grant(invoice, charge):
            return {'applied_payments': [charge]}
        first, second = input_message('456 30'), input_message('456 30')
        with patch.object(menu, 'OWNER_ID', [123]), patch.object(func, 'db', db), patch.object(func.premium_users_collection, 'create_index', new_callable=AsyncMock), patch.object(pay, 'grant_invoice', new_callable=AsyncMock, side_effect=grant) as grant_mock:
            task = asyncio.create_task(menu.menu_input(None, first))
            await entered.wait()
            await menu.menu_input(None, second)
            release.set()
            await task
        grant_mock.assert_awaited_once()
        self.assertIn('still being processed', second.reply_text.await_args.args[0])

    async def test_logout_does_not_claim_storage_removed_on_failure(self):
        from plugins import mirror
        from utils import func, encrypt
        q = callback('logout_confirm')
        temp = SimpleNamespace(connect=AsyncMock(), log_out=AsyncMock(), disconnect=AsyncMock())
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'cipher'}), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, side_effect=RuntimeError('db error')), patch.object(encrypt, 'dcs', return_value='plaintext'), patch('pyrogram.Client', return_value=temp):
            await menu.menu_callback(None, q)
            q.data = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await menu.menu_callback(None, q)
        self.assertIn('Could not confirm removal from storage', q.message.edit_text.await_args.args[0])
        self.assertNotIn('revoked and removed', q.message.edit_text.await_args.args[0])

    async def test_bot_removal_does_not_claim_success_on_failure(self):
        from plugins import mirror
        from utils import func
        q = callback('removebot_confirm')
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'bot_token': 'cipher'}), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, side_effect=RuntimeError('db error')), patch('os.remove', side_effect=FileNotFoundError):
            await menu.menu_callback(None, q)
            q.data = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await menu.menu_callback(None, q)
        self.assertIn('Could not confirm token and local session removal', q.message.edit_text.await_args.args[0])

    async def test_logout_is_blocked_while_login_is_pending(self):
        from plugins import mirror
        from utils import func
        q = callback('logout_confirm')
        with patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={}), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop:
            await menu.menu_callback(None, q)
            q.data = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            set_user_step(123, login.STEP_CODE)
            await menu.menu_callback(None, q)
        stop.assert_not_awaited()
        self.assertIn('finish login', q.message.reply_text.await_args.args[0])

    async def test_login_input_and_cancel_are_serialized(self):
        set_user_step(123, login.STEP_CODE)
        temp = SimpleNamespace(disconnect=AsyncMock())
        login.login_cache[123] = {'temp_client': temp}
        entered, release = asyncio.Event(), asyncio.Event()
        async def do_login(client, message):
            entered.set()
            await release.wait()
        q = callback('cancel')
        msg = input_message('1 2 3 4 5')
        with patch.object(login, '_handle_login_steps', side_effect=do_login):
            running = asyncio.create_task(login.handle_login_steps(None, msg))
            await entered.wait()
            cancelling = asyncio.create_task(menu.menu_callback(None, q))
            await asyncio.sleep(0)
            self.assertFalse(cancelling.done())
            self.assertIn(123, user_steps)
            release.set()
            await asyncio.gather(running, cancelling)
        self.assertNotIn(123, user_steps)
        self.assertNotIn(123, login.login_cache)
        temp.disconnect.assert_awaited_once()

    async def test_queued_login_input_from_old_step_is_discarded(self):
        set_user_step(123, login.STEP_PHONE)
        login.login_cache[123] = {'status_msg': object()}
        entered, release = asyncio.Event(), asyncio.Event()
        async def handle(client, message):
            entered.set()
            await release.wait()
            set_user_step(123, login.STEP_CODE)
        first, second = input_message('+12345678900'), input_message('+12345678900')
        with patch.object(login, '_handle_login_steps', new_callable=AsyncMock, side_effect=handle) as handler:
            a = asyncio.create_task(login.handle_login_steps(None, first))
            await entered.wait()
            b = asyncio.create_task(login.handle_login_steps(None, second))
            await asyncio.sleep(0)
            release.set()
            await asyncio.gather(a, b)
        self.assertEqual(handler.await_count, 1)
        second.delete.assert_awaited_once()
        self.assertEqual(user_steps[123], login.STEP_CODE)

    async def test_saved_login_replaces_old_cached_client(self):
        temp = SimpleNamespace(export_session_string=AsyncMock(return_value='sensitive'), disconnect=AsyncMock(), log_out=AsyncMock())
        old = SimpleNamespace(stop=AsyncMock())
        status = SimpleNamespace(edit=AsyncMock())
        batch.UC[123] = old
        login.login_cache[123] = {'temp_client': temp, 'status_msg': status}
        set_user_step(123, login.STEP_CODE)
        with patch.object(login, 'ecs', return_value='encrypted'), patch.object(login, 'save_user_session', new_callable=AsyncMock, return_value=True) as save:
            ok = await login.store_authorized_session(123, temp, status)
        self.assertTrue(ok)
        save.assert_awaited_once_with(123, 'encrypted')
        old.stop.assert_awaited_once()
        self.assertNotIn(123, batch.UC)
        self.assertNotIn(123, user_steps)
        self.assertNotIn(123, login.login_cache)
        self.assertIn('successfully', status.edit.await_args.args[0])

    async def test_failed_session_save_does_not_claim_success(self):
        temp = SimpleNamespace(export_session_string=AsyncMock(return_value='sensitive'), disconnect=AsyncMock(), log_out=AsyncMock())
        status = SimpleNamespace(edit=AsyncMock())
        login.login_cache[123] = {'temp_client': temp, 'status_msg': status}
        set_user_step(123, login.STEP_CODE)
        with patch.object(login, 'ecs', return_value='encrypted'), patch.object(login, 'save_user_session', new_callable=AsyncMock, return_value=False):
            ok = await login.store_authorized_session(123, temp, status)
        self.assertFalse(ok)
        temp.log_out.assert_awaited_once()
        self.assertNotIn(123, user_steps)
        self.assertNotIn(123, login.login_cache)
        self.assertIn('Could not confirm', status.edit.await_args.args[0])
        self.assertNotIn('successfully', status.edit.await_args.args[0])

    async def test_login_stops_mirror_before_starting_new_flow(self):
        from plugins import mirror
        q = callback('login')
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop, patch.object(login, 'begin_login', new_callable=AsyncMock) as begin:
            await menu.menu_callback(None, q)
        stop.assert_awaited_once_with(123, drain=True)
        begin.assert_awaited_once_with(q.message, 123, 'en')

    async def test_single_is_active_during_fetch_and_cancel_prevents_send(self):
        batch.Z[123] = {'step': 'start_single'}
        progress = SimpleNamespace(id=10, edit=AsyncMock())
        msg = input_message('https://t.me/example/10')
        msg.chat = SimpleNamespace(id=123)
        msg.reply_text.return_value = progress
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            entered.set()
            await release.wait()
            return object()
        bot, user = object(), object()
        with patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=bot), patch.object(batch, 'get_uclient', new_callable=AsyncMock, return_value=user), patch.object(batch_queue, 'has_unfinished', new_callable=AsyncMock, return_value=False), patch.object(batch, 'get_msg', new_callable=AsyncMock, side_effect=fetch), patch.object(batch, 'process_msg', new_callable=AsyncMock) as send, patch.object(batch, 'save_active_users_to_file', new_callable=AsyncMock):
            running = asyncio.create_task(batch.text_handler(None, msg))
            await entered.wait()
            self.assertTrue(batch.is_user_active(123))
            self.assertEqual(batch.get_batch_info(123)['kind'], 'single')
            self.assertFalse(await batch.set_batch_paused(123, True))
            q = callback('cancel')
            await menu.menu_callback(None, q)
            self.assertTrue(batch.should_cancel(123))
            release.set()
            await running
        send.assert_not_awaited()
        self.assertFalse(batch.is_user_active(123))
        self.assertNotIn(123, batch.Z)
        self.assertIn('cancelled before delivery', progress.edit.await_args.args[0])

    async def test_single_active_blocks_account_deletion(self):
        batch.ACTIVE_USERS['123'] = {'kind': 'single'}
        q = callback('delete_data_yes:' + 'a' * 16)
        q.message.id = 100
        menu.delete_confirmations[123] = ('a' * 16, __import__('time').monotonic() + 60, 100)
        with patch.object(batch_queue.jobs, 'delete_one', new_callable=AsyncMock) as delete:
            await menu.menu_callback(None, q)
        delete.assert_not_awaited()
        self.assertIn('Stop your active task', q.message.edit_text.await_args.args[0])

    async def test_single_is_shown_in_task_status(self):
        batch.ACTIVE_USERS['123'] = {'kind': 'single', 'cancel_requested': False}
        q = callback('task')
        with patch.object(batch_queue, 'get_job', new_callable=AsyncMock) as saved:
            await menu.menu_callback(None, q)
        saved.assert_not_awaited()
        self.assertIn('single post', q.message.edit_text.await_args.args[0])

    async def test_two_begin_requests_cannot_overwrite_each_other(self):
        progress1 = SimpleNamespace(edit=AsyncMock())
        progress2 = SimpleNamespace(edit=AsyncMock())
        m1 = SimpleNamespace(reply_text=AsyncMock(return_value=progress1))
        m2 = SimpleNamespace(reply_text=AsyncMock(return_value=progress2))
        entered, release = asyncio.Event(), asyncio.Event()
        async def connect(uid):
            entered.set()
            await release.wait()
            return object()
        with patch.object(batch, 'FREEMIUM_LIMIT', 5), patch.object(batch, 'sub', new_callable=AsyncMock, return_value=0), patch.object(batch_queue, 'has_unfinished', new_callable=AsyncMock, return_value=False), patch.object(batch, 'get_ubot', new_callable=AsyncMock, side_effect=connect):
            first = asyncio.create_task(batch.begin_processing(None, m1, 123, 'batch'))
            await entered.wait()
            second = asyncio.create_task(batch.begin_processing(None, m2, 123, 'single'))
            release.set()
            await asyncio.gather(first, second)
        self.assertEqual(batch.Z[123]['step'], 'start')
        self.assertIn('already in progress', progress2.edit.await_args.args[0])
