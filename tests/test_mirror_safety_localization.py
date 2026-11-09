"""Mirror status/localization and stop retry safety with offline mocks."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, menu, mirror
from utils.custom_filters import user_steps


def query(action, uid=123):
    message = SimpleNamespace(id=100, chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                              edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(message=message, from_user=SimpleNamespace(id=uid), data='ui:' + action, answer=AsyncMock())


def input_message(text):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), text=text, photo=None,
                           reply_text=AsyncMock(), stop_propagation=MagicMock())


def state():
    return {'active': True, 'client': SimpleNamespace(remove_handler=MagicMock()),
            'handler': object(), 'work_lock': asyncio.Lock()}


class MirrorSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        mirror.active.clear(); mirror.stopping.clear(); menu.waiting.clear(); menu.busy_inputs.clear()
        menu.input_languages.clear(); batch.Z.clear(); batch.ACTIVE_USERS.clear(); batch_queue.locks.clear(); user_steps.clear()
        p = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi')
        self.language = p.start(); self.addCleanup(p.stop)

    async def test_status_is_user_scoped_and_shows_saved_separately(self):
        mirror.active[123] = state()
        q = query('mirror_status')
        with patch.object(mirror.mirrors, 'find_one', new_callable=AsyncMock, return_value={'status': 'stopped'}) as find:
            await menu.menu_callback(None, q)
        find.assert_awaited_once_with({'user_id': 123}, {'_id': 0, 'status': 1})
        text = q.message.edit_text.await_args.args[0]
        self.assertIn('इस वर्कर में:', text); self.assertIn('सेव स्थिति:', text)
        self.assertIn('रुका हुआ सेव', text); self.assertIn('गारंटी नहीं', text)
        actions = [b.callback_data for row in q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard for b in row]
        self.assertIn('ui:mirror_stop', actions)

    async def test_status_db_failure_and_malicious_saved_values_are_safe(self):
        q = query('mirror_status')
        with patch.object(mirror.mirrors, 'find_one', new_callable=AsyncMock, side_effect=RuntimeError('private')):
            await menu.menu_callback(None, q)
        self.assertIn('जाँची नहीं', q.message.edit_text.await_args.args[0]); self.assertNotIn('private', q.message.edit_text.await_args.args[0])
        with patch.object(mirror.mirrors, 'find_one', new_callable=AsyncMock, return_value={'status': '<script>evil</script>'}):
            await menu.menu_callback(None, q)
        self.assertNotIn('<script>', q.message.edit_text.await_args.args[0])
        self.assertIn('अज्ञात', q.message.edit_text.await_args.args[0])

    async def test_private_ownership_required(self):
        for mode in ('group', 'foreign'):
            q = query('mirror_status')
            if mode == 'group': q.message.chat.type.value = 'supergroup'
            else: q.message.chat.id = 456
            with patch.object(mirror.mirrors, 'find_one', new_callable=AsyncMock) as find:
                await menu.menu_callback(None, q)
            find.assert_not_awaited(); self.assertTrue(q.answer.await_args.kwargs['show_alert'])

    async def test_hindi_prompt_validation_and_translated_failure(self):
        q = query('mirror'); await menu.menu_callback(None, q)
        text = q.message.reply_text.await_args.args[0]
        for value in ('प्रीमियम', 'डेस्टिनेशन', 'छूटे संदेश'):
            self.assertIn(value, text)
        msg = input_message('bad-source')
        with patch.object(mirror, 'start_mirror', new_callable=AsyncMock) as start:
            await menu.menu_input(None, msg)
        start.assert_not_awaited(); self.assertEqual(menu.waiting[123], 'mirror')
        self.assertIn('@username', msg.reply_text.await_args.args[0])
        msg = input_message('@example')
        with patch.object(mirror, 'start_mirror', new_callable=AsyncMock, return_value=(False, 'An active premium plan is required.')) as start:
            await menu.menu_input(None, msg)
        start.assert_awaited_once_with(123, '@example')
        self.assertIn('प्रीमियम', msg.reply_text.await_args.args[0]); self.assertNotIn(123, menu.waiting)

    async def test_start_ambiguous_error_warns_about_restart(self):
        menu.waiting[123] = 'mirror'; msg = input_message('@example')
        with patch.object(mirror, 'start_mirror', new_callable=AsyncMock, side_effect=RuntimeError('secret')):
            await menu.menu_input(None, msg)
        self.assertIn('रीस्टार्ट', msg.reply_text.await_args.args[0])
        self.assertNotIn('secret', msg.reply_text.await_args.args[0])

    async def test_stop_db_failure_retains_inflight_for_retry_and_drain(self):
        entry = state(); mirror.active[123] = entry
        await entry['work_lock'].acquire()
        with patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, side_effect=[RuntimeError('db'), SimpleNamespace(acknowledged=True)]) as update:
            with self.assertRaises(RuntimeError): await mirror.stop_mirror(123, drain=True)
            self.assertNotIn(123, mirror.active); self.assertIs(mirror.stopping[123], entry)
            self.assertFalse(entry['active']); entry['client'].remove_handler.assert_called_once()
            retry = asyncio.create_task(mirror.stop_mirror(123, drain=True))
            await asyncio.sleep(0)
            self.assertFalse(retry.done())
            entry['work_lock'].release()
            self.assertTrue(await retry)
            self.assertNotIn(123, mirror.stopping); self.assertEqual(update.await_count, 2)
            entry['client'].remove_handler.assert_called_once()

    async def test_unacknowledged_stop_or_detach_failure_keeps_state(self):
        for failure in ('db', 'detach'):
            with self.subTest(failure=failure):
                mirror.active.clear(); mirror.stopping.clear(); entry = state(); mirror.active[123] = entry
                if failure == 'detach': entry['client'].remove_handler.side_effect = RuntimeError('detach')
                with patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=False)):
                    with self.assertRaises(RuntimeError): await mirror.stop_mirror(123, drain=True)
                self.assertIs(mirror.stopping[123], entry); self.assertFalse(entry['active'])

    async def test_non_draining_stop_blocks_restart_until_confirmed_drain(self):
        entry = state(); mirror.active[123] = entry
        with patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=True)):
            self.assertTrue(await mirror.stop_mirror(123))
            ok, reason = await mirror.start_mirror(123, '@example')
            self.assertFalse(ok); self.assertIn('confirmed stop', reason)
            self.assertTrue(await mirror.stop_mirror(123, drain=True))
        self.assertNotIn(123, mirror.stopping)

    async def test_failed_handler_registration_disables_callback_and_needs_cleanup(self):
        client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-100999)),
                                 add_handler=MagicMock(side_effect=RuntimeError('failed')),
                                 remove_handler=MagicMock())
        doc = {'session_string': 'cipher', 'bot_token': 'cipher', 'chat_id': '-100888'}
        with patch.object(mirror, 'OWNER_ID', [123]), patch.object(mirror, 'get_user_data', new_callable=AsyncMock, return_value=doc), patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(batch, 'get_uclient', new_callable=AsyncMock, return_value=client), patch.dict(batch.UC, {123: client}), patch.object(mirror.mirrors, 'create_index', new_callable=AsyncMock), patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=True)), patch.object(batch, 'process_msg', new_callable=AsyncMock) as process:
            ok, reason = await mirror.start_mirror(123, '@example')
            self.assertFalse(ok); self.assertIn('Stop Mirror', reason)
            self.assertFalse(mirror.stopping[123]['active'])
            handler = client.add_handler.call_args.args[0]
            await handler.original_callback(client, SimpleNamespace(id=10))
            process.assert_not_awaited()
            self.assertTrue(await mirror.stop_mirror(123, drain=True))
        self.assertNotIn(123, mirror.stopping); client.remove_handler.assert_called_once()

    async def test_unacknowledged_start_and_error_state_are_uncertain(self):
        client = SimpleNamespace(get_chat=AsyncMock(return_value=SimpleNamespace(id=-100999)), add_handler=MagicMock())
        doc = {'session_string': 'cipher', 'bot_token': 'cipher', 'chat_id': '-100888'}
        with patch.object(mirror, 'OWNER_ID', [123]), patch.object(mirror, 'get_user_data', new_callable=AsyncMock, return_value=doc), patch.object(batch, 'get_ubot', new_callable=AsyncMock, return_value=object()), patch.object(batch, 'get_uclient', new_callable=AsyncMock, return_value=client), patch.dict(batch.UC, {123: client}), patch.object(mirror.mirrors, 'create_index', new_callable=AsyncMock), patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=False)):
            with self.assertRaises(RuntimeError): await mirror.start_mirror(123, '@example')
        client.add_handler.assert_not_called(); self.assertNotIn(123, mirror.active)

    async def test_stop_ui_drain_and_fail_warning(self):
        q = query('mirror_stop')
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock, return_value=True) as stop:
            await menu.menu_callback(None, q)
        stop.assert_awaited_once_with(123, drain=True)
        self.assertIn('पहले पूरी हो सकती', q.message.edit_text.await_args.args[0])
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock, side_effect=RuntimeError('secret')):
            await menu.menu_callback(None, q)
        self.assertIn('रीस्टार्ट', q.message.edit_text.await_args.args[0]); self.assertNotIn('secret', q.message.edit_text.await_args.args[0])

    async def test_callback_self_stop_does_not_deadlock_external_drain(self):
        entry = state(); mirror.active[123] = entry
        entered = asyncio.Event()
        async def callback():
            async with entry['work_lock']:
                entered.set()
                await asyncio.sleep(0)
                await mirror.stop_mirror(123)
        with patch.object(mirror.mirrors, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(acknowledged=True)):
            task = asyncio.create_task(callback()); await entered.wait()
            stop = asyncio.create_task(mirror.stop_mirror(123, drain=True))
            await asyncio.wait_for(asyncio.gather(task, stop), timeout=1)
        self.assertNotIn(123, mirror.stopping)
