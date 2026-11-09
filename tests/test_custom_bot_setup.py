"""Custom-bot token setup: offline localization, privacy, and storage tests."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import batch, batch_queue, login, menu, mirror
from utils import func
from utils.custom_filters import user_steps

TOKEN = '123456:' + 'A' * 32


def query(action, uid=123):
    msg = SimpleNamespace(id=101, chat=SimpleNamespace(id=uid, type=SimpleNamespace(value='private')),
                          edit_text=AsyncMock(), reply_text=AsyncMock())
    return SimpleNamespace(from_user=SimpleNamespace(id=uid), data='ui:' + action, message=msg, answer=AsyncMock())


def input_message(text=TOKEN):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), text=text, photo=None,
                           delete=AsyncMock(), reply_text=AsyncMock(), stop_propagation=MagicMock())


class CustomBotSetupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        batch_queue.locks.clear(); batch.Z.clear(); batch.ACTIVE_USERS.clear(); batch.UB.clear()
        menu.waiting.clear(); menu.input_languages.clear(); menu.busy_inputs.clear()
        menu.simple_confirmations.clear(); menu.delete_confirmations.clear()
        user_steps.clear(); login.login_cache.clear()
        self.patch_language = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi')
        self.language = self.patch_language.start(); self.addCleanup(self.patch_language.stop)

    async def open_prompt(self):
        q = query('addbot')
        await menu.menu_callback(None, q)
        return q

    async def test_privacy_prompt_snapshots_language_and_keeps_cancel(self):
        q = await self.open_prompt()
        prompt = q.message.reply_text.await_args.args[0]
        for term in ('भरोसेमंद', 'एन्क्रिप्ट', 'गारंटी नहीं', 'मिरर', 'वैधता नहीं', 'BotFather'):
            self.assertIn(term, prompt)
        self.assertEqual(menu.waiting[123], 'addbot')
        self.assertEqual(menu.input_languages[123], 'hi')
        self.assertEqual(q.message.reply_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'ui:cancel')

    async def test_hindi_success_deletes_token_drains_mirror_and_does_not_claim_validation(self):
        await self.open_prompt()
        self.language.return_value = 'en'  # The input keeps its prompt language.
        msg = input_message()
        old = SimpleNamespace(stop=AsyncMock()); batch.UB[123] = old
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop, patch.object(menu, 'save_user_bot', new_callable=AsyncMock, return_value=True) as save, patch('os.remove', side_effect=FileNotFoundError):
            await menu.menu_input(None, msg)
        msg.delete.assert_awaited_once(); old.stop.assert_awaited_once()
        stop.assert_awaited_once_with(123, drain=True); save.assert_awaited_once_with(123, TOKEN)
        text = msg.reply_text.await_args.args[0]
        self.assertIn('सेव हुआ', text); self.assertIn('वैधता जाँची नहीं', text)
        self.assertNotIn(TOKEN, text); self.assertNotIn(123, menu.waiting); self.assertNotIn(123, menu.input_languages)

    async def test_invalid_input_keeps_prompt_and_cancel_button(self):
        await self.open_prompt(); msg = input_message('not-a-secret-token')
        with patch.object(menu, 'save_user_bot', new_callable=AsyncMock) as save:
            await menu.menu_input(None, msg)
        msg.delete.assert_awaited_once(); save.assert_not_awaited()
        self.assertIn('फ़ॉर्मैट', msg.reply_text.await_args.args[0])
        self.assertEqual(msg.reply_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'ui:cancel')
        self.assertEqual(menu.waiting[123], 'addbot')
        await menu.menu_callback(None, query('cancel'))
        self.assertNotIn(123, menu.waiting); self.assertNotIn(123, menu.input_languages)

    async def test_deletion_failure_warns_without_leaking_secret_and_still_saves(self):
        self.language.return_value = 'en'; await self.open_prompt(); msg = input_message()
        msg.delete.side_effect = RuntimeError(TOKEN)
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(menu, 'save_user_bot', new_callable=AsyncMock, return_value=True), patch('os.remove', side_effect=FileNotFoundError), self.assertLogs(menu.logger, level='WARNING') as logs:
            await menu.menu_input(None, msg)
        warnings = ' '.join(str(call.args[0]) for call in msg.reply_text.await_args_list)
        self.assertIn('Delete it yourself', warnings); self.assertIn('BotFather', warnings)
        self.assertIn('validity was not checked', warnings)
        self.assertNotIn(TOKEN, warnings + ' '.join(logs.output))

    async def test_uncertain_save_and_stop_failure_never_claim_success(self):
        for failure in ('save', 'mirror', 'local_file'):
            with self.subTest(failure=failure):
                menu.waiting.clear(); menu.input_languages.clear()
                await self.open_prompt(); msg = input_message()
                with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock, side_effect=RuntimeError(TOKEN) if failure == 'mirror' else None), patch.object(menu, 'save_user_bot', new_callable=AsyncMock, return_value=False), patch('os.remove', side_effect=PermissionError(TOKEN) if failure == 'local_file' else FileNotFoundError):
                    await menu.menu_input(None, msg)
                text = msg.reply_text.await_args.args[0]
                self.assertIn('पुष्टि नहीं हुई', text); self.assertIn('सेव हो चुका हो सकता', text)
                self.assertIn('BotFather', text); self.assertNotIn(TOKEN, text)
                self.assertNotIn(123, menu.waiting)

    async def test_busy_second_token_deleted_and_not_saved_twice(self):
        await self.open_prompt(); entered, release = asyncio.Event(), asyncio.Event()
        async def save(*args):
            entered.set(); await release.wait(); return True
        first, second = input_message(), input_message('987654:' + 'B' * 32)
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(menu, 'save_user_bot', new_callable=AsyncMock, side_effect=save) as store, patch('os.remove', side_effect=FileNotFoundError):
            first_task = asyncio.create_task(menu.menu_input(None, first)); await entered.wait()
            await menu.menu_input(None, second)
            second.delete.assert_awaited_once(); self.assertIn('प्रोसेस हो रहा', second.reply_text.await_args.args[0])
            release.set(); await first_task
        store.assert_awaited_once_with(123, TOKEN)

    async def test_cancel_during_save_cannot_claim_to_undo_inflight_write(self):
        await self.open_prompt(); entered, release = asyncio.Event(), asyncio.Event()
        async def save(*args):
            entered.set(); await release.wait(); return True
        msg, cancel = input_message(), query('cancel')
        with patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(menu, 'save_user_bot', new_callable=AsyncMock, side_effect=save), patch('os.remove', side_effect=FileNotFoundError):
            running = asyncio.create_task(menu.menu_input(None, msg)); await entered.wait()
            await menu.menu_callback(None, cancel)
            self.assertIn('still being processed', cancel.message.reply_text.await_args.args[0])
            self.assertEqual(menu.waiting[123], 'addbot')
            release.set(); await running
        self.assertNotIn(123, menu.waiting)
        self.assertIn('सेव हुआ', msg.reply_text.await_args.args[0])

    async def test_group_or_foreign_chat_cannot_start_token_prompt(self):
        for mode in ('group', 'foreign'):
            q = query('addbot')
            if mode == 'group': q.message.chat.type.value = 'supergroup'
            else: q.message.chat.id = 456
            await menu.menu_callback(None, q)
            self.assertTrue(q.answer.await_args.kwargs['show_alert'])
            self.assertNotIn(123, menu.waiting)

    async def test_encryption_failure_never_logs_secret_or_writes(self):
        with patch('utils.encrypt.ecs', side_effect=RuntimeError(TOKEN)), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock) as update, self.assertLogs(func.logger, level='ERROR') as logs:
            self.assertFalse(await func.save_user_bot(123, TOKEN))
        update.assert_not_awaited()
        self.assertNotIn(TOKEN, ' '.join(logs.output))

    async def test_active_task_or_login_blocks_save_but_preserves_cancel(self):
        for condition in ('task', 'login'):
            with self.subTest(condition=condition):
                menu.waiting.clear(); menu.input_languages.clear(); batch.ACTIVE_USERS.clear(); user_steps.clear()
                await self.open_prompt(); msg = input_message()
                if condition == 'task': batch.ACTIVE_USERS['123'] = {'kind': 'single'}
                else: user_steps[123] = login.STEP_CODE
                with patch.object(menu, 'save_user_bot', new_callable=AsyncMock) as save, patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop:
                    await menu.menu_input(None, msg)
                save.assert_not_awaited(); stop.assert_not_awaited(); msg.delete.assert_awaited_once()
                self.assertEqual(menu.waiting[123], 'addbot')
                self.assertIn('टास्क', msg.reply_text.await_args.args[0])

    async def test_navigate_and_failed_prompt_discard_locale(self):
        await self.open_prompt(); await menu.menu_callback(None, query('home'))
        self.assertNotIn(123, menu.waiting); self.assertNotIn(123, menu.input_languages)
        q = query('addbot'); q.message.reply_text.side_effect = RuntimeError('send failure')
        with self.assertRaises(RuntimeError): await menu.menu_callback(None, q)
        self.assertNotIn(123, menu.waiting); self.assertNotIn(123, menu.input_languages)

    async def test_legacy_command_failed_deletion_warns_without_token(self):
        msg = SimpleNamespace(from_user=SimpleNamespace(id=123), text='/setbot ' + TOKEN,
                              delete=AsyncMock(side_effect=RuntimeError(TOKEN)), reply=AsyncMock())
        with self.assertLogs(login.logger, level='WARNING') as logs:
            await login.bot_command_redirect(None, msg)
        output = ' '.join(str(call.args[0]) for call in msg.reply.await_args_list)
        self.assertIn('स्वयं हटाएँ', output); self.assertIn('BotFather', output)
        self.assertNotIn(TOKEN, output + ' '.join(logs.output))

    async def test_database_write_is_encrypted_acknowledged_and_error_is_sanitized(self):
        results = [SimpleNamespace(acknowledged=True, matched_count=1, upserted_id=None),
                   SimpleNamespace(acknowledged=True, matched_count=0, upserted_id='new'),
                   SimpleNamespace(acknowledged=False),
                   SimpleNamespace(acknowledged=True, matched_count=0, upserted_id=None),
                   RuntimeError(TOKEN)]
        for i, result in enumerate(results):
            with self.subTest(i=i), patch('utils.encrypt.ecs', return_value='cipher'), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, **({'side_effect': result} if isinstance(result, Exception) else {'return_value': result})) as update:
                with patch.object(func.logger, 'error') as log:
                    saved = await func.save_user_bot(123, TOKEN)
                self.assertEqual(saved, i < 2)
                data = update.await_args.args[1]['$set']
                self.assertEqual(data['bot_token'], 'enc:v1:cipher')
                self.assertNotIn(TOKEN, str(data) + str(log.call_args_list))
