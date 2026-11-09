"""Offline tests: no Telegram, payment provider or MongoDB access required."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('API_ID', '12345')
os.environ.setdefault('API_HASH', 'test-api-hash')
os.environ.setdefault('BOT_TOKEN', '12345:test-token')
os.environ.setdefault('MONGO_DB', 'mongodb://localhost:27017')

from plugins import menu, pay


class MenuTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        language_patch = patch.object(menu.localization, 'language_for', new_callable=AsyncMock, return_value='en')
        language_patch.start()
        self.addCleanup(language_patch.stop)
        menu.waiting.clear()
        self.msg = SimpleNamespace(
            chat=SimpleNamespace(type=SimpleNamespace(value='private'), id=123),
            reply_text=AsyncMock(), edit_text=AsyncMock(),
        )
        self.q = SimpleNamespace(message=self.msg, from_user=SimpleNamespace(id=123), data='', answer=AsyncMock())

    async def test_every_page_has_navigation_and_html(self):
        for name in menu.PAGES:
            with self.subTest(name=name):
                await menu.render(self.msg, name)
                args, kwargs = self.msg.reply_text.call_args
                self.assertIn('<blockquote>', args[0])
                self.assertTrue(kwargs['reply_markup'].inline_keyboard)

    async def test_single_uses_clicking_user_not_bot_authored_message(self):
        self.q.data = 'ui:single'
        with patch.object(menu.batch, 'begin_processing', new_callable=AsyncMock) as begin, patch.object(menu, 'get_user_step', return_value=None):
            await menu.menu_callback(None, self.q)
        begin.assert_awaited_once_with(None, self.msg, 123, 'single')

    async def test_input_and_cancel(self):
        self.q.data = 'ui:caption'
        with patch.object(menu, 'get_user_step', return_value=None):
            await menu.menu_callback(None, self.q)
        self.assertEqual(menu.waiting[123], 'caption')
        self.q.data = 'ui:cancel'
        await menu.menu_callback(None, self.q)
        self.assertNotIn(123, menu.waiting)

    async def test_group_callback_is_denied(self):
        self.msg.chat.type.value = 'supergroup'
        self.q.data = 'ui:home'
        await menu.menu_callback(None, self.q)
        self.msg.edit_text.assert_not_awaited()
        self.q.answer.assert_awaited_once()

    async def test_owner_controls_reject_non_owner(self):
        self.q.data = 'ui:owner'
        with patch.object(menu, 'OWNER_ID', [999]):
            await menu.menu_callback(None, self.q)
        self.msg.edit_text.assert_not_awaited()
        self.assertTrue(self.q.answer.call_args.kwargs['show_alert'])

    async def test_pause_controls_are_per_user(self):
        self.q.data = 'ui:pause'
        with patch.object(menu.batch, 'set_batch_paused', new_callable=AsyncMock, return_value=True) as pause:
            await menu.menu_callback(None, self.q)
        pause.assert_awaited_once_with(123, True)

    async def test_task_status_shows_paused_resume_even_when_last_item_finished(self):
        from plugins import batch_queue
        self.q.data = 'ui:task'
        data = {'status': 'paused', 'pending': [], 'failed_ids': [], 'current': 1, 'total': 1}
        with patch.object(batch_queue, 'get_job', new_callable=AsyncMock, return_value=data), patch.object(menu.batch, 'is_user_active', return_value=True):
            await menu.menu_callback(None, self.q)
        buttons = self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        callbacks = [b.callback_data for row in buttons for b in row]
        self.assertIn('ui:resume', callbacks)
        self.assertNotIn('ui:pause', callbacks)

    async def test_resume_does_not_claim_running_batch_was_paused(self):
        self.q.data = 'ui:resume'
        with patch.object(menu.batch, 'is_user_active', return_value=True), patch.object(menu.batch, 'get_batch_info', return_value={'paused': False}), patch.object(menu.batch, 'set_batch_paused', new_callable=AsyncMock) as resume:
            await menu.menu_callback(None, self.q)
        resume.assert_not_awaited()
        self.assertIn('already running', self.msg.edit_text.await_args.args[0])

    async def test_task_status_distinguishes_processing_from_interruption(self):
        from plugins import batch_queue
        self.q.data = 'ui:task'
        data = {'status': 'running', 'pending': [10], 'inflight': 10}
        with patch.object(batch_queue, 'get_job', new_callable=AsyncMock, return_value=data), patch.object(menu.batch, 'is_user_active', return_value=True):
            await menu.menu_callback(None, self.q)
        text = self.msg.edit_text.await_args.args[0]
        self.assertIn('being processed', text)
        self.assertNotIn('unknown delivery result', text)

    async def test_uncertain_resume_offers_skip_or_retry(self):
        from plugins import batch_queue
        self.q.data = 'ui:resume'
        with patch.object(menu.batch, 'is_user_active', return_value=False), patch.object(batch_queue, 'resume', new_callable=AsyncMock, return_value=(False, 'uncertain')), patch.object(batch_queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': 'a' * 24, 'inflight': 42}):
            await menu.menu_callback(None, self.q)
        text = self.msg.edit_text.call_args.args[0]
        self.assertIn('duplicate', text.lower())
        buttons = self.msg.edit_text.call_args.kwargs['reply_markup'].inline_keyboard
        actions = [b.callback_data for row in buttons for b in row]
        self.assertIn('ui:ri:s:' + 'a' * 24 + ':42:legacy', actions)
        self.assertIn('ui:ri:r:' + 'a' * 24 + ':42:legacy', actions)

    async def test_retry_warns_before_running(self):
        from plugins import batch_queue
        self.q.data = 'ui:retry_failed'
        with patch.object(batch_queue, 'retry_failed', new_callable=AsyncMock) as retry, patch.object(batch_queue, 'get_job', new_callable=AsyncMock, return_value={'job_id': 'a' * 24, 'failed_ids': [42]}):
            await menu.menu_callback(None, self.q)
        retry.assert_not_awaited()
        self.assertIn('duplicate', self.msg.edit_text.call_args.args[0].lower())

    async def test_uncertain_buttons_bind_item_attempt_and_fit_telegram_limit(self):
        from plugins import batch_queue
        self.q.data = 'ui:resume'
        entry = {'job_id': 'a' * 24, 'inflight': 2147483647, 'inflight_nonce': 'b' * 16}
        with patch.object(menu.batch, 'is_user_active', return_value=False), patch.object(batch_queue, 'resume', new_callable=AsyncMock, return_value=(False, 'uncertain')), patch.object(batch_queue, 'get_job', new_callable=AsyncMock, return_value=entry):
            await menu.menu_callback(None, self.q)
        buttons = self.msg.edit_text.await_args.kwargs['reply_markup'].inline_keyboard
        callbacks = [b.callback_data for row in buttons for b in row]
        selected = next(c for c in callbacks if c.startswith('ui:ri:r:'))
        self.assertIn('b' * 16, selected)
        self.assertLessEqual(len(selected.encode()), 64)
        self.q.data = selected
        with patch.object(batch_queue, 'resume', new_callable=AsyncMock, return_value=(False, 'stale')) as resume:
            await menu.menu_callback(None, self.q)
        resume.assert_awaited_once_with(123, self.msg, 'retry', 'a' * 24, 2147483647, 'b' * 16)

    async def test_old_uncertain_buttons_do_not_resume(self):
        from plugins import batch_queue
        self.q.data = 'ui:resume_retry_inflight'
        with patch.object(batch_queue, 'resume', new_callable=AsyncMock) as resume:
            await menu.menu_callback(None, self.q)
        resume.assert_not_awaited()
        self.assertIn('expired', self.msg.edit_text.await_args.args[0])

    async def test_old_failed_retry_button_does_not_launch(self):
        from plugins import batch_queue
        self.q.data = 'ui:retry_failed_confirm'
        with patch.object(batch_queue, 'retry_failed', new_callable=AsyncMock) as retry:
            await menu.menu_callback(None, self.q)
        retry.assert_not_awaited()
        self.assertIn('expired', self.msg.edit_text.await_args.args[0])

    async def test_other_users_private_message_is_denied(self):
        self.msg.chat.id = 456
        self.q.data = 'ui:task'
        await menu.menu_callback(None, self.q)
        self.msg.edit_text.assert_not_awaited()
        self.assertTrue(self.q.answer.call_args.kwargs['show_alert'])

    async def test_cancel_input_does_not_stop_existing_batch(self):
        self.q.data = 'ui:cancel'
        with patch.object(menu.batch, 'is_user_active', return_value=True), patch.object(menu.batch, 'request_batch_cancel', new_callable=AsyncMock) as stop:
            await menu.menu_callback(None, self.q)
        stop.assert_not_awaited()

    async def test_deleting_data_removes_saved_jobs_and_history(self):
        from plugins import batch_queue, mirror, batch_history, settings_clear, gift
        from utils import func
        batch_history.confirmations[123] = ('a' * 16, 9999999999)
        settings_clear.confirmations[123] = ('b' * 16, 'caption', 'value', 9999999999)
        gift.pending[123] = {'plan': 'd'}
        self.q.data = 'ui:delete_data'
        self.msg.id = 100
        menu.delete_confirmations[123] = ('a' * 16, __import__('time').monotonic() + 60, 100)
        self.q.data = 'ui:delete_data_yes:' + 'a' * 16
        with patch.object(menu.batch, 'is_user_active', return_value=False), patch.object(menu, 'get_user_step', return_value=None), patch.object(mirror, 'stop_mirror', new_callable=AsyncMock), patch.object(mirror.mirrors, 'delete_one', new_callable=AsyncMock), patch.object(batch_queue.jobs, 'delete_one', new_callable=AsyncMock) as delete_job, patch.object(batch_queue.history, 'delete_many', new_callable=AsyncMock) as delete_history, patch.object(func.users_collection, 'delete_many', new_callable=AsyncMock), patch.object(func.users_collection, 'find_one', new_callable=AsyncMock, return_value={}):
            await menu.menu_callback(None, self.q)
        delete_job.assert_awaited_once_with({'_id': 123})
        delete_history.assert_awaited_once_with({'user_id': 123})
        self.assertNotIn(123, batch_history.confirmations)
        self.assertNotIn(123, settings_clear.confirmations)
        self.assertNotIn(123, gift.pending)

    async def test_custom_bot_replacement_rejected_during_batch(self):
        uid = 123
        menu.waiting[uid] = 'addbot'
        message = SimpleNamespace(from_user=SimpleNamespace(id=uid), text='12345:' + 'a' * 25,
                                  photo=None, delete=AsyncMock(), reply_text=AsyncMock(), stop_propagation=MagicMock())
        with patch.object(menu.batch, 'is_user_active', return_value=True), patch.object(menu, 'save_user_bot', new_callable=AsyncMock) as save:
            await menu.menu_input(None, message)
        save.assert_not_awaited()
        self.assertIn('Stop your active task', message.reply_text.await_args.args[0])

    async def test_login_rejected_during_batch(self):
        self.q.data = 'ui:login'
        with patch.object(menu.batch, 'is_user_active', return_value=True), patch.object(menu.login, 'begin_login', new_callable=AsyncMock) as login:
            await menu.menu_callback(None, self.q)
        login.assert_not_awaited()

    async def test_gift_and_razorpay_are_not_falsely_advertised(self):
        for action in ('gift', 'razorpay'):
            self.q.data = f'ui:{action}'
            await menu.menu_callback(None, self.q)
            self.assertIn('not configured', self.msg.edit_text.call_args.args[0].lower())


class PaymentTests(unittest.IsolatedAsyncioTestCase):
    def test_invoice_validation_checks_user_currency_and_price(self):
        inv = {'user_id': 123, 'amount': 5}
        self.assertTrue(pay.matches_invoice(inv, 123, 'XTR', 5))
        self.assertFalse(pay.matches_invoice(inv, 124, 'XTR', 5))
        self.assertFalse(pay.matches_invoice(inv, 123, 'USD', 5))
        self.assertFalse(pay.matches_invoice(inv, 123, 'XTR', 1))
        self.assertFalse(pay.matches_invoice(None, 123, 'XTR', 5))

    async def test_precheckout_rejects_unknown_invoice(self):
        q = SimpleNamespace(payload='unknown', from_user=SimpleNamespace(id=123), currency='XTR', total_amount=5, answer=AsyncMock())
        with patch.object(pay.invoices, 'find_one', new_callable=AsyncMock, return_value=None):
            await pay.pre_checkout(None, q)
        self.assertFalse(q.answer.call_args.kwargs['ok'])

    async def test_grant_filters_by_charge_id_and_extends_atomically(self):
        invoice = {'user_id': 123, 'days': 7}
        with patch.object(pay.premium_users_collection, 'update_one', new_callable=AsyncMock) as update, patch.object(pay.premium_users_collection, 'find_one', new_callable=AsyncMock, return_value={'user_id': 123}), patch.object(pay.premium_users_collection, 'index_information', new_callable=AsyncMock, return_value={'_id_': {}}):
            await pay.grant_invoice(invoice, 'charge-123')
        selector, pipeline = update.call_args.args
        self.assertEqual(selector, {'user_id': 123, 'applied_payments': {'$ne': 'charge-123'}})
        self.assertIn('$max', pipeline[0]['$set']['subscription_end']['$add'][0])
        self.assertTrue(update.call_args.kwargs['upsert'])

    async def test_completed_payment_is_not_granted_again(self):
        invoice = {'user_id': 123, 'amount': 5, 'status': 'completed', 'charge_id': 'abc'}
        payment = SimpleNamespace(payload='payload', currency='XTR', total_amount=5, telegram_payment_charge_id='abc')
        message = SimpleNamespace(from_user=SimpleNamespace(id=123), successful_payment=payment, reply_text=AsyncMock())
        with patch.object(pay.invoices, 'find_one', new_callable=AsyncMock, return_value=invoice), patch.object(pay, 'grant_invoice', new_callable=AsyncMock) as grant:
            await pay.successful_payment(None, message)
        grant.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
