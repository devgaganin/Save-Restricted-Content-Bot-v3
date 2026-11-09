"""Offline tests for credential flow, safe failures and retired settings buttons."""
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from pyrogram.errors import BadRequest, PhoneCodeExpired, PhoneCodeInvalid, SessionPasswordNeeded
from telethon import events
from plugins import batch_queue, login, menu, settings, payment_review
from utils import func
from utils.custom_filters import get_user_step, set_user_step, user_steps


def message(text):
    return SimpleNamespace(from_user=SimpleNamespace(id=123), text=text,
                           delete=AsyncMock(), reply=AsyncMock())


def temp_client():
    return SimpleNamespace(connect=AsyncMock(), send_code=AsyncMock(return_value=SimpleNamespace(phone_code_hash='hash')),
                           sign_in=AsyncMock(), check_password=AsyncMock(), disconnect=AsyncMock(),
                           export_session_string=AsyncMock(return_value='session-secret'), log_out=AsyncMock())


class LoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        user_steps.clear()
        login.login_cache.clear()
        batch_queue.locks.clear()
        self.status = SimpleNamespace(edit=AsyncMock())

    async def start(self, language='hi'):
        msg = SimpleNamespace(reply=AsyncMock(return_value=self.status))
        await login.begin_login(msg, 123, language)
        return msg

    async def send(self, value):
        msg = message(value)
        await login.handle_login_steps(None, msg)
        msg.delete.assert_awaited_once()
        return msg

    async def test_hindi_phone_code_2fa_success_and_language_snapshot(self):
        temp = temp_client()
        temp.sign_in.side_effect = SessionPasswordNeeded()
        await self.start()
        with patch.object(login, 'Client', return_value=temp), patch.object(login, 'ecs', return_value='encrypted'), patch.object(login, 'save_user_session', new_callable=AsyncMock, return_value=True) as save:
            await self.send('+12345678900')
            self.assertIn('वेरिफ़िकेशन', self.status.edit.await_args.args[0])
            await self.send('1 2 3 4 5')
            self.assertEqual(get_user_step(123), login.STEP_PASSWORD)
            self.assertIn('पासवर्ड', self.status.edit.await_args.args[0])
            await self.send('private-password')
        temp.sign_in.assert_awaited_once_with('+12345678900', 'hash', '12345')
        save.assert_awaited_once_with(123, 'encrypted')
        self.assertIn('सफल', self.status.edit.await_args.args[0])
        self.assertIsNone(get_user_step(123))
        self.assertNotIn(123, login.login_cache)
        temp.disconnect.assert_awaited_once()

    async def test_english_code_login_succeeds_and_cancel_button_is_available(self):
        temp = temp_client()
        prompt = await self.start('en')
        keyboard = prompt.reply.await_args.kwargs['reply_markup']
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, 'ui:cancel')
        with patch.object(login, 'Client', return_value=temp), patch.object(login, 'ecs', return_value='cipher'), patch.object(login, 'save_user_session', new_callable=AsyncMock, return_value=True):
            await self.send('+12345678900')
            self.assertEqual(self.status.edit.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data, 'ui:cancel')
            await self.send('12345')
        self.assertIn('successfully', self.status.edit.await_args.args[0])
        self.assertIsNone(self.status.edit.await_args.kwargs['reply_markup'])
        self.assertIsNone(get_user_step(123))

    async def test_phone_request_failure_does_not_expose_telegram_error(self):
        temp = temp_client()
        temp.send_code.side_effect = BadRequest('sensitive-provider-response')
        await self.start('hi')
        with patch.object(login, 'Client', return_value=temp):
            await self.send('+12345678900')
        self.assertIn('नहीं भेजा', self.status.edit.await_args.args[0])
        self.assertNotIn('sensitive-provider-response', str(self.status.edit.await_args))
        self.assertIsNone(get_user_step(123))
        temp.disconnect.assert_awaited_once()

    async def test_invalid_and_expired_codes_clear_state_even_on_disconnect_failure(self):
        for error in (PhoneCodeInvalid(), PhoneCodeExpired()):
            for lang in ('en', 'hi'):
                with self.subTest(error=type(error).__name__, lang=lang):
                    user_steps.clear(); login.login_cache.clear()
                    temp = temp_client()
                    temp.sign_in.side_effect = error
                    temp.disconnect.side_effect = RuntimeError('secret-error')
                    await self.start(lang)
                    with patch.object(login, 'Client', return_value=temp):
                        await self.send('+12345678900')
                        await self.send('12345')
                    self.assertIn('expired' if lang == 'en' else 'समाप्त', self.status.edit.await_args.args[0])
                    self.assertIsNone(get_user_step(123))
                    self.assertNotIn(123, login.login_cache)

    async def test_generic_password_failure_is_not_claimed_wrong_and_does_not_leak(self):
        await self.start('en')
        temp = temp_client()
        temp.check_password.side_effect = BadRequest('secret-error')
        login.login_cache[123]['temp_client'] = temp
        set_user_step(123, login.STEP_PASSWORD)
        with patch.object(login.logger, 'error') as log:
            await self.send('my-private-password')
        self.assertEqual(get_user_step(123), login.STEP_PASSWORD)
        out = str(self.status.edit.await_args)
        self.assertIn('could not be completed', out)
        self.assertNotIn('wrong', out.lower())
        self.assertNotIn('secret-error', out + str(log.call_args_list))
        self.assertNotIn('my-private-password', out + str(log.call_args_list))

    async def test_storage_failure_warns_devices_and_revocation_is_best_effort(self):
        for lang in ('en', 'hi'):
            with self.subTest(lang=lang):
                user_steps.clear(); login.login_cache.clear()
                await self.start(lang)
                temp = temp_client()
                temp.log_out.side_effect = RuntimeError('private-session')
                temp.disconnect.side_effect = RuntimeError('private-disconnect')
                with patch.object(login, 'ecs', return_value='cipher'), patch.object(login, 'save_user_session', new_callable=AsyncMock, return_value=False), self.assertLogs(login.logger, level='ERROR') as logs:
                    result = await login.store_authorized_session(123, temp, self.status)
                self.assertFalse(result)
                temp.log_out.assert_awaited_once()
                temp.disconnect.assert_awaited_once()
                self.assertIn('Devices', self.status.edit.await_args.args[0])
                self.assertNotIn('private-session', ' '.join(logs.output))
                self.assertIsNone(get_user_step(123))
                self.assertNotIn(123, login.login_cache)

    async def test_input_deletion_failure_warns_user_and_english_fallback(self):
        msg = await self.start('invalid')
        self.assertIn('phone number', msg.reply.await_args.args[0])
        incoming = message('not-a-phone')
        incoming.delete.side_effect = RuntimeError('secret-error')
        await login.handle_login_steps(None, incoming)
        self.assertIn('delete', incoming.reply.await_args.args[0])
        self.assertIn('valid phone', self.status.edit.await_args.args[0])
        self.assertEqual(get_user_step(123), login.STEP_PHONE)

    async def test_generic_failure_is_sanitized(self):
        await self.start('hi')
        temp = temp_client()
        temp.connect.side_effect = RuntimeError('phone-or-otp-secret')
        with patch.object(login, 'Client', return_value=temp), self.assertLogs(login.logger, level='ERROR') as logs:
            await self.send('+12345678900')
        self.assertIn('Devices', self.status.edit.await_args.args[0])
        self.assertNotIn('phone-or-otp-secret', str(self.status.edit.await_args) + ' '.join(logs.output))
        self.assertIsNone(get_user_step(123))

    async def test_second_begin_does_not_replace_active_flow(self):
        await self.start()
        saved = login.login_cache[123]
        with self.assertRaises(RuntimeError):
            await self.start('en')
        self.assertIs(login.login_cache[123], saved)
        self.assertEqual(get_user_step(123), login.STEP_PHONE)

    async def test_database_failure_log_never_contains_exception_message(self):
        with patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, side_effect=RuntimeError('encrypted-secret')), self.assertLogs(func.logger, level='ERROR') as logs:
            self.assertFalse(await func.save_user_session(123, 'encrypted-secret'))
        self.assertNotIn('encrypted-secret', ' '.join(logs.output))

    async def test_menu_hindi_login_privacy_and_duplicate_guard(self):
        from plugins import mirror
        q = SimpleNamespace(from_user=SimpleNamespace(id=123), data='ui:login', answer=AsyncMock(),
                            message=SimpleNamespace(chat=SimpleNamespace(id=123, type=SimpleNamespace(value='private')), reply_text=AsyncMock()))
        with patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock) as stop, patch.object(login, 'begin_login', new_callable=AsyncMock) as begin:
            await menu.menu_callback(None, q)
            self.assertIn('ऑपरेटर', q.message.reply_text.await_args.args[0])
            self.assertIn('2FA', q.message.reply_text.await_args.args[0])
            begin.assert_awaited_once_with(q.message, 123, 'hi')
            set_user_step(123, login.STEP_PHONE)
            await menu.menu_callback(None, q)
            stop.assert_awaited_once()
            begin.assert_awaited_once()
            self.assertIn('पहले से', q.message.reply_text.await_args.args[0])

    async def test_hindi_logout_warns_if_revoke_failed(self):
        from plugins import mirror
        q = SimpleNamespace(from_user=SimpleNamespace(id=123), data='ui:logout_confirm', answer=AsyncMock(),
                            message=SimpleNamespace(id=100, chat=SimpleNamespace(id=123, type=SimpleNamespace(value='private')), edit_text=AsyncMock(), reply_text=AsyncMock()))
        temp = temp_client(); temp.log_out.side_effect = RuntimeError('sensitive')
        with patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='hi'), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={'session_string': 'cipher'}), patch.object(func.users_collection, 'update_one', new_callable=AsyncMock, return_value=SimpleNamespace(modified_count=1)), patch('utils.encrypt.dcs', return_value='secret'), patch('pyrogram.Client', return_value=temp):
            await menu.menu_callback(None, q)
            q.data = q.message.edit_text.await_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data
            await menu.menu_callback(None, q)
        self.assertIn('Devices', q.message.edit_text.await_args.args[0])
        self.assertIn('असफल', q.message.edit_text.await_args.args[0])


class RetiredSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_stale_callbacks_only_answer_disabled(self):
        for value in settings.LEGACY_CALLBACKS:
            event = SimpleNamespace(data=value, answer=AsyncMock(), respond=AsyncMock())
            await settings.callback_query_handler(event)
            event.answer.assert_awaited_once()
            self.assertTrue(event.answer.await_args.kwargs['alert'])
            event.respond.assert_not_awaited()

    async def test_group_settings_only_redirects_private(self):
        event = SimpleNamespace(is_private=False, respond=AsyncMock())
        await settings.settings_command(event)
        self.assertIn('private', event.respond.await_args.args[0])

    def test_no_broad_telethon_handlers(self):
        handlers = [(fn, builder) for fn, builder in settings.gf.list_event_handlers() if fn.__module__ == settings.__name__]
        self.assertEqual({fn.__name__ for fn, _ in handlers}, {'settings_command', 'callback_query_handler'})
        callbacks = [builder for _, builder in handlers if isinstance(builder, events.CallbackQuery)]
        self.assertEqual(len(callbacks), 1)
        pattern = callbacks[0].match
        for value in settings.LEGACY_CALLBACKS:
            self.assertIsNotNone(pattern(value))
            self.assertIsNone(pattern(value + b'extra'))
        self.assertIsNone(pattern(b'ui:login'))

    async def test_filename_customization_preserved(self):
        async def data(user, key, default):
            return {'delete_words': ['remove'], 'rename_tag': 'TAG', 'replacement_words': {'old': 'new'}}.get(key, default)
        with tempfile.TemporaryDirectory() as directory:
            name = os.path.join(directory, 'oldremove.mkv')
            with open(name, 'w') as f:
                f.write('data')
            with patch.object(settings, 'get_user_data_key', side_effect=data):
                result = await settings.rename_file(name, 123, None)
            self.assertEqual(result, os.path.join(directory, 'new TAG.mp4'))
            self.assertTrue(os.path.isfile(result))
